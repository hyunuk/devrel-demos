"""Create a Knowledge Catalog AspectType and attach contracts to BigQuery entries."""

import os
import time
from google.api_core import exceptions as gcp_exceptions
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2
from google.protobuf import struct_pb2

PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
if not PROJECT_ID or "your-project" in PROJECT_ID.lower():
    raise ValueError(
        "Set PROJECT_ID to a valid Google Cloud project ID before running."
    )

KC_LOCATION = os.environ.get("KC_LOCATION", "us").strip().lower()
DATASET_ID = os.environ.get("DATASET_ID", "charlies_coffee_kc").strip()
ASPECT_TYPE_ID = "business-metric-contract"

catalog_client = dataplex_v1.CatalogServiceClient()
location_parent = f"projects/{PROJECT_ID}/locations/{KC_LOCATION}"
aspect_type_name = f"{location_parent}/aspectTypes/{ASPECT_TYPE_ID}"

aspect_template = dataplex_v1.AspectType.MetadataTemplate(
    name="BusinessMetricContract",
    type_="record",
    record_fields=[
        dataplex_v1.AspectType.MetadataTemplate(
            index=1,
            name="entity_scope",
            type_="string",
            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(
                required=True
            ),
            annotations=dataplex_v1.AspectType.MetadataTemplate.Annotations(
                display_name="Entity scope",
                description="Business population represented by this table.",
            ),
        ),
        dataplex_v1.AspectType.MetadataTemplate(
            index=2,
            name="required_sql_filter",
            type_="string",
            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(
                required=True
            ),
            annotations=dataplex_v1.AspectType.MetadataTemplate.Annotations(
                display_name="Required SQL filter",
                description="Mandatory row filter when computing Charlie's Coffee metrics.",
            ),
        ),
        dataplex_v1.AspectType.MetadataTemplate(
            index=3,
            name="metric_rule",
            type_="string",
            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(
                required=True
            ),
            annotations=dataplex_v1.AspectType.MetadataTemplate.Annotations(
                display_name="Metric formula and join rule",
                description="Authoritative metric calculation and conformed join rule.",
            ),
        ),
    ],
)

try:
    create_op = catalog_client.create_aspect_type(
        parent=location_parent,
        aspect_type_id=ASPECT_TYPE_ID,
        aspect_type=dataplex_v1.AspectType(
            description="Business metric rules and row filters for Charlie's Coffee analytics.",
            metadata_template=aspect_template,
        ),
    )
    created_aspect_type = create_op.result()
    print(f"Created AspectType: {created_aspect_type.name}")
except gcp_exceptions.AlreadyExists:
    existing_aspect_type = catalog_client.get_aspect_type(name=aspect_type_name)
    print(f"Reusing existing AspectType: {existing_aspect_type.name}")

TABLE_CONTRACTS = {
    "customer_feedback": {
        "summary": (
            "Customer review table covering Charlie's Coffee stores and named"
            " local competitors across San Francisco ZIP codes. Always filter"
            " WHERE business_type = 'own_store' before reporting Charlie's"
            " Coffee ratings or store counts."
        ),
        "entity_scope": (
            "Mixed brand and competitor reviews (120 Charlie's Coffee rows and"
            " 180 competitor rows)."
        ),
        "required_sql_filter": "business_type = 'own_store'",
        "metric_rule": (
            "For Charlie's Coffee customer ratings, filter WHERE business_type"
            " = 'own_store'. Only 6 San Francisco ZIP codes (94102, 94103,"
            " 94110, 94118, 94123, 94133) have Charlie's Coffee stores; rows in"
            " 94121 and 94122 belong exclusively to competitors."
        ),
    },
    "historical_sales": {
        "summary": (
            "Weekly store and SKU sales for Charlie's Coffee own stores only"
            " (104 ISO weeks). Use SUM(net_revenue) as the numerator when"
            " calculating Charlie's Coffee market capture rate by zip_code."
        ),
        "entity_scope": (
            "Charlie's Coffee own-store weekly sales across 6 operating San"
            " Francisco ZIP codes."
        ),
        "required_sql_filter": "NONE (all rows represent Charlie's Coffee own stores)",
        "metric_rule": (
            "Aggregate SUM(net_revenue) grouped by zip_code first, then join"
            " to aggregated market_demand on zip_code and compute"
            " SAFE_DIVIDE(SUM(net_revenue), SUM(category_spend_usd)) for store"
            " market capture rate."
        ),
    },
    "market_demand": {
        "summary": (
            "Whole-market weekly coffee spend across ALL coffee businesses in"
            " each San Francisco ZIP code (15 ZIP codes total), NOT Charlie's"
            " Coffee revenue. Use SUM(category_spend_usd) as the denominator"
            " for Charlie's Coffee market capture rate."
        ),
        "entity_scope": (
            "Total third-party coffee-category spend across all businesses in"
            " 15 San Francisco ZIP codes."
        ),
        "required_sql_filter": (
            "Join to historical_sales on zip_code after pre-aggregating each"
            " table by zip_code to avoid week-by-SKU fan-out."
        ),
        "metric_rule": (
            "category_spend_usd is whole-ZIP coffee spend across every coffee"
            " shop, never Charlie's Coffee revenue. Market capture rate per"
            " store ZIP = SAFE_DIVIDE(brand_zip_sales.brand_revenue,"
            " zip_market.total_market_spend)."
        ),
    },
}

aspect_key = f"{PROJECT_ID}.{KC_LOCATION}.{ASPECT_TYPE_ID}"
overview_key = "dataplex-types.global.overview"
overview_type = "projects/dataplex-types/locations/global/aspectTypes/overview"
target_suffix = f".{KC_LOCATION}.{ASPECT_TYPE_ID}"

for table_name, contract in TABLE_CONTRACTS.items():
    entry_name = (
        f"{location_parent}/entryGroups/@bigquery/entries/"
        f"bigquery.googleapis.com/projects/{PROJECT_ID}"
        f"/datasets/{DATASET_ID}/tables/{table_name}"
    )
    entry = None
    for attempt in range(6):
        try:
            entry = catalog_client.lookup_entry(
                request=dataplex_v1.LookupEntryRequest(
                    name=location_parent,
                    entry=entry_name,
                    view=dataplex_v1.EntryView.BASIC,
                )
            )
            break
        except gcp_exceptions.NotFound:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)

    contract_payload = struct_pb2.Struct()
    contract_payload.update(
        {
            "entity_scope": contract["entity_scope"],
            "required_sql_filter": contract["required_sql_filter"],
            "metric_rule": contract["metric_rule"],
        }
    )

    overview_payload = struct_pb2.Struct()
    overview_payload.update(
        {
            "content": (
                f"<p>{contract['summary']}</p>"
                f"<p><strong>Scope:</strong> {contract['entity_scope']}</p>"
                f"<p><strong>Required SQL filter:</strong> <code>{contract['required_sql_filter']}</code></p>"
                f"<p><strong>Metric rule:</strong> {contract['metric_rule']}</p>"
            ),
            "contentType": "HTML",
        }
    )

    entry_patch = dataplex_v1.Entry(
        name=entry.name,
        aspects={
            aspect_key: dataplex_v1.Aspect(
                aspect_type=aspect_type_name,
                data=contract_payload,
            ),
            overview_key: dataplex_v1.Aspect(
                aspect_type=overview_type,
                data=overview_payload,
            ),
        },
    )

    catalog_client.update_entry(
        request=dataplex_v1.UpdateEntryRequest(
            entry=entry_patch,
            update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
            aspect_keys=[aspect_key, overview_key],
        )
    )

    verified_entry = catalog_client.lookup_entry(
        request=dataplex_v1.LookupEntryRequest(
            name=location_parent,
            entry=entry_name,
            view=dataplex_v1.EntryView.ALL,
        )
    )
    matching_aspects = {
        k: v
        for k, v in verified_entry.aspects.items()
        if k.endswith(target_suffix)
    }
    assert matching_aspects, f"Missing {target_suffix} aspect on {table_name}"
    matched_key = next(iter(matching_aspects))
    print(f"Attached and verified {matched_key} on {table_name}")
