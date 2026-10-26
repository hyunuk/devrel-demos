import json
import os
import re
from google import genai
from google.genai import types
from google.cloud import bigquery
from google.cloud import dataplex_v1
from pydantic import BaseModel, Field

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.popen("gcloud config get-value project").read().strip()
)
if not PROJECT_ID or PROJECT_ID == "YOUR_PROJECT_ID":
    raise ValueError("PROJECT_ID must be set to a valid project ID.")

DATASET_ID = os.environ.get("DATASET_ID", "payment_analytics")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")
CATALOG_LOCATION = os.environ.get("CATALOG_LOCATION", "us")
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global")
ASPECT_TYPE_ID = os.environ.get("ASPECT_TYPE_ID", "payment-rules")


class PaymentAggregationComparisonPlan(BaseModel):
    baseline_sql: str = Field(
        description=(
            "Baseline BigQuery Standard SQL query generated from physical "
            "table schemas only, without Knowledge Catalog rules."
        )
    )
    applied_deduplication_rule: str = Field(
        description="Deduplication rule read from Knowledge Catalog."
    )
    applied_valid_payment_filter: str = Field(
        description="Valid payment filter read from Knowledge Catalog."
    )
    governed_sql: str = Field(
        description=(
            "Governed BigQuery Standard SQL query applying the "
            "Knowledge Catalog payment-rules aspect."
        )
    )


def resolve_newest_flash_model(client: genai.Client) -> str:
    ga_candidates = []
    preview_candidates = []
    excluded = (
        "1.0", "1.5", "2.0", "2.5", "lite", "image",
        "audio", "tts", "live", "embedding", "computer-use",
    )
    for m in client.models.list():
        short_id = (getattr(m, "name", "") or "").split("/")[-1]
        if (
            not short_id.startswith("gemini-")
            or "flash" not in short_id
            or any(t in short_id for t in excluded)
        ):
            continue
        ver_match = re.match(r"^gemini-(\d+)\.(\d+)-flash(.*)$", short_id)
        if ver_match and int(ver_match.group(1)) >= 3:
            is_prev = (
                "preview" in ver_match.group(3)
                or "exp" in ver_match.group(3)
            )
            target_list = preview_candidates if is_prev else ga_candidates
            ver_tuple = (int(ver_match.group(1)), int(ver_match.group(2)))
            target_list.append((ver_tuple, short_id))
    for candidates in (ga_candidates, preview_candidates):
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
    raise RuntimeError("No active Gemini 3.x+ Flash model found.")


catalog_client = dataplex_v1.CatalogServiceClient()
location_parent = f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}"
entry_name = (
    f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}/"
    f"entryGroups/@bigquery/entries/bigquery.googleapis.com/"
    f"projects/{PROJECT_ID}/datasets/{DATASET_ID}/tables/transaction_logs"
)
entry = catalog_client.lookup_entry(
    request={
        "name": location_parent,
        "entry": entry_name,
        "view": dataplex_v1.EntryView.ALL,
    }
)

payment_rules = None
for k, aspect in entry.aspects.items():
    if k.endswith(f".{CATALOG_LOCATION}.{ASPECT_TYPE_ID}"):
        payment_rules = dict(aspect.data)
        break
if not payment_rules:
    raise RuntimeError("payment-rules aspect not found on transaction_logs")

genai_client = genai.Client(
    vertexai=True, project=PROJECT_ID, location=GEMINI_LOCATION
)
model_id = resolve_newest_flash_model(genai_client)

t_logs = f"`{PROJECT_ID}.{DATASET_ID}.transaction_logs`"
t_payees = f"`{PROJECT_ID}.{DATASET_ID}.dim_payees`"
prompt = f"""
You are comparing ungrounded vs. Knowledge Catalog-grounded BigQuery SQL
generation over {t_logs} joined with {t_payees} on `payee_id`.

Physical table schemas:
- {t_logs}: transaction_id STRING, payor_id STRING, payee_id STRING,
  amount FLOAT64, currency STRING, status STRING, timestamp TIMESTAMP
- {t_payees}: payee_id STRING, payee_country STRING, payee_category STRING

1. For `baseline_sql` (Without Knowledge Catalog):
   Generate a direct schema-only aggregation query joining {t_logs} and
   {t_payees} on `payee_id` without any Knowledge Catalog deduplication CTE.
2. For `governed_sql` (With Knowledge Catalog):
   Apply these Knowledge Catalog `payment-rules` aspect fields retrieved
   from {t_logs} before aggregating:
   - deduplication_rule: {payment_rules['deduplication_rule']}
   - valid_payment_filter: {payment_rules['valid_payment_filter']}

Output requirements for both queries:
- Group by `payee_country, payee_category`.
- Return columns `payee_country`, `payee_category`,
  `COUNT(*) AS valid_tx_count`, `ROUND(SUM(amount), 2) AS total_volume`,
  and `ROUND(AVG(amount), 2) AS avg_payment_size`.
- Order by `total_volume DESC` and `LIMIT 6`.
- Return raw BigQuery Standard SQL without markdown fences.
"""

response = genai_client.models.generate_content(
    model=model_id,
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=PaymentAggregationComparisonPlan,
        temperature=0.0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    ),
)
plan = PaymentAggregationComparisonPlan.model_validate_json(response.text)

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
baseline_rows = [
    {
        "payee_country": r.payee_country,
        "payee_category": r.payee_category,
        "raw_tx_count": int(r.valid_tx_count),
        "raw_volume": float(r.total_volume),
        "raw_avg_size": float(r.avg_payment_size),
    }
    for r in bq_client.query(plan.baseline_sql).result()
]
governed_rows = [
    {
        "payee_country": r.payee_country,
        "payee_category": r.payee_category,
        "valid_tx_count": int(r.valid_tx_count),
        "total_volume": float(r.total_volume),
        "avg_payment_size": float(r.avg_payment_size),
    }
    for r in bq_client.query(plan.governed_sql).result()
]

b_top = baseline_rows[0]
g_top = governed_rows[0]
overstated_tx = b_top["raw_tx_count"] - g_top["valid_tx_count"]
overstated_usd = round(b_top["raw_volume"] - g_top["total_volume"], 2)

print(json.dumps({
    "catalog_rule_applied": True,
    "us_healthcare_without_catalog": b_top,
    "us_healthcare_with_catalog": g_top,
    "us_healthcare_overstated_tx_removed": overstated_tx,
    "us_healthcare_overstated_usd_removed": overstated_usd,
    "top_6_country_category_pairs": governed_rows,
}, indent=2))

assert b_top["raw_tx_count"] == 93155
assert b_top["raw_volume"] == 85045494.88
assert g_top["payee_country"] == "US"
assert g_top["payee_category"] == "Healthcare"
assert g_top["valid_tx_count"] == 91762
assert g_top["total_volume"] == 83759597.75
assert overstated_tx == 1393
assert overstated_usd == 1285897.13
