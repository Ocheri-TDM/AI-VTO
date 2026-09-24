# Background workers

The existing PostgreSQL queue, independent supplier schedule, conditional supplier lease, bounded retries/backoff and browser/context reuse remain. Defaults: two global workers, one slot per supplier, two browser pages, hourly commercial refresh, daily discovery and ten-minute initial supplier stagger. Configure through the existing Stage 4 environment variables; no Redis or cloud queue is required.

Discovery now uses the supplier catalog graph. `CatalogRun` survives execution slices; `CatalogSeen` stages exact offer observations. Failed/interrupted work retains checkpoints. A running job with an expired lease is recovered by the next claimant. Existing jobs are not automatically treated as complete after an API restart.

Refresh reads known offer URLs, with Ucontay family URL batching. Existing good observations retain their timestamp while refresh is running; they naturally become stale if the job takes too long. A validated completed run publishes fresh supplier timestamps. The application never extends freshness by changing a timestamp without reading the supplier.

The background update callback no longer loops over every historic ResearchSession. Active clients synchronize on request/poll/SSE; background new matches are offered for acceptance. This avoids a page-by-page fanout across saved research history.

Anomaly runs preserve good observations and are visible in developer diagnostics. Cancellation is cooperative and the shared BrowserEngine is closed by the worker/application host. CAPTCHA is never bypassed and causes an hourly retry delay.

The local host still runs workers in-process. The persistent queue and supplier lease can be used by a separate host, but the global worker limit is per host. Same-query jobs are shared in single-user mode; cancelling a shared job is not a multi-tenant ownership model.
