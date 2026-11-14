"""Load Charlie's Coffee Expansion tables into BigQuery without catalog rules."""

import os
import pathlib
from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
if not PROJECT_ID or "your-project" in PROJECT_ID.lower():
    raise ValueError(
        "Set PROJECT_ID to a valid Google Cloud project ID before running."
    )

BQ_LOCATION = os.environ.get("BQ_LOCATION", "US").strip()
DATASET_ID = os.environ.get("DATASET_ID", "charlies_coffee_kc").strip()
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

TABLE_SCHEMAS = {
    "customer_feedback": [
        bigquery.SchemaField("feedback_id", "STRING"),
        bigquery.SchemaField("business_id", "STRING"),
        bigquery.SchemaField("business_name", "STRING"),
        bigquery.SchemaField("business_type", "STRING"),
        bigquery.SchemaField("zip_code", "STRING"),
        bigquery.SchemaField("rating", "INTEGER"),
        bigquery.SchemaField("source", "STRING"),
        bigquery.SchemaField("review_date", "DATE"),
        bigquery.SchemaField("review_text", "STRING"),
    ],
    "historical_sales": [
        bigquery.SchemaField("week_start_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("store_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("zip_code", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("sku", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("units_sold", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("net_revenue", "NUMERIC", mode="REQUIRED"),
    ],
    "market_demand": [
        bigquery.SchemaField("zip_code", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("week_start_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("category_spend_usd", "NUMERIC", mode="REQUIRED"),
        bigquery.SchemaField("transaction_count", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("avg_ticket", "NUMERIC", mode="REQUIRED"),
    ],
    "demographics_and_traffic": [
        bigquery.SchemaField("zip_code", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("neighborhood_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("city", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("latitude", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("longitude", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("median_household_income", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("total_population", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("households", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("median_age", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("morning_foot_traffic_index", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("evening_foot_traffic_index", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("weekend_foot_traffic_index", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("foot_traffic_score", "FLOAT", mode="REQUIRED"),
    ],
    "candidate_sites": [
        bigquery.SchemaField("site_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("zip_code", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("neighborhood_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("street_address", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("latitude", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("longitude", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("available_sqft", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("monthly_rent", "NUMERIC", mode="REQUIRED"),
        bigquery.SchemaField("lease_term_months", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("listed_date", "DATE", mode="REQUIRED"),
    ],
}

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)

dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
dataset_ref.location = BQ_LOCATION
bq_client.create_dataset(dataset_ref, exists_ok=True)
print(f"Dataset ready: {PROJECT_ID}.{DATASET_ID} (location={BQ_LOCATION})")

for table_name, schema_fields in TABLE_SCHEMAS.items():
    csv_path = DATA_DIR / f"{table_name}.csv"
    table_id = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        schema=schema_fields,
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=0,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with csv_path.open("rb") as csv_file:
        load_job = bq_client.load_table_from_file(
            csv_file,
            table_id,
            location=BQ_LOCATION,
            job_config=job_config,
        )
    load_job.result()
    loaded_table = bq_client.get_table(table_id)
    assert loaded_table.num_rows > 0, f"Expected non-zero rows in {table_id}"
    print(f"Loaded {table_id}: {loaded_table.num_rows} rows")
