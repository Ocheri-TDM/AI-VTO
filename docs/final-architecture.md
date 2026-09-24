# Research Platform — architecture

Supplier pages remain the evidence source. `SupplierProvider.catalog_navigation()` reads published navigation; `research_listing()` and `research_products()` extract listing and exact variant data. The background queue owns browser work. Ordinary chat endpoints use `IndexedResearchService` and PostgreSQL; the explicit `legacy=true` Stage 2 contract remains supported because it still has consumers and regression tests.

`BackgroundIndex` obtains supplier leases, commits checkpoints and stages observations in `CatalogSeen`. `CatalogSafety` checks completed runs before publishing changed observations and before reconciliation. New offer discoveries become lexically searchable immediately. No product-count truncation is applied. Time slices preserve pending URLs.

The global index stores product metadata, supplier offers and latest commercial observations. `ResearchSession` stores intent, the IDs of its base pool, currently valid IDs, pending new matches, view and history. For index sessions, full product DTOs are no longer written to the session JSON. Existing legacy research snapshots remain readable. History contains view state, not product copies.

Query flow: parse independent intent → SQL freshness/stock/price/category or Russian FTS → evidence-based category/material/color validation → full matching pool → ranking → paginated public DTO. Soft preferences reorder; hard constraints remain in the base intent. `Покажи всё` resets optional view refinements, not the original intent.

Background discoveries do not automatically expand an established research. New IDs appear in `pending_offer_ids`; accepting them merges the currently valid observations while preserving filters and selection. Live price/stock changes can invalidate existing offers without deleting research history.

SSE carries bounded invalidation/count/status deltas. The client fetches a bounded first page after invalidation. Product pages use a cursor tied to view version and ordered identity fingerprint; conflicting cursors return 409. This is not a fully SQL-backed streaming cursor: matching/ranking still materializes the pool in Python. See performance results and limitations.

Developer diagnostics live at `/dev/index`, backed by `/api/dev/index`, gated by `DEBUG_AGENT=true`. Main cards and production details exclude supplier internals. No auth, paid APIs, presentation engine, image generation or new external service was added.
