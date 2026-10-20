import json
from google.cloud import dataplex_v1
from schemas import (
    ASPECT_TYPE_ID,
    DATAPLEX_LOCATION,
    PROJECT_ID,
    STATE_FILE,
    ColumnGovernanceFinding,
    GovernanceAuditReport,
)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

order_items_entry_name = state["order_items_entry_name"]
aspect_type_path = state["aspect_type_path"]
aspect_key_prefix = state["aspect_key_prefix"]
catalog_client = dataplex_v1.CatalogServiceClient()
lookup_scope = f"projects/{PROJECT_ID}/locations/{DATAPLEX_LOCATION}"

# 1. Mode 1: EntryView.BASIC (source-system check, 0 aspects returned)
basic_entry = catalog_client.lookup_entry(
    request=dataplex_v1.LookupEntryRequest(
        name=lookup_scope,
        entry=order_items_entry_name,
        view=dataplex_v1.EntryView.BASIC,
    )
)
if len(basic_entry.aspects) != 0:
    raise AssertionError("Expected EntryView.BASIC to return 0 aspects.")

# 2. Mode 2: EntryView.CUSTOM with aspect_types (all 7 annotated columns)
full_entry = catalog_client.lookup_entry(
    request=dataplex_v1.LookupEntryRequest(
        name=lookup_scope,
        entry=order_items_entry_name,
        view=dataplex_v1.EntryView.CUSTOM,
        aspect_types=[aspect_type_path],
    )
)

# 3. Mode 3: EntryView.CUSTOM with aspect_types x paths Cartesian filter
target_paths = ["Schema.product_id", "Schema.sale_price"]
filtered_entry = catalog_client.lookup_entry(
    request=dataplex_v1.LookupEntryRequest(
        name=lookup_scope,
        entry=order_items_entry_name,
        view=dataplex_v1.EntryView.CUSTOM,
        aspect_types=[aspect_type_path],
        paths=target_paths,
    )
)
filtered_cols = sorted(
    k.split("@Schema.", 1)[1]
    for k in filtered_entry.aspects
    if "@Schema." in k
)
if filtered_cols != ["product_id", "sale_price"]:
    raise AssertionError(
        f"Expected ['product_id', 'sale_price'] from paths, got {filtered_cols}"
    )

# 4. Extract column-level aspects keyed by numeric PROJECT_NUMBER
aspect_type_suffix = f".global.{ASPECT_TYPE_ID}"
annotated_columns = 0
restricted_findings = []
certified_columns = []

for aspect_key, aspect_obj in sorted(full_entry.aspects.items()):
    if "@Schema." not in aspect_key:
        continue
    key_prefix, col_name = aspect_key.split("@Schema.", 1)
    if not key_prefix.endswith(aspect_type_suffix):
        continue
    annotated_columns += 1
    data_map = dict(aspect_obj.data)
    finding = ColumnGovernanceFinding(
        column_name=col_name,
        governance_role=str(
            data_map.get("governance_role", "INTERNAL_OPS_KEY")
        ),
        certified_for_ai=bool(data_map.get("certified_for_ai", False)),
    )
    if not finding.certified_for_ai:
        restricted_findings.append(finding)
    else:
        certified_columns.append(finding.column_name)

audit_report = GovernanceAuditReport(
    entry_resource_name=full_entry.name,
    aspect_key_prefix=aspect_key_prefix,
    total_annotated_columns=annotated_columns,
    filtered_path_columns=filtered_cols,
    restricted_columns=restricted_findings,
    certified_columns=certified_columns,
)

if audit_report.total_annotated_columns != 7:
    raise AssertionError(
        f"Expected 7 columns, got {audit_report.total_annotated_columns}"
    )
if len(audit_report.restricted_columns) != 4:
    restricted_cnt = len(audit_report.restricted_columns)
    raise AssertionError(
        f"Expected 4 restricted columns, got {restricted_cnt}"
    )
if len(audit_report.certified_columns) != 3:
    certified_cnt = len(audit_report.certified_columns)
    raise AssertionError(f"Expected 3 certified columns, got {certified_cnt}")

state["certified_columns"] = audit_report.certified_columns
state["restricted_columns"] = [
    item.model_dump() for item in audit_report.restricted_columns
]
with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print("=== lookup_entry Inspection Report ===")
print(f"Inspected Entry ID: {full_entry.name.split('/')[-1]}")
print(f"EntryView.BASIC Aspects Returned: {len(basic_entry.aspects)}")
print(f"EntryView.CUSTOM Aspects Returned: {len(full_entry.aspects)}")
filtered_csv = ", ".join(audit_report.filtered_path_columns)
print(f"EntryView.CUSTOM + paths Filter (2 paths): {filtered_csv}")
print(f"Restricted Columns: {len(audit_report.restricted_columns)}")
for item in audit_report.restricted_columns:
    print(
        f"  - {item.column_name}: role={item.governance_role}, "
        f"certified_for_ai={item.certified_for_ai}"
    )
certified_csv = ", ".join(audit_report.certified_columns)
print(f"Certified Columns for AI: {certified_csv}")
