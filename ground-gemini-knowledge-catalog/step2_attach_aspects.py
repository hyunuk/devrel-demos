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
    aspect_type_obj = create_op.result()
except gcp_exceptions.AlreadyExists:
    aspect_type_obj = catalog_client.get_aspect_type(name=aspect_type_name)
print(f"AspectType ready: {aspect_type_obj.name}")

TABLE_CONTRACTS = {
    "customer_feedback": {
        "summary": (
            "Customer review table covering Charlie's Coffee stores and local"
            " competitors across San Francisco ZIP codes. For Charlie's Coffee"
            " customer ratings or store experience scores, ALWAYS filter"
            " WHERE business_type = 'own_store' AND source IN ('Google Maps',"
            " 'Yelp') to exclude competitor cafes and third-party DoorDash"
            " courier delivery ratings."
        ),
        "entity_scope": (
            "Mixed reviews across Google Maps, Yelp, and DoorDash (only 76 rows"
            " represent verified in-cafe Charlie's Coffee visits)."
        ),
        "required_sql_filter": (
            "business_type = 'own_store' AND source IN ('Google Maps', 'Yelp')"
        ),
        "metric_rule": (
            "For Charlie's Coffee average customer rating by ZIP code, filter"
            " WHERE business_type = 'own_store' AND source IN ('Google Maps',"
            " 'Yelp'). DoorDash rows reflect third-party courier transit and"
            " are excluded from Charlie's Coffee store ratings."
        ),
    },
    "historical_sales": {
        "summary": (
            "Weekly store and SKU net revenue for Charlie's Coffee own stores."
            " SKU prefixes encode product lines: SKU-1xx (Espresso Drinks),"
            " SKU-2xx (Filter Coffee), SKU-3xx (Cold Beverages), SKU-4xx"
            " (Tea & Matcha), SKU-5xx (Retail Beans), SKU-6xx (Food & Snacks),"
            " and SKU-7xx (Merchandise). For coffee market capture rate, include"
            " ONLY prepared beverage SKUs (SKU-1%, SKU-2%, SKU-3%, SKU-4%)."
        ),
        "entity_scope": (
            "Charlie's Coffee weekly store sales across prepared beverages,"
            " whole-bean retail bags, food, and merchandise."
        ),
        "required_sql_filter": (
            "REGEXP_CONTAINS(sku, r'^SKU-[1-4]') when comparing against"
            " market_demand.category_spend_usd"
        ),
        "metric_rule": (
            "Filter WHERE REGEXP_CONTAINS(sku, r'^SKU-[1-4]') to exclude"
            " SKU-5xx retail beans, SKU-6xx food, and SKU-7xx merchandise,"
            " aggregate SUM(net_revenue) grouped by zip_code first, then join"
            " to aggregated market_demand on zip_code and compute"
            " SAFE_DIVIDE(beverage_revenue, total_market_spend)."
        ),
    },
    "market_demand": {
        "summary": (
            "Whole-market weekly prepared-beverage spend across ALL coffee"
            " shops in each San Francisco ZIP code (15 ZIP codes), NOT"
            " Charlie's Coffee revenue. Excludes packaged retail beans, food,"
            " and merchandise. Pre-aggregate SUM(category_spend_usd) by"
            " zip_code before joining to historical_sales."
        ),
        "entity_scope": (
            "Third-party prepared-beverage spend across all coffee businesses"
            " in 15 San Francisco ZIP codes."
        ),
        "required_sql_filter": (
            "Pre-aggregate SUM(category_spend_usd) by zip_code before joining"
            " to historical_sales (filtered to SKU-1xx through SKU-4xx)."
        ),
        "metric_rule": (
            "category_spend_usd measures prepared beverage spend only. Market"
            " capture rate per store ZIP = SAFE_DIVIDE(SUM(net_revenue for"
            " SKU-1xx..SKU-4xx), SUM(category_spend_usd))."
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
