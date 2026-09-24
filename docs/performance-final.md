# Performance — evidence and limits

Final isolated repeat (`souvenir_final_large_index`, separated from restart fixtures): p50 **487.16 ms**, eight-sample maximum **969.85 ms**, local price filter **46.50 ms**, similarity **124.93 ms**, first-page/facets **111.84 ms**. Pool remains 12,500 offers; response 22,810 bytes; illustrative delta 113 bytes; session 340,268 bytes. An extra untimed query with tracemalloc measured **114.43 MB peak Python allocations**, not process RSS. Cold and warmed samples are retained in `large-index.json`; warmed maximum after the first sample was 589.22 ms, so warmed p95 <500 ms remains unproven. Synthetic clocks were reset only in this isolated fixture database for repeatability. Live timestamps were refreshed through suppliers.

Stage 4 live index HTTP replay measured 59–132 ms on its small populated index. Those historical measurements are preserved, not silently reused as final results. Final HTTP dialogues are in `.local/final/dialogues.json`; each response records timing, intent, counts and browser invocation. No synchronous browser is created by the acceptance application.

`scripts/benchmark_final_index.py` uses a **dedicated synthetic PostgreSQL database**, 10,000 indexed products and 25,000 offers. A pencil query keeps all **12,500 matches**. The response exposes 50 products and a continuation cursor. Session JSON is approximately 340 KB of IDs/state rather than 12,500 full product DTOs; first response is about 23 KB. History contains view state only.

The initial unoptimized full-pool query measured p50 ≈2213 ms, maximum ≈2304 ms. Avoiding per-product resolver creation and caching title/path classification reduced an isolated repeat to p50 ≈636 ms, with cold maximum ≈1644 ms. A subsequent run while browser/LLM/background work competed for resources measured p50 ≈1389 ms, maximum ≈1974 ms. These are eight-sample engineering measurements; the script's p95 is its conservative sample maximum, not a statistically strong percentile. Raw timings are preserved in `.local/final/large-index.json`.

The desired warmed p95 <500 ms is **not demonstrated at 12,500 matching offers**. DTO hydration and full Python pool/ranking remain the dominant scalability limitation. Pagination bounds network payload but does not eliminate pool materialization. A 50,000/100,000 stress run has not been performed. No production SLA or broad concurrency capacity is claimed.

Supplier profiling from Stage 4 remains in `performance-stage4.md`; final sample/navigation timings are in the final provider artifacts. Browser work remains asynchronous relative to chat, with one process and bounded supplier contexts. Index-update callbacks no longer hydrate every historic session per supplier page.

SSE events now carry a small count/status/version/observation fingerprint delta. They trigger a bounded first-page fetch rather than carrying the complete pool. Facets still scan the materialized eligible pool. Cursor generation currently fingerprints the entire ordered pool; this is conservative but not a database keyset cursor.

Local embedding experiment loaded a real 768-dimensional model, but its relevance result did not justify production activation. Its initial batch time includes model loading and must not be presented as warmed hybrid-query latency.
