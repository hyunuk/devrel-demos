# Knowledge Catalog search, entry lookup, and context grounding lab

Companion Python scripts for the **Search, inspect, and ground metadata in
Knowledge Catalog** Google Cloud Codelab (`catalog-search-and-retrieval`).

## Script sequence

1. `schemas.py` — Shared environment validation, Pydantic `BaseModel` contracts
   (`ColumnGovernanceFinding`, `GovernanceAuditReport`, `GroundedAgentDecision`),
   `metric-governance` `AspectType` template, and dynamic GA Gemini Flash
   discovery.
2. `step2_seed_environment.py` — Provisions the `kc_ecommerce_sandbox` BigQuery
   dataset, copies 4 tables from `bigquery-public-data.thelook_ecommerce` with
   1P descriptions, creates the global `metric-governance` `AspectType`,
   attaches 7 column-level `metric-governance` aspects (`3` certified, `4`
   restricted) to `order_items`, and creates the `schema-join` `EntryLink`
   between `order_items` and `products`.
3. `step3_search_entries.py` — Runs `search_entries` (`semantic_search=True`
   natural-language discovery vs. structured predicate query on `order_items`
   with `column:sale_price` and `aspect:...`) and verifies that `search_entries`
   returns lightweight entry pointers (`aspects: {}`).
4. `step4_lookup_entry.py` — Runs `lookup_entry` (`EntryView.BASIC` vs.
   `EntryView.CUSTOM` vs. `EntryView.CUSTOM` + `paths`) on `order_items` with
   `PROJECT_ID` request filtering and `PROJECT_NUMBER` key resolution to audit
   certified (`certified_for_ai=True`) and restricted (`certified_for_ai=False`)
   columns and persist them to `catalog_state.json`.
5. `step5_lookup_context_grounding.py` — Validates entry candidates, passes only
   `order_items` to `lookup_context` (`format="yaml"`, `context_budget="4000"`)
   to traverse the `schema-join` `EntryLink` to `products`, combines the 1P
   Knowledge Graph YAML with the 3P column governance rules from `lookup_entry`,
   and synthesizes and executes a grounded BigQuery SQL query using Gemini Flash
   (`google-genai`).
6. `cleanup.py` — Performs 1:1 reverse-dependency resource cleanup.
