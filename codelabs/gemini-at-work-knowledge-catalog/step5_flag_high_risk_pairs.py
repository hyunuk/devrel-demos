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


class RiskFlagPlan(BaseModel):
    applied_high_risk_rule: str = Field(
        description="High-risk review rule read from Knowledge Catalog."
    )
    bigquery_sql: str = Field(
        description="Executable BigQuery Standard SQL query."
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
t_payers = f"`{PROJECT_ID}.{DATASET_ID}.dim_payers`"
t_payees = f"`{PROJECT_ID}.{DATASET_ID}.dim_payees`"
hr_expr = "SUM(CASE WHEN pr.payor_risk_score >= 0.70 THEN amount ELSE 0 END)"
pct_expr = f"ROUND(SAFE_DIVIDE({hr_expr}, SUM(amount)) * 100, 2)"
prompt = f"""
You are a BigQuery SQL generator grounded in Knowledge Catalog metadata.
Generate a BigQuery Standard SQL query that joins:
- {t_logs} (deduplicated)
- {t_payers} on `payor_id`
- {t_payees} on `payee_id`

Apply these exact Knowledge Catalog payment-rules aspect fields:
- deduplication_rule: {payment_rules['deduplication_rule']}
- valid_payment_filter: {payment_rules['valid_payment_filter']}
- high_risk_review_rule: {payment_rules['high_risk_review_rule']}

Requirements for `bigquery_sql`:
1. In a CTE `deduped_logs`, exclude blank `transaction_id` rows
   (`transaction_id IS NOT NULL AND TRIM(transaction_id) != ''`), compute
   `ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY timestamp ASC) AS rn`,
   and keep `rn = 1` with `status = 'COMPLETED' AND amount > 0`
   and `currency IN ('USD', 'EUR', 'GBP')`.
2. Join `dim_payers` (`pr`) on `payor_id` and `dim_payees` (`py`) on
   `payee_id`, and group by `py.payee_country, py.payee_category`.
3. Compute:
   - `payee_country`
   - `payee_category`
   - `ROUND(SUM(amount), 2) AS total_volume`
   - `ROUND({hr_expr}, 2) AS high_risk_volume`
   - `{pct_expr} AS high_risk_volume_pct`
   - `CASE WHEN {pct_expr} >= 4.00 THEN 'REVIEW_REQUIRED' ELSE 'NORMAL' END AS review_status`
4. Order by `total_volume DESC` and `LIMIT 6`.
Return raw SQL without markdown fences in `bigquery_sql`.
"""

response = genai_client.models.generate_content(
    model=model_id,
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RiskFlagPlan,
        temperature=0.0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    ),
)
plan = RiskFlagPlan.model_validate_json(response.text)

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
top6_rows = [
    {
        "payee_country": r.payee_country,
        "payee_category": r.payee_category,
        "total_volume": float(r.total_volume),
        "high_risk_volume": float(r.high_risk_volume),
        "high_risk_volume_pct": float(r.high_risk_volume_pct),
        "review_status": r.review_status,
    }
    for r in bq_client.query(plan.bigquery_sql).result()
]

flagged_top6 = [
    f"{r['payee_country']} {r['payee_category']}"
    for r in top6_rows
    if r["review_status"] == "REVIEW_REQUIRED"
]
print(json.dumps({
    "flagged_in_top_6": flagged_top6,
    "top_6_risk_summary": top6_rows,
}, indent=2))
assert flagged_top6 == ["US Financial", "US Retail"]
