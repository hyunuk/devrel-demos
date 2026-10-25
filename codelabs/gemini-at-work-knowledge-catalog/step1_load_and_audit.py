import json
import os
from google.cloud import bigquery

PROJECT_ID = (
    os.environ.get("PROJECT_ID")
    or os.popen("gcloud config get-value project").read().strip()
)
if not PROJECT_ID or PROJECT_ID == "YOUR_PROJECT_ID":
    raise ValueError("PROJECT_ID must be set to a valid project ID.")

DATASET_ID = os.environ.get("DATASET_ID", "payment_analytics")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")
SOURCE_PREFIX = "gs://sample-data-and-media/cymbal-financial-fraud"

bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)

dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
dataset_ref.location = BQ_LOCATION
dataset_ref.description = (
    "Payment logs and payer/payee lookup tables for Knowledge Catalog "
    "and Gemini SQL analysis."
)
bq_client.create_dataset(dataset_ref, exists_ok=True)

tables_to_load = [
    (
        "transaction_logs",
        f"{SOURCE_PREFIX}/logs.json",
        bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    ),
    (
        "dim_payers",
        f"{SOURCE_PREFIX}/payers.csv",
        bigquery.SourceFormat.CSV,
    ),
    (
        "dim_payees",
        f"{SOURCE_PREFIX}/payees.csv",
        bigquery.SourceFormat.CSV,
    ),
]

for table_name, uri, source_format in tables_to_load:
    table_id = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        source_format=source_format,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    if source_format == bigquery.SourceFormat.CSV:
        job_config.skip_leading_rows = 1
    load_job = bq_client.load_table_from_uri(
        uri, table_id, job_config=job_config
    )
    load_job.result()

tbl = f"`{PROJECT_ID}.{DATASET_ID}.transaction_logs`"
valid_cond = (
    "status = 'COMPLETED' AND amount > 0 "
    "AND currency IN ('USD', 'EUR', 'GBP')"
)
audit_sql = f"""
WITH deduped AS (
  SELECT
    transaction_id,
    amount,
    currency,
    status,
    ROW_NUMBER() OVER (
      PARTITION BY transaction_id ORDER BY timestamp ASC
    ) AS rn
  FROM {tbl}
  WHERE transaction_id IS NOT NULL AND TRIM(transaction_id) != ''
)
SELECT
  (SELECT COUNT(*) FROM {tbl}) AS raw_rows,
  (SELECT COUNT(*) FROM {tbl}
   WHERE transaction_id IS NULL OR TRIM(transaction_id) = '') AS blank_rows,
  (SELECT COUNT(*) FROM deduped WHERE rn > 1) AS retry_rows,
  (SELECT COUNT(*) FROM deduped WHERE rn = 1) AS dedup_rows,
  ROUND((SELECT SUM(amount) FROM {tbl}
         WHERE {valid_cond}), 2) AS raw_completed_usd,
  ROUND((SELECT SUM(amount) FROM deduped
         WHERE rn = 1 AND {valid_cond}), 2) AS dedup_completed_usd
"""

row = list(bq_client.query(audit_sql).result())[0]
overstated_usd = round(
    float(row.raw_completed_usd) - float(row.dedup_completed_usd), 2
)

summary = {
    "dataset": f"{PROJECT_ID}.{DATASET_ID}",
    "raw_rows": int(row.raw_rows),
    "blank_id_rows": int(row.blank_rows),
    "duplicate_retry_rows": int(row.retry_rows),
    "dedup_rows": int(row.dedup_rows),
    "raw_completed_usd": float(row.raw_completed_usd),
    "dedup_completed_usd": float(row.dedup_completed_usd),
    "overstated_usd": overstated_usd,
}
print(json.dumps(summary, indent=2))

assert summary["raw_rows"] == 800000
assert summary["blank_id_rows"] == 3936
assert summary["duplicate_retry_rows"] == 7785
assert summary["dedup_rows"] == 788279
assert summary["overstated_usd"] == 10675964.02
