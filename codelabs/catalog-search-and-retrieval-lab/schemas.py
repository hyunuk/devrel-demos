import os
import re
from typing import List
from google import genai
from google.cloud import dataplex_v1
from pydantic import BaseModel, Field

# Load environment configuration with fail-fast validation
PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
DATAPLEX_LOCATION = os.environ.get("DATAPLEX_LOCATION", "us").strip()
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global").strip()
DATASET_ID = os.environ.get("DATASET_ID", "kc_ecommerce_sandbox").strip()
ASPECT_TYPE_ID = os.environ.get("ASPECT_TYPE_ID", "metric-governance").strip()
ENTRY_LINK_ID = os.environ.get(
    "ENTRY_LINK_ID", "el-order-items-products-join"
).strip()
STATE_FILE = "catalog_state.json"

if not PROJECT_ID or PROJECT_ID == "your-project-id":
    raise ValueError(
        "PROJECT_ID environment variable must be set to a valid project ID."
    )
if not DATAPLEX_LOCATION or DATAPLEX_LOCATION == "your-location":
    raise ValueError(
        "DATAPLEX_LOCATION must be set to a valid region (e.g., 'us')."
    )


class ColumnGovernanceFinding(BaseModel):
    column_name: str = Field(
        description="BigQuery column name bound to metric-governance aspect."
    )
    governance_role: str = Field(
        description="Assigned analytical role (REVENUE_METRIC, JOIN_KEY, etc.)."
    )
    certified_for_ai: bool = Field(
        description="True when certified for AI-generated SQL queries."
    )


class GovernanceAuditReport(BaseModel):
    entry_resource_name: str = Field(
        description="Full Knowledge Catalog entry resource path."
    )
    aspect_key_prefix: str = Field(
        description="Numeric project-number aspect key prefix."
    )
    total_annotated_columns: int = Field(
        description="Count of columns annotated with metric-governance aspects."
    )
    filtered_path_columns: List[str] = Field(
        description="Columns returned when filtering by Schema.<col> paths."
    )
    restricted_columns: List[ColumnGovernanceFinding] = Field(
        description="Columns where certified_for_ai=False."
    )
    certified_columns: List[str] = Field(
        description="Column names where certified_for_ai=True."
    )


class GroundedAgentDecision(BaseModel):
    selected_tables: List[str] = Field(
        description="Tables chosen from lookup_context YAML."
    )
    join_conditions: List[str] = Field(
        description="Cross-table join predicates extracted from context."
    )
    sql_query: str = Field(
        description="Synthesized BigQuery Standard SQL query."
    )
    grounding_rationale: str = Field(
        description="Explanation of how catalog context guided SQL synthesis."
    )


# Matching Knowledge Catalog AspectType metadata template (1-based indices)
metric_governance_template = dataplex_v1.AspectType.MetadataTemplate(
    name="metric_governance_metadata",
    type_="record",
    record_fields=[
        dataplex_v1.AspectType.MetadataTemplate(
            name="governance_role",
            type_="enum",
            index=1,
            enum_values=[
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="REVENUE_METRIC", index=1
                ),
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="LIFECYCLE_FILTER", index=2
                ),
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="JOIN_KEY", index=3
                ),
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="INTERNAL_OPS_KEY", index=4
                ),
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="RESTRICTED_IDENTIFIER", index=5
                ),
                dataplex_v1.AspectType.MetadataTemplate.EnumValue(
                    name="LOGISTICS_TIMESTAMP", index=6
                ),
            ],
            annotations=dataplex_v1.AspectType.MetadataTemplate.Annotations(
                description="Analytical role of the column in SQL reporting."
            ),
            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(
                required=True
            ),
        ),
        dataplex_v1.AspectType.MetadataTemplate(
            name="certified_for_ai",
            type_="bool",
            index=2,
            annotations=dataplex_v1.AspectType.MetadataTemplate.Annotations(
                description="True when column is certified for AI SQL queries."
            ),
            constraints=dataplex_v1.AspectType.MetadataTemplate.Constraints(
                required=True
            ),
        ),
    ],
)

COLUMN_GOVERNANCE_RULES = {
    "sale_price": {
        "governance_role": "REVENUE_METRIC",
        "certified_for_ai": True,
    },
    "status": {
        "governance_role": "LIFECYCLE_FILTER",
        "certified_for_ai": True,
    },
    "product_id": {
        "governance_role": "JOIN_KEY",
        "certified_for_ai": True,
    },
    "inventory_item_id": {
        "governance_role": "INTERNAL_OPS_KEY",
        "certified_for_ai": False,
    },
    "user_id": {
        "governance_role": "RESTRICTED_IDENTIFIER",
        "certified_for_ai": False,
    },
    "shipped_at": {
        "governance_role": "LOGISTICS_TIMESTAMP",
        "certified_for_ai": False,
    },
    "returned_at": {
        "governance_role": "LOGISTICS_TIMESTAMP",
        "certified_for_ai": False,
    },
}

TABLE_DESCRIPTIONS = {
    "users": (
        "Customer profile table. Primary key: id. "
        "Joins to orders.user_id."
    ),
    "orders": (
        "Customer order header table. Primary key: order_id. "
        "Foreign key: user_id -> users.id."
    ),
    "order_items": (
        "Order line items table. Foreign keys: order_id -> orders.order_id, "
        "product_id -> products.id. Revenue metric: sale_price (USD)."
    ),
    "products": (
        "Product catalog dimension table. Primary key: id "
        "(join target for order_items.product_id). Category column: category."
    ),
}

COLUMN_DESCRIPTIONS = {
    "order_items": {
        "id": "Unique order line item identifier.",
        "order_id": "Foreign key to orders.order_id.",
        "user_id": "Restricted customer identifier.",
        "product_id": "Foreign key joining to products.id.",
        "inventory_item_id": "Internal warehouse inventory key.",
        "status": "Fulfillment status (filter WHERE status = 'Complete').",
        "sale_price": "Completed item sale price in USD.",
        "shipped_at": "Warehouse shipment timestamp.",
        "returned_at": "Return processing timestamp.",
    },
    "products": {
        "id": "Primary product identifier joined with order_items.product_id.",
        "category": "Product merchandise category for revenue reporting.",
        "brand": "Product manufacturer brand name.",
        "retail_price": "Catalog list price in USD.",
    },
}


def discover_gemini_flash_model(genai_client: genai.Client) -> str:
    """Dynamically resolves the newest GA Gemini Flash model."""
    candidates = []
    pattern = re.compile(r"^gemini-(\d+)\.(\d+)-flash(?:-(.+))?$")
    for m in genai_client.models.list():
        short_name = (m.name or "").split("/")[-1]
        match = pattern.match(short_name)
        if not match:
            continue
        major, minor, suffix = (
            int(match.group(1)),
            int(match.group(2)),
            match.group(3) or "",
        )
        if any(
            x in suffix
            for x in ("lite", "image", "audio", "tts", "live", "thinking")
        ):
            continue
        is_ga = 1 if (suffix == "" or suffix == "001") else 0
        candidates.append(((is_ga, major, minor), short_name))
    if not candidates:
        raise RuntimeError(
            "No active Gemini Flash model discovered from google-genai."
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


if __name__ == "__main__":
    print(f"Configured Project ID: {PROJECT_ID}")
    print(f"Catalog Data Plane Region: {DATAPLEX_LOCATION}")
    print(f"Gemini Endpoint Location: {GEMINI_LOCATION}")
    print(f"Target BigQuery Dataset: {DATASET_ID}")
    print(f"Custom AspectType ID: {ASPECT_TYPE_ID}")
    print(f"Schema-Join EntryLink ID: {ENTRY_LINK_ID}")
    print("✓ Validated ColumnGovernanceFinding, GovernanceAuditReport, and")
    print("  GroundedAgentDecision Pydantic schemas.")
    print("✓ Prepared metric-governance AspectType metadata template.")
