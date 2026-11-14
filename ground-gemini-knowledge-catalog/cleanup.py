"""Delete Knowledge Catalog AspectType and BigQuery dataset in reverse order."""

import os
from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from google.cloud import dataplex_v1

PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
if not PROJECT_ID or "your-project" in PROJECT_ID.lower():
    raise ValueError(
        "Set PROJECT_ID to a valid Google Cloud project ID before running."
    )

BQ_LOCATION = os.environ.get("BQ_LOCATION", "US").strip()
KC_LOCATION = os.environ.get("KC_LOCATION", "us").strip().lower()
DATASET_ID = os.environ.get("DATASET_ID", "charlies_coffee_kc").strip()
ASPECT_TYPE_ID = "business-metric-contract"

catalog_client = dataplex_v1.CatalogServiceClient()
aspect_type_name = (
    f"projects/{PROJECT_ID}/locations/{KC_LOCATION}/aspectTypes/{ASPECT_TYPE_ID}"
)

try:
    delete_op = catalog_client.delete_aspect_type(name=aspect_type_name)
    delete_op.result()
    print(f"Deleted AspectType: {aspect_type_name}")
except gcp_exceptions.NotFound:
    print(f"AspectType already absent: {aspect_type_name}")

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
dataset_full_id = f"{PROJECT_ID}.{DATASET_ID}"
bq_client.delete_dataset(
    dataset_full_id,
    delete_contents=True,
    not_found_ok=True,
)
print(f"Deleted BigQuery dataset: {dataset_full_id}")
