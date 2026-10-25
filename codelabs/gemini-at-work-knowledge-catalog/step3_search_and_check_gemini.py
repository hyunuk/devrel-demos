import json
import os
import re
import time
from google import genai
from google.cloud import dataplex_v1

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.popen("gcloud config get-value project").read().strip()
)
if not PROJECT_ID or PROJECT_ID == "YOUR_PROJECT_ID":
    raise ValueError("PROJECT_ID must be set to a valid project ID.")

DATASET_ID = os.environ.get("DATASET_ID", "payment_analytics")
CATALOG_LOCATION = os.environ.get("CATALOG_LOCATION", "us")
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global")


def resolve_newest_flash_model(client: genai.Client) -> str:
    ga_candidates = []
    preview_candidates = []
    excluded = (
        "1.0", "1.5", "2.0", "2.5", "lite", "image",
        "audio", "tts", "live", "embedding", "computer-use",
    )
    for m in client.models.list():
        raw_name = getattr(m, "name", "") or ""
        short_id = raw_name.split("/")[-1]
        if not short_id.startswith("gemini-") or "flash" not in short_id:
            continue
        if any(tok in short_id for tok in excluded):
            continue
        ver_match = re.match(r"^gemini-(\d+)\.(\d+)-flash(.*)$", short_id)
        if not ver_match:
            continue
        major = int(ver_match.group(1))
        minor = int(ver_match.group(2))
        suffix = ver_match.group(3)
        if major < 3:
            continue
        if "preview" in suffix or "exp" in suffix:
            preview_candidates.append(((major, minor), short_id))
        else:
            ga_candidates.append(((major, minor), short_id))
    if ga_candidates:
        ga_candidates.sort(reverse=True)
        return ga_candidates[0][1]
    if preview_candidates:
        preview_candidates.sort(reverse=True)
        return preview_candidates[0][1]
    raise RuntimeError("No active Gemini 3.x+ Flash model found.")


catalog_client = dataplex_v1.CatalogServiceClient()
search_request = dataplex_v1.SearchEntriesRequest(
    name=f"projects/{PROJECT_ID}/locations/global",
    query=f"name:{DATASET_ID} system=BIGQUERY",
    page_size=10,
)
expected_tables = {"transaction_logs", "dim_payers", "dim_payees"}
found_tables = []
for _ in range(18):
    found_tables = sorted([
        res.dataplex_entry.fully_qualified_name.split(".")[-1]
        for res in catalog_client.search_entries(request=search_request)
        if DATASET_ID in res.dataplex_entry.fully_qualified_name
    ])
    if expected_tables.issubset(found_tables):
        break
    time.sleep(5)

entry_name = (
    f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}/"
    f"entryGroups/@bigquery/entries/bigquery.googleapis.com/"
    f"projects/{PROJECT_ID}/datasets/{DATASET_ID}/tables/transaction_logs"
)
ctx_resp = catalog_client.lookup_context(
    request={
        "name": f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}",
        "resources": [entry_name],
    }
)

genai_client = genai.Client(
    vertexai=True, project=PROJECT_ID, location=GEMINI_LOCATION
)
model_id = resolve_newest_flash_model(genai_client)

assert "transaction_logs" in found_tables
assert "dim_payers" in found_tables
assert "dim_payees" in found_tables
assert len(ctx_resp.context) > 0

print(json.dumps({
    "catalog_search_dataset": DATASET_ID,
    "discovered_tables": found_tables,
    "lookup_context_ready": bool(ctx_resp.context),
    "gemini_location": GEMINI_LOCATION,
    "resolved_flash_model_tier": "gemini-3.x-flash",
    "model_active": bool(model_id),
}, indent=2))
