# Supplier-first catalog traversal

Discovery no longer enumerates application taxonomy seeds. It observes supplier navigation roots and follows same-host published catalog/category/collection links. Taxonomy classification runs after extraction; an unmapped product is still indexed.

The provider-specific path shapes are in `providers/catalog_navigation.py`, based on saved Chromium navigation evidence in `.local/final/catalogs`. They do not generate product URLs or arbitrary query terms. Supplier listing adapters retain their verified pagination, structured data and stock extraction. Targeted user research still legitimately uses search terms.

Each persistent `CatalogSyncJob.checkpoint` contains observed roots, branches, parent URLs, navigation status, listing cursor, pending/visited product references, next page, totals and errors. Pre-final taxonomy query checkpoints are retained under `legacy_query_coverage` and a supplier-first traversal is started. Catalog runs and per-offer seen/staged facts are relational tables.

COMPLETE requires observed nonempty roots, every branch complete, exhausted pagination, no pending/failed URLs and no branch error. A worker returning successfully is insufficient. Time budgets yield PENDING with TIME_BUDGET; the cursor is retained. Supplier errors cannot establish completeness. One failed branch is recorded and other branches may proceed. CAPTCHA stops that supplier with backoff.

Execution slices are not catalog limits. A resumed run reuses its run ID and current page. Offer IDs and seen rows are upserted. The process-kill test resumes at product 10 after the first ten were committed, with 20 unique offers in total; its supplier is synthetic and its database is real PostgreSQL.

Reconciliation is performed only after a complete, non-quarantined discovery: one missing run → NOT_SEEN; two → SUSPECTED_REMOVED; three → REMOVED. Offers remain physically present. A run losing more than half of at least 20 previous offers is quarantined as MASS_REMOVAL. Definitive 404 currently remains an error/stale observation rather than independently removing an offer.

Anomaly checks cover price loss/zero, currency changes, unknown stock replacing known stock and at least 80% stock collapse over ten previously positive offers. Existing observations are staged until run validation. New discoveries can appear before completion. This conservative policy can quarantine legitimate bulk changes; diagnostics expose the reason. It does not silently approve suspicious runs.

Coverage is only the actually observed public navigation graph. Navigation-only/configurator pages, supplier timeouts and changing layouts can leave unresolved branches. The current live evidence does **not** establish full catalog coverage for any supplier.
# Execution fairness

When a slice is cancelled by its execution timeout, the current branch keeps its cursor and moves to the end of the persisted pending order. The next slice visits another branch. This prevents one slow navigation/listing from consuming every slice indefinitely. Restart after a hard process kill resumes from the last committed cursor; in-flight extraction may repeat idempotently.
