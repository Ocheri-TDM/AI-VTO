# Search → Research: implementation plan

The previous Stage 3 selection-workspace plan is superseded. Its working UI,
chat persistence, selection controls and accessibility remain reusable components.

## Boundaries

- Keep `SearchService` and its short-lived search API compatible.
- Add `ResearchService` above supplier adapters. Research uses resumable catalog
  traversal, not `max_pages_per_query` or `max_products_per_query`.
- Keep site HTML/API extraction inside each provider. A traversal checkpoint
  records the current listing URL/page and pending product URLs. Commit observations
  and checkpoint together before acknowledging progress.
- A durable research belongs to a project/chat. Its observations become stale
  after 60 minutes; neither research nor history is deleted by cache cleanup.
- Jobs run independently of HTTP/SSE consumers. Cancellation stops a job while
  retaining committed products and cursors. Restart recovery marks interrupted
  work resumable. Only one worker may own a research at a time.
- Coverage is a graph of category → supplier → query/route. COMPLETE requires
  verified natural pagination exhaustion with no unprocessed products/errors.
  Time/route budgets produce LIMITED, never a false COMPLETE.
- Category and exact stock are hard constraints. Broad blue-family matching is
  the default; strict color is explicit. Every accepted offer retains evidence.
- Refinement changes a view over the durable pool. Similarity and facets use
  supplier-derived attributes. Model decisions cannot introduce product facts,
  URLs or arbitrary executable tools.

## Implementation order

1. Browser reconnaissance and six provider notes.
2. Typed research intent, planner, coverage, cursor and budget models.
3. Resumable provider traversal, preserving the existing provider interface.
4. Durable repository/migration and independent research worker.
5. Local refinement, similarity, facets, grouping and structured model decisions.
6. Research API/SSE and integration with the existing workspace.
7. Regression tests, real six-source acceptance, build and visual checks.

Reconnaissance artifacts are local and ignored under `.local/research/six`.
Implementation and live verification status must be reported separately.
