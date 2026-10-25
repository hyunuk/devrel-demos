import json
import os
from google.api_core import exceptions as gcp_exceptions
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2
from google.protobuf import struct_pb2

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.popen("gcloud config get-value project").read().strip()
)
if not PROJECT_ID or PROJECT_ID == "YOUR_PROJECT_ID":
    raise ValueError("PROJECT_ID must be set to a valid project ID.")

DATASET_ID = os.environ.get("DATASET_ID", "payment_analytics")
CATALOG_LOCATION = os.environ.get("CATALOG_LOCATION", "us")
ASPECT_TYPE_ID = os.environ.get("ASPECT_TYPE_ID", "payment-rules")

catalog_client = dataplex_v1.CatalogServiceClient()
location_parent = f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}"
aspect_type_name = f"{location_parent}/aspectTypes/{ASPECT_TYPE_ID}"

Anns = dataplex_v1.AspectType.MetadataTemplate.Annotations
Cons = dataplex_v1.AspectType.MetadataTemplate.Constraints
EnumVal = dataplex_v1.AspectType.MetadataTemplate.EnumValue
MetaTpl = dataplex_v1.AspectType.MetadataTemplate

template = MetaTpl(
    name="PaymentRulesTemplate",
    type_="record",
    record_fields=[
        MetaTpl(
            name="data_domain",
            type_="enum",
            index=1,
            annotations=Anns(description="Business domain owning dataset."),
            constraints=Cons(required=True),
            enum_values=[
                EnumVal(name="PAYMENTS", index=1),
                EnumVal(name="TREASURY", index=2),
                EnumVal(name="RETAIL_FINANCE", index=3),
            ],
        ),
        MetaTpl(
            name="deduplication_rule",
            type_="string",
            index=2,
            annotations=Anns(
                description="SQL rule to remove blank IDs and retry rows."
            ),
            constraints=Cons(required=True),
        ),
        MetaTpl(
            name="valid_payment_filter",
            type_="string",
            index=3,
            annotations=Anns(
                description="SQL filter for valid completed payments."
            ),
            constraints=Cons(required=True),
        ),
        MetaTpl(
            name="high_risk_review_rule",
            type_="string",
            index=4,
            annotations=Anns(
                description="Rule for flagging high-risk country pairs."
            ),
            constraints=Cons(required=True),
        ),
    ],
)

aspect_type_spec = dataplex_v1.AspectType(
    description="Payment deduplication and risk review rules for BigQuery.",
    display_name="Payment Rules",
    metadata_template=template,
)

try:
    op = catalog_client.create_aspect_type(
        parent=location_parent,
        aspect_type_id=ASPECT_TYPE_ID,
        aspect_type=aspect_type_spec,
    )
    op.result(timeout=120)
    aspect_type_status = "READY"
except gcp_exceptions.AlreadyExists:
    aspect_type_spec.name = aspect_type_name
    mask = field_mask_pb2.FieldMask(
        paths=["description", "display_name", "metadata_template"]
    )
    op = catalog_client.update_aspect_type(
        aspect_type=aspect_type_spec, update_mask=mask
    )
    op.result(timeout=120)
    aspect_type_status = "READY"

entry_name = (
    f"projects/{PROJECT_ID}/locations/{CATALOG_LOCATION}/"
    f"entryGroups/@bigquery/entries/bigquery.googleapis.com/"
    f"projects/{PROJECT_ID}/datasets/{DATASET_ID}/tables/transaction_logs"
)
entry_meta = catalog_client.get_entry(
    request={"name": entry_name, "view": dataplex_v1.EntryView.BASIC}
)

overview_key = "dataplex-types.global.overview"
overview_struct = struct_pb2.Struct()
overview_struct.update({
    "content": (
        "<h3>Payment Transaction Logs (transaction_logs)</h3>"
        "<p>Contains 800,000 raw payment gateway events. Exclude blank "
        "<code>transaction_id</code> rows and deduplicate retries using "
        "<code>ROW_NUMBER() OVER (PARTITION BY transaction_id "
        "ORDER BY timestamp ASC) = 1</code> before summing amount.</p>"
    )
})

rules_key = f"{PROJECT_ID}.{CATALOG_LOCATION}.{ASPECT_TYPE_ID}"
rules_struct = struct_pb2.Struct()
rules_struct.update({
    "data_domain": "PAYMENTS",
    "deduplication_rule": (
        "Exclude rows where transaction_id IS NULL OR TRIM(transaction_id) "
        "= '' and keep only ROW_NUMBER() OVER (PARTITION BY transaction_id "
        "ORDER BY timestamp ASC) = 1."
    ),
    "valid_payment_filter": (
        "status = 'COMPLETED' AND amount > 0 "
        "AND currency IN ('USD', 'EUR', 'GBP')"
    ),
    "high_risk_review_rule": (
        "Join dim_payers on payor_id and dim_payees on payee_id. Treat "
        "payments from payers with payor_risk_score >= 0.70 as high-risk "
        "volume, and flag any (payee_country, payee_category) pair as "
        "REVIEW_REQUIRED when high_risk_volume_pct >= 4.00, else NORMAL."
    ),
})

updated_entry = dataplex_v1.Entry(
    name=entry_meta.name,
    aspects={
        overview_key: dataplex_v1.Aspect(data=overview_struct),
        rules_key: dataplex_v1.Aspect(data=rules_struct),
    },
)
catalog_client.update_entry(
    request={
        "entry": updated_entry,
        "update_mask": field_mask_pb2.FieldMask(paths=["aspects"]),
        "aspect_keys": [overview_key, rules_key],
    }
)

verified_entry = catalog_client.lookup_entry(
    request={
        "name": location_parent,
        "entry": entry_meta.name,
        "view": dataplex_v1.EntryView.ALL,
    }
)

matched_key = None
matched_fields = {}
for k, aspect in verified_entry.aspects.items():
    if k.endswith(f".{CATALOG_LOCATION}.{ASPECT_TYPE_ID}"):
        matched_key = k
        matched_fields = dict(aspect.data)
        break

assert matched_key is not None, "payment-rules aspect not found"
print(json.dumps({
    "aspect_type_id": ASPECT_TYPE_ID,
    "aspect_type_status": aspect_type_status,
    "table": f"{DATASET_ID}.transaction_logs",
    "attached_aspect_suffix": f".{CATALOG_LOCATION}.{ASPECT_TYPE_ID}",
    "data_domain": matched_fields["data_domain"],
    "verified_fields": sorted(list(matched_fields.keys())),
}, indent=2))
