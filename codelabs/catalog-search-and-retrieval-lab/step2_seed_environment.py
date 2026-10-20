import json
import time
from google.api_core.exceptions import (
    AlreadyExists,
    InvalidArgument,
    NotFound,
    PermissionDenied,
)
from google.cloud import bigquery, dataplex_v1
from google.protobuf import field_mask_pb2, struct_pb2
from schemas import (
    ASPECT_TYPE_ID,
    COLUMN_DESCRIPTIONS,
    COLUMN_GOVERNANCE_RULES,
    DATAPLEX_LOCATION,
    DATASET_ID,
    ENTRY_LINK_ID,
    PROJECT_ID,
    STATE_FILE,
    TABLE_DESCRIPTIONS,
    metric_governance_template,
)

bq_client = bigquery.Client(project=PROJECT_ID)
catalog_client = dataplex_v1.CatalogServiceClient()

# 1. Provision BigQuery dataset, copy 4 tables, and attach 1P descriptions
dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
dataset_ref.location = "US"
bq_client.create_dataset(dataset_ref, exists_ok=True)
print(f"BigQuery dataset ready: {PROJECT_ID}.{DATASET_ID} (US)")

PUBLIC_SOURCE = "bigquery-public-data.thelook_ecommerce"
TABLES = ["users", "orders", "order_items", "products"]
copy_config = bigquery.CopyJobConfig(
    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
)
for table_name in TABLES:
    src_table = f"{PUBLIC_SOURCE}.{table_name}"
    dst_table = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    copy_job = bq_client.copy_table(
        src_table, dst_table, job_config=copy_config
    )
    copy_job.result()
    table_obj = bq_client.get_table(dst_table)
    table_obj.description = TABLE_DESCRIPTIONS[table_name]
    col_descs = COLUMN_DESCRIPTIONS.get(table_name, {})
    if col_descs:
        updated_schema = []
        for field in table_obj.schema:
            if field.name in col_descs:
                repr_dict = field.to_api_repr()
                repr_dict["description"] = col_descs[field.name]
                field = bigquery.SchemaField.from_api_repr(repr_dict)
            updated_schema.append(field)
        table_obj.schema = updated_schema
    bq_client.update_table(table_obj, ["description", "schema"])
    print(f"Copied and enriched table: {DATASET_ID}.{table_name}")

# 2. Create or retrieve global AspectType (metric-governance)
parent_global = f"projects/{PROJECT_ID}/locations/global"
aspect_type_path = f"{parent_global}/aspectTypes/{ASPECT_TYPE_ID}"

try:
    create_op = catalog_client.create_aspect_type(
        parent=parent_global,
        aspect_type_id=ASPECT_TYPE_ID,
        aspect_type=dataplex_v1.AspectType(
            description=(
                "Tracks column-level metric governance role and AI "
                "certification status."
            ),
            metadata_template=metric_governance_template,
        ),
    )
    live_aspect_type = create_op.result()
except AlreadyExists:
    live_aspect_type = catalog_client.get_aspect_type(name=aspect_type_path)

aspect_short_id = live_aspect_type.name.split("/")[-1]
print(f"Verified AspectType ID: {aspect_short_id} (global)")

# 3. Poll Knowledge Catalog search until all 4 table entries are indexed
search_scope = f"projects/{PROJECT_ID}/locations/global"
entry_map = {}
for attempt in range(1, 20):
    hits = list(
        catalog_client.search_entries(
            request=dataplex_v1.SearchEntriesRequest(
                name=search_scope,
                scope=f"projects/{PROJECT_ID}",
                query=f"system=BIGQUERY AND parent:{DATASET_ID}",
                page_size=10,
            )
        )
    )
    for hit in hits:
        short_name = hit.dataplex_entry.name.split("/")[-1]
        entry_map[short_name] = hit.dataplex_entry
    if all(t in entry_map for t in TABLES):
        break
    time.sleep(4)

if not all(t in entry_map for t in TABLES):
    raise RuntimeError(
        f"Timed out waiting for catalog entries: {list(entry_map.keys())}"
    )

order_items_entry = entry_map["order_items"]
products_entry = entry_map["products"]

# Extract numeric PROJECT_NUMBER from the entry resource name
project_number = order_items_entry.name.split("/")[1]
aspect_key_prefix = f"{project_number}.global.{ASPECT_TYPE_ID}"

# 4. Build and attach 7 column-level aspects (@Schema.<col>) on order_items
aspects_map = {}
aspect_keys = []
for col_name, payload in COLUMN_GOVERNANCE_RULES.items():
    aspect_key = f"{aspect_key_prefix}@Schema.{col_name}"
    aspect_keys.append(aspect_key)
    aspect_struct = struct_pb2.Struct()
    aspect_struct.update(payload)
    aspects_map[aspect_key] = dataplex_v1.Aspect(
        aspect_type=aspect_type_path,
        data=aspect_struct,
    )

updated_entry = None
for sync_attempt in range(1, 20):
    try:
        updated_entry = catalog_client.update_entry(
            request=dataplex_v1.UpdateEntryRequest(
                entry=dataplex_v1.Entry(
                    name=order_items_entry.name,
                    aspects=aspects_map,
                ),
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                aspect_keys=aspect_keys,
            )
        )
        break
    except (PermissionDenied, NotFound, InvalidArgument):
        if sync_attempt == 19:
            raise
        time.sleep(5)

print(f"Resolved Entry ID: {updated_entry.name.split('/')[-1]}")
print(f"Aspect Type Scope: {parent_global}")
print(f"Attached Column Aspects: {len(aspect_keys)} columns on order_items")

# 5. Create schema-join EntryLink between order_items and products
bq_entry_group = (
    f"projects/{PROJECT_ID}/locations/{DATAPLEX_LOCATION}/entryGroups/@bigquery"
)
entry_link_name = f"{bq_entry_group}/entryLinks/{ENTRY_LINK_ID}"
schema_join_type = (
    "projects/dataplex-types/locations/global/entryLinkTypes/schema-join"
)
schema_join_aspect = (
    "projects/dataplex-types/locations/global/aspectTypes/schema-join"
)

join_struct = struct_pb2.Struct()
join_struct.update(
    {
        "joins": [
            {
                "source": {"name": "order_items", "fields": ["product_id"]},
                "target": {"name": "products", "fields": ["id"]},
                "type": "FOREIGN_KEY",
                "inferenceSource": "USER",
            }
        ],
        "userManaged": True,
    }
)

try:
    created_link = catalog_client.create_entry_link(
        request=dataplex_v1.CreateEntryLinkRequest(
            parent=bq_entry_group,
            entry_link_id=ENTRY_LINK_ID,
            entry_link=dataplex_v1.EntryLink(
                entry_link_type=schema_join_type,
                entry_references=[
                    dataplex_v1.EntryLink.EntryReference(
                        name=order_items_entry.name
                    ),
                    dataplex_v1.EntryLink.EntryReference(
                        name=products_entry.name
                    ),
                ],
                aspects={
                    "dataplex-types.global.schema-join": dataplex_v1.Aspect(
                        aspect_type=schema_join_aspect,
                        data=join_struct,
                    )
                },
            ),
        )
    )
except AlreadyExists:
    created_link = catalog_client.get_entry_link(name=entry_link_name)

print(f"Verified Schema-Join EntryLink: {created_link.name.split('/')[-1]}")

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "project_id": PROJECT_ID,
            "project_number": project_number,
            "dataplex_location": DATAPLEX_LOCATION,
            "dataset_id": DATASET_ID,
            "aspect_type_id": ASPECT_TYPE_ID,
            "aspect_type_path": aspect_type_path,
            "aspect_key_prefix": aspect_key_prefix,
            "order_items_entry_name": updated_entry.name,
            "products_entry_name": products_entry.name,
            "entry_link_name": created_link.name,
        },
        f,
        indent=2,
    )
print(f"Saved state manifest: {STATE_FILE} ({len(TABLES)} tables, 1 link)")
