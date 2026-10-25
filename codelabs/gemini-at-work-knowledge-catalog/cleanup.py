import json
import os
import shutil
from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.popen("gcloud config get-value project").read().strip()
)
if not PROJECT_ID or PROJECT_ID == "YOUR_PROJECT_ID":
    raise ValueError("PROJECT_ID must be set to a valid project ID.")

DATASET_ID = os.environ.get("DATASET_ID", "payment_analytics")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")
CATALOG_LOCATION = os.environ.get("CATALOG_LOCATION", "us")
ASPECT_TYPE_ID = os.environ.get("ASPECT_TYPE_ID", "payment-rules")

cleanup_log = {
    "entry_aspects_detached": False,
    "aspect_type_deleted": False,
    "bigquery_dataset_deleted": False,
    "pycache_cleaned": False,
}

catalog_client = dataplex_v1.CatalogServiceClient()
entry_name = (
    f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}/"
    f"entryGroups/@bigquery/entries/bigquery.googleapis.com/"
    f"projects/{PROJECT_ID}/datasets/{DATASET_ID}/tables/transaction_logs"
)
if "entry_name" in locals() and entry_name:
    try:
        entry_meta = catalog_client.get_entry(
            request={"name": entry_name, "view": dataplex_v1.EntryView.BASIC}
        )
        cleared_entry = dataplex_v1.Entry(name=entry_meta.name, aspects={})
        catalog_client.update_entry(
            request={
                "entry": cleared_entry,
                "update_mask": field_mask_pb2.FieldMask(paths=["aspects"]),
                "delete_missing_aspects": True,
            }
        )
        cleanup_log["entry_aspects_detached"] = True
    except (gcp_exceptions.NotFound, NameError):
        cleanup_log["entry_aspects_detached"] = True

aspect_type_name = (
    f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}/"
    f"aspectTypes/{ASPECT_TYPE_ID}"
)
if "aspect_type_name" in locals() and aspect_type_name:
    try:
        op = catalog_client.delete_aspect_type(name=aspect_type_name)
        op.result(timeout=120)
        cleanup_log["aspect_type_deleted"] = True
    except (gcp_exceptions.NotFound, NameError):
        cleanup_log["aspect_type_deleted"] = True

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
dataset_full_id = f"{PROJECT_ID}.{DATASET_ID}"
if "dataset_full_id" in locals() and dataset_full_id:
    try:
        bq_client.delete_dataset(
            dataset_full_id,
            delete_contents=True,
            not_found_ok=True,
        )
        cleanup_log["bigquery_dataset_deleted"] = True
    except (gcp_exceptions.NotFound, NameError):
        cleanup_log["bigquery_dataset_deleted"] = True

shutil.rmtree("__pycache__", ignore_errors=True)
cleanup_log["pycache_cleaned"] = True

print(json.dumps(cleanup_log, indent=2))
