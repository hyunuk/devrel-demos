import json
import os
import shutil
from google.api_core.exceptions import NotFound
from google.cloud import bigquery, dataplex_v1
from google.protobuf import field_mask_pb2
from schemas import (
    ASPECT_TYPE_ID,
    COLUMN_GOVERNANCE_RULES,
    DATAPLEX_LOCATION,
    DATASET_ID,
    ENTRY_LINK_ID,
    PROJECT_ID,
    STATE_FILE,
)

catalog_client = dataplex_v1.CatalogServiceClient()
bq_client = bigquery.Client(project=PROJECT_ID)

state = {}
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

order_items_entry_name = state.get("order_items_entry_name", "")
aspect_key_prefix = state.get("aspect_key_prefix", "")
aspect_type_path = state.get(
    "aspect_type_path",
    f"projects/{PROJECT_ID}/locations/global/aspectTypes/{ASPECT_TYPE_ID}",
)
entry_link_name = state.get(
    "entry_link_name",
    f"projects/{PROJECT_ID}/locations/{DATAPLEX_LOCATION}"
    f"/entryGroups/@bigquery/entryLinks/{ENTRY_LINK_ID}",
)

print("Starting reverse-dependency resource cleanup...")

# 1. Delete schema-join EntryLink first
link_status = "deleted"
if "entry_link_name" in locals() and entry_link_name:
    try:
        catalog_client.delete_entry_link(name=entry_link_name)
    except (NotFound, NameError):
        link_status = "already absent"
print(f"Deleted EntryLink ID: {ENTRY_LINK_ID} ({link_status})")

# 2. Detach column-level aspects from the order_items entry
aspect_keys = [
    f"{aspect_key_prefix}@Schema.{col}"
    for col in COLUMN_GOVERNANCE_RULES
]
detached_count = 0
if (
    "order_items_entry_name" in locals()
    and order_items_entry_name
    and aspect_key_prefix
):
    try:
        catalog_client.update_entry(
            request=dataplex_v1.UpdateEntryRequest(
                entry=dataplex_v1.Entry(
                    name=order_items_entry_name, aspects={}
                ),
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                delete_missing_aspects=True,
                aspect_keys=aspect_keys,
            )
        )
        detached_count = len(aspect_keys)
    except (NotFound, NameError):
        detached_count = 0
print(f"Detached column aspects: {detached_count} keys removed")

# 3. Delete global AspectType (metric-governance)
aspect_scope = "global"
if "aspect_type_path" in locals() and aspect_type_path:
    try:
        del_op = catalog_client.delete_aspect_type(name=aspect_type_path)
        del_op.result()
    except (NotFound, NameError):
        aspect_scope = "global, already absent"
print(f"Deleted AspectType ID: {ASPECT_TYPE_ID} ({aspect_scope})")

# 4. Delete BigQuery sandbox dataset and all copied tables
dataset_full_id = f"{PROJECT_ID}.{DATASET_ID}"
if "dataset_full_id" in locals() and dataset_full_id:
    try:
        bq_client.delete_dataset(
            dataset_full_id, delete_contents=True, not_found_ok=True
        )
    except (NotFound, NameError):
        pass
print(f"Deleted BigQuery dataset: {dataset_full_id}")

# 5. Remove local state file and bytecode cache
if os.path.exists(STATE_FILE):
    os.remove(STATE_FILE)
shutil.rmtree("__pycache__", ignore_errors=True)
print(f"Removed local state file: {STATE_FILE}")

print("✓ Standalone teardown complete. Environment cleanly reset.")
