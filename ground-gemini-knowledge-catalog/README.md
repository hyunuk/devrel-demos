# Ground Gemini at work with Knowledge Catalog

Companion scripts and Charlie's Coffee Expansion sample CSV data for the standalone Google Cloud Codelab demonstrating how Knowledge Catalog `AspectType` and `Aspect` business contracts change query accuracy in Gemini at work.

## Contents

- `data/`: Five CSV tables (`customer_feedback.csv`, `historical_sales.csv`, `market_demand.csv`, `demographics_and_traffic.csv`, `candidate_sites.csv`).
- `step1_load_bigquery.py`: Creates the `charlies_coffee_kc` BigQuery dataset and loads the five CSV tables without catalog rules.
- `step2_attach_aspects.py`: Creates the `business-metric-contract` Knowledge Catalog `AspectType` and attaches governed `Aspect` records and overview notes to the `@bigquery` table entries.
- `cleanup.py`: Deletes the `business-metric-contract` `AspectType` and removes the `charlies_coffee_kc` BigQuery dataset in reverse dependency order.
