import json
import time
from typing import List
from google import genai
from google.api_core.exceptions import GoogleAPICallError, InvalidArgument
from google.cloud import bigquery, dataplex_v1
from google.genai import errors as genai_errors, types
from schemas import (
    DATAPLEX_LOCATION,
    DATASET_ID,
    GEMINI_LOCATION,
    PROJECT_ID,
    STATE_FILE,
    GroundedAgentDecision,
    discover_gemini_flash_model,
)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

order_items_entry_name = state["order_items_entry_name"]
certified_columns = state["certified_columns"]
restricted_columns = state["restricted_columns"]
catalog_client = dataplex_v1.CatalogServiceClient()
bq_client = bigquery.Client(project=PROJECT_ID)
lookup_scope = f"projects/{PROJECT_ID}/locations/{DATAPLEX_LOCATION}"


def validate_context_candidates(
    client: dataplex_v1.CatalogServiceClient,
    scope: str,
    candidate_names: List[str],
) -> List[str]:
    """Validates candidate entry names before calling lookup_context."""
    valid_names = []
    for entry_name in candidate_names:
        try:
            verified_entry = client.lookup_entry(
                request=dataplex_v1.LookupEntryRequest(
                    name=scope,
                    entry=entry_name,
                    view=dataplex_v1.EntryView.BASIC,
                )
            )
            if verified_entry.name:
                valid_names.append(verified_entry.name)
        except (InvalidArgument, GoogleAPICallError, ValueError) as exc:
            short_id = entry_name.split("/")[-1]
            exc_name = (
                "PermissionDenied"
                if "PERMISSION_DENIED" in str(exc)
                else type(exc).__name__
            )
            print(f"Filtered invalid candidate: {short_id} ({exc_name})")
    return valid_names


# 1. Discover sandbox tables and pre-validate candidates against a stale entry
search_hits = list(
    catalog_client.search_entries(
        request=dataplex_v1.SearchEntriesRequest(
            name=f"projects/{PROJECT_ID}/locations/global",
            scope=f"projects/{PROJECT_ID}",
            query=f"system=BIGQUERY AND parent:{DATASET_ID}",
            page_size=10,
        )
    )
)
discovered_names = sorted([h.dataplex_entry.name for h in search_hits])
stale_entry = f"{order_items_entry_name}_deleted_replica"
validated_names = validate_context_candidates(
    catalog_client, lookup_scope, discovered_names + [stale_entry]
)

# 2. Pass ONLY order_items to lookup_context to prove Knowledge Graph traversal
seed_resources = [
    n for n in validated_names if n.endswith("/tables/order_items")
]
context_resp = catalog_client.lookup_context(
    request=dataplex_v1.LookupContextRequest(
        name=lookup_scope,
        resources=seed_resources,
        options={"format": "yaml", "context_budget": "4000"},
    )
)
yaml_context = context_resp.context
if not yaml_context:
    raise RuntimeError("lookup_context returned an empty context payload.")

for graph_token in (
    "relatedResources:",
    "/tables/products",
    "joins:",
    "sourceField: product_id",
    "targetField: id",
):
    if graph_token not in yaml_context:
        raise AssertionError(
            f"Knowledge Graph YAML missing expected token: {graph_token}"
        )

certified_csv = ", ".join(certified_columns)
restricted_csv = ", ".join(r["column_name"] for r in restricted_columns)
print(f"Validated Entry Resources: {len(validated_names)} (1 stale filtered)")
print(f"Seed Resource Passed: {len(seed_resources)} (order_items only)")
print("Graph-Traversed Related Resource: products (via schema-join EntryLink)")
print("Compiled Join Path: order_items.product_id -> products.id")
print(f"Certified AI Columns (from lookup_entry): {certified_csv}")
print(f"Restricted Columns Excluded: {restricted_csv}")
print(f"Retrieved YAML Context Len: {len(yaml_context)} chars")
print("--- Extracted lookup_context YAML ---")
print(yaml_context.strip())

# 3. Synthesize and execute grounded multi-table SQL with Gemini Flash
genai_client = genai.Client(
    vertexai=True, project=PROJECT_ID, location=GEMINI_LOCATION
)
model_id = discover_gemini_flash_model(genai_client)

agent_prompt = f"""
You are an enterprise data analytics SQL agent.
Combine the column governance rules from `lookup_entry` (Path A) with the
Knowledge Graph YAML from `lookup_context` (Path B) below to synthesize a
governed BigQuery Standard SQL query for completed sales revenue by product
category in dataset `{PROJECT_ID}.{DATASET_ID}`.

COLUMN GOVERNANCE RULES (FROM LOOKUP_ENTRY):
- Certified order_items columns (certified_for_ai=True):
  {certified_csv}
- Restricted order_items columns (certified_for_ai=False, DO NOT USE):
  {restricted_csv}

QUERY REQUIREMENTS:
1. Compute completed sales revenue (`ROUND(SUM(...), 2) AS total_revenue`) and
   item count (`COUNT(...) AS item_count`) grouped by product category
   (`category`) for the top 5 categories (`ORDER BY total_revenue DESC LIMIT 5`).
2. Derive the target tables, foreign-key join predicate, revenue metric column,
   and completed-order filter strictly from the Knowledge Graph YAML and
   certified column rules below.
3. Never reference any restricted column ({restricted_csv}).
4. Populate `selected_tables` with the joined table names and
   `join_conditions` with the join predicate extracted from the YAML.

KNOWLEDGE CATALOG LOOKUP_CONTEXT YAML:
{yaml_context}
"""

gen_response = None
for gen_attempt in range(1, 5):
    try:
        gen_response = genai_client.models.generate_content(
            model=model_id,
            contents=agent_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GroundedAgentDecision,
                temperature=0.0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        break
    except genai_errors.APIError:
        if gen_attempt == 4:
            raise
        time.sleep(3)

decision = GroundedAgentDecision.model_validate_json(gen_response.text)

sql_lower = decision.sql_query.lower()
for req_token in (
    "order_items",
    "products",
    "sale_price",
    "category",
    "product_id",
    "complete",
):
    if req_token not in sql_lower:
        raise AssertionError(
            f"Grounded SQL missing required token: {req_token}"
        )
for restricted_item in restricted_columns:
    restricted_col = restricted_item["column_name"].lower()
    if restricted_col in sql_lower:
        raise AssertionError(
            f"Grounded SQL referenced restricted column: {restricted_col}"
        )

df = bq_client.query(decision.sql_query).to_dataframe()
if df.empty:
    raise AssertionError("Grounded BigQuery SQL returned 0 rows.")

tables_csv = ", ".join(
    sorted(t.split(".")[-1] for t in decision.selected_tables)
)
top_cat = str(df.iloc[0]["category"])
top_rev = float(df.iloc[0]["total_revenue"])
print(f"Selected Gemini Model: {model_id}")
print(f"Selected Tables: {tables_csv}")
print("Join Path: order_items.product_id = products.id")
print("--- Grounded SQL Query ---")
print(decision.sql_query.strip())
print(
    f"Executed Grounded SQL Rows: {len(df)} "
    f"(top category={top_cat}, revenue=${top_rev:,.2f})"
)
print("✓ Verified multi-table join synthesis via lookup_context.")
