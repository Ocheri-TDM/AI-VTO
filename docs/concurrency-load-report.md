# Multi-user concurrency, isolation and load report

Date: 2026-10-11 (Asia/Yekaterinburg). Target: the locally running development stack at
`127.0.0.1:8000`/`3000`, backed by the populated PostgreSQL catalog. No application code was
changed as part of this audit.

## Executive result

The bounded 10-user run completed 65 measured HTTP operations: 64 passed and one failed. All
cross-user ownership checks passed, and the 1/5/10-user SIMPLE stages and five concurrent
COMPOSITE creations returned successful responses. The system does **not** meet the requested
SIMPLE p95 target at concurrency 5 or 10. A 20-user stage was therefore deliberately not run.

The one functional failure was a `503` during the final COMPOSITE -> SIMPLE transition in the
same-project sequence. The public exception handler identified it as a SQLAlchemy/database
error but removes the concrete exception from the HTTP response. Because the API process had
no persisted log sink, the exact SQLSTATE could not be recovered after the response. This is a
confirmed failure with incomplete server-side diagnostics, not a pass.

## Environment and safety

- API: one Uvicorn process/worker (`python -m app`, PID 22396 during the run).
- Database: PostgreSQL on the existing local development instance; catalog access was read-only.
- LLM: Ollama was running, but the measured deterministic SIMPLE requests produced no observable
  Ollama CPU load.
- Load data: ten users and ten projects with random `codex-load-<run>-uNN` names.
- Cleanup: all ten projects returned HTTP 204 on deletion; exactly ten prefixed users were then
  deleted. A failed preliminary run was also cleaned: ten prefixed users found, ten deleted.
- Catalog, SupplierOffer identities, observations, real users/projects, research history, jobs,
  checkpoints, and crawler state were not deleted or reset.
- The run used the existing background scheduler. No refresh job was manually inserted, cancelled,
  or deleted.

This was a bounded test against the current development contour rather than a separately cloned
database. It therefore proves API isolation under the measured workload, but it is not a production
capacity certification.

## Architecture audit

### ASGI and database

The Windows entry point creates a single Uvicorn server without `workers` or reload. This preserves
the Proactor event loop needed by Playwright, but one event loop is also the API's only scheduling
lane. Blocking CPU work or synchronous library work can delay every request.

`create_async_engine(database_url, pool_pre_ping=True)` uses SQLAlchemy defaults: pool size 5,
max overflow 10, and the default pool timeout. Pool size, overflow, checkout wait, and SQL timing
are not exposed in application metrics. After the run PostgreSQL reported one active and five idle
connections, zero ungranted locks, and `max_connections=100`.

Authentication performs a database lookup for every protected request. Service methods generally
use short `async with sessions()`/`sessions.begin()` scopes. Ownership predicates consistently join
the requested resource to its project/user and intentionally return 404 for foreign resources.

### Workspace transitions and shared state

Universal project transitions take both an in-process `asyncio.Lock` keyed by project ID and a
PostgreSQL advisory lock (`pg_advisory_lock(hashtext(project_id))`). This serializes one project's
read/route/mutate/activate sequence across processes. Different projects remain concurrent.

The in-memory lock dictionaries, intent LRU cache, active task dictionaries, and background owner ID
are process-local. They are safe in the observed single-process deployment; the advisory lock is the
cross-process protection. Multi-worker deployment would duplicate background schedulers and caches
unless separately coordinated.

SIMPLE research and COMPOSITE build records are persisted. `Project.active_query_id` is the server
source of truth. The observed 503 occurred after a COMPOSITE build when `create_standard_query`
started a new research and then archived the previous active query/assigned a new active query. The
failure boundary is inside that database-backed transition, but the current generic 503 handler does
not retain enough evidence to attribute it to pool checkout, transaction state, or a particular SQL
constraint.

### SSE and frontend

Research SSE checks ownership before streaming and sends research identity/version-derived state.
The React hook also checks the research ID and refuses revision regressions. Hard-request SSE checks
ownership and polls the active hard request; when another hard request becomes active it emits that
active object and closes. The hard-workspace component binds the stream to `active.id` and disposes
it when dependencies change.

The Next.js layer uses an internal rewrite to the API and sends cookies with `credentials: include`.
There is no explicit proxy timeout, SSE buffering policy in the Next config, or client retry/backoff
policy. Research EventSource reconnect uses browser defaults. The hard-request stream has no
heartbeat; research streams also emit only on changes and completion. Long silent intervals can be
closed by intermediaries.

### Background work and LLM

The background index starts a scheduler, a reserved catalog lane, general lanes, a targeted-research
lane, and a refresh lane. Jobs are durable/leased in PostgreSQL, while worker tasks are process-local.
Interactive research uses semaphores; COMPOSITE retrieval has a four-way semaphore. The local LLM
provider creates a new `httpx.AsyncClient` per completion and has no application-level queue or
semaphore, so ambiguous concurrent requests can reach Ollama concurrently and queue invisibly there.

## Measured scenarios

| Scenario | Requests | Passed | Failed | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SIMPLE baseline | 1 | 1 | 0 | 1,780.08 | 1,780.08 | 1,780.08 |
| SIMPLE concurrency 1 | 1 | 1 | 0 | 2,954.74 | 2,954.74 | 2,954.74 |
| SIMPLE concurrency 5 | 5 | 5 | 0 | 4,059.70 | 5,716.48 | 5,716.48 |
| SIMPLE concurrency 10 | 10 | 10 | 0 | 8,749.47 | 10,108.54 | 10,108.54 |
| Isolation + pagination | 20 | 20 | 0 | 60.39 | 1,369.29 | 1,466.14 |
| COMPOSITE concurrency 5 | 5 | 5 | 0 | 3,894.01 | 5,997.59 | 5,997.59 |
| Mixed workload | 10 | 10 | 0 | 8,343.72 | 9,290.69 | 9,290.69 |
| Same-project transition chain | 5 | 4 | 1 | 1,732.93 | 2,454.95 | 2,454.95 |
| Auth/ownership | 5 | 5 | 0 | 43.13 | 1,064.07 | 1,064.07 |
| Reload/history/Undo | 3 | 3 | 0 | 59.56 | 3,044.82 | 3,044.82 |

The SIMPLE wall times were 2,956.95 ms (1), 5,718.77 ms (5), and 10,110.77 ms (10), corresponding
to approximately 0.34, 0.87, and 0.99 completed requests/second. Throughput nearly plateaus between
five and ten while latency roughly doubles, indicating saturation of a shared lane rather than
linear scaling. Percentiles on very small samples are nearest-rank descriptive values, not stable
capacity estimates.

The ten SIMPLE subjects were pens, pencils, thermomugs, notebooks, powerbanks, backpacks, umbrellas,
flash drives, T-shirts, and shoppers. Each actor retained its own cookie jar. Query history contained
no foreign project ID, pages never exceeded 100 products, and foreign project/research/hard-request/
SSE access returned 403/404. An unauthenticated cookie jar received 401.

Five different two-category COMPOSITE requests were issued concurrently. The mixed stage contained
three SIMPLE requests, three COMPOSITE requests, two follow-up refinements, one build/rebuild lane,
and one history lane. All ten returned accepted responses. The same-project route chain validated
SIMPLE, refinement, SIMPLE replacement, and COMPOSITE before the final SIMPLE replacement returned
503.

## Targets

| Target | Result |
|---|---|
| SIMPLE p95 <= 3 s | PASS at c1 (2.95 s); FAIL at c5 (5.72 s) and c10 (10.11 s) |
| Pagination <= 1 s | FAIL overall: p95 1.37 s; most requests were fast (p50 60 ms) |
| Non-LLM follow-up <= 3 s | PASS for successful chain calls; one transition failed with 503 |
| COMPOSITE baseline | 3.89 s p50 / 6.00 s p95 at concurrency 5 |
| Error rate | 1/65 = 1.54% overall; 0% in staged SIMPLE and COMPOSITE batches |

## Bottlenecks and risks

1. A single ASGI worker and event loop carry API traffic and coordinate in-process work. At c10,
   throughput plateaus while per-request latency grows.
2. The database pool is implicit and uninstrumented. Authentication adds a DB checkout to every API
   operation. Pool checkout wait cannot be separated from SQL execution with current telemetry.
3. Response construction calculates facets, grouping, ranking, and serializes up to 100 products in
   the request path. The pagination tail above one second warrants SQL/serialization tracing.
4. Background workers share the same process/event loop and database pool with user traffic.
5. Ollama has no bounded app-side queue, reuseable HTTP client, or queue wait metric.
6. SSE has no explicit heartbeat for current research/hard-request streams and no observable
   reconnect counter. Proxy behavior on long silent streams remains uncertain.
7. The generic database exception handler turns the confirmed transition failure into an opaque 503;
   no request ID or SQLSTATE is returned, and this run had no persisted API log for correlation.

## Recovery and race coverage

Reloading the chat and query history passed. One research Undo request returned an allowed success or
version-conflict response, preserving optimistic concurrency semantics. Existing unit tests cover
simultaneous search-job sharing, chat isolation, repeated semantic Undo, workspace version conflicts,
and SSE payload completion.

The following items remain **NOT TESTED** in live HTTP/browser form:

- two simultaneous mutations of the same project and deterministic last-writer ordering;
- authenticated own-resource SSE reconnect/heartbeat during a forced background publication;
- user project switch while an SSE event is in flight;
- browser F5 preserving a live COMPOSITE Case View;
- HTTP retry idempotency for creation endpoints (they do not expose an idempotency key);
- a deliberately triggered refresh during the peak stage;
- real CPU/RAM time series, pool checkout wait, per-SQL timing, event-loop lag, Qwen/Ollama queue
  wait, and per-worker utilization (these metrics are not instrumented);
- concurrency 20, skipped because c10 exceeded the latency target and produced near-flat throughput.

## Reproduction

The durable harness is `apps/api/tests/performance/concurrency_load.py`. From `apps/api`, run:

```powershell
..\..\.venv\Scripts\python.exe tests\performance\concurrency_load.py `
  --base-url http://127.0.0.1:8000
```

It deliberately stops at ten users, records each status/latency, validates isolation and ownership,
and cleans only resources with its generated prefix. Its JSON output is temporary evidence and is
removed after this report is produced.

## Conclusion

Ten isolated users can issue independent SIMPLE and COMPOSITE requests without observed data or
cookie leakage, but the current stack is not ready to claim the requested concurrency target. The
c10 latency is above the target, throughput is saturated, one critical workspace transition returned
503, and several SSE/browser/race cases remain unverified. The safe outcome is a measured partial
pass, not `FULL PASS`.
