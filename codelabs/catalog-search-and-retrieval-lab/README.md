# Search, inspect, and ground metadata in Knowledge Catalog

Companion Python scripts for the **Search, inspect, and ground metadata in
Knowledge Catalog** Google Cloud Codelab (`catalog-search-and-retrieval`).

## Script sequence

1. `schemas.py` defines the environment validation, Pydantic `BaseModel`
   contracts (`ColumnGovernanceFinding`, `GovernanceAuditReport`, and
   `GroundedAgentDecision`), the `metric-governance` `AspectType` template, and
   dynamic General Availability (GA) Gemini Flash model discovery.
2. `step2_seed_environment.py` creates the `kc_ecommerce_sandbox` BigQuery
   dataset, copies four tables from `bigquery-public-data.thelook_ecommerce`
   with first-party descriptions, creates the global `metric-governance`
   `AspectType`, attaches seven column-level `metric-governance` aspects (three
   certified and four restricted) to `order_items`, and creates the
   `schema-join` `EntryLink` between `order_items` and `products`.
3. `step3_search_entries.py` runs `search_entries` with natural-language
   semantic search (`semantic_search=True`) and a structured predicate query on
   `order_items` (`column:sale_price` and `aspect:...`), verifying that
   `search_entries` omits aspect payloads (`aspects: {}`).
4. `step4_lookup_entry.py` runs `lookup_entry` across `EntryView.BASIC`,
   `EntryView.CUSTOM`, and `EntryView.CUSTOM` + `paths` on `order_items` to
   audit certified (`certified_for_ai=True`) and restricted
   (`certified_for_ai=False`) columns and save them to `catalog_state.json`.
5. `step5_lookup_context_grounding.py` pre-validates candidate entry paths,
   passes only `order_items` to `lookup_context` (`format="yaml"`,
   `context_budget="4000"`) to traverse the `schema-join` `EntryLink` to
   `products`, combines the Knowledge Graph YAML with the column governance
   rules from `lookup_entry`, and generates and executes a governed BigQuery
   SQL query using Gemini platform (`google-genai`).
6. `cleanup.py` deletes all created cloud resources and local state files in
   reverse dependency order.
