# Analyze payment data with Gemini at work and Knowledge Catalog

Companion Python scripts for the **Analyze payment data with Gemini at work and
Knowledge Catalog** Google Cloud Codelab (`gemini-at-work-knowledge-catalog`).

## Script sequence

1. `step1_load_and_audit.py` creates the `payment_analytics` BigQuery dataset in
   `US`, loads `transaction_logs`, `dim_payers`, and `dim_payees` from
   `gs://sample-data-and-media/cymbal-financial-fraud/`, and compares a naive
   `SUM(amount)` query against a deduplicated query that keeps only the earliest
   timestamp per non-blank `transaction_id`.
2. `step2_attach_payment_rules.py` creates the `payment-rules` `AspectType` in
   Knowledge Catalog (`us`), attaches both a human-readable `overview` aspect
   and structured `payment-rules` metadata (`data_domain`, `deduplication_rule`,
   `valid_payment_filter`, `high_risk_review_rule`) to `transaction_logs`, and
   verifies the attached aspect via `lookup_entry` (`EntryView.ALL`).
3. `step3_search_and_check_gemini.py` queries Knowledge Catalog with
   `search_entries` and `lookup_context` for `payment_analytics` tables and
   dynamically resolves the newest active Gemini 3.x+ Flash model on the
   `global` endpoint via `google-genai`.
4. `step4_aggregate_valid_payments.py` generates both a schema-only baseline
   query (without Knowledge Catalog) and a governed query grounded in the
   `payment-rules` aspect (`response_schema=PaymentAggregationComparisonPlan`),
   comparing how deduplication removes overstated retry volume across recipient
   countries and categories.
5. `step5_flag_high_risk_pairs.py` applies the `high_risk_review_rule` threshold
   (`payor_risk_score >= 0.70` and `high_risk_volume_pct >= 4.00%`) to join
   `transaction_logs`, `dim_payers`, and `dim_payees` and flag recipient country
   and category pairs requiring review (`US Financial` and `US Retail`).
6. `cleanup.py` detaches the catalog aspects, deletes the `payment-rules`
   `AspectType`, deletes the `payment_analytics` BigQuery dataset, and cleans
   local Python cache files in reverse dependency order.
