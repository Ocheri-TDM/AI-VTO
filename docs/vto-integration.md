# VTO integration — 2026-10-30

**Последующее изменение UI:** SIMPLE и COMPOSITE заменены одной товарной сеткой;
автоматическая сборка больше не используется. Приёмка COMPOSITE ниже относится
к моменту перехода на VTO. Текущий путь описан в [unified-product-grid.md](unified-product-grid.md).

**Status: PARTIAL.** The application now uses VTO exclusively. The full catalog
scan is asynchronous and remains in progress; no claim of complete coverage is
made. Counts in the snapshot below describe the new database at measurement time.

## Source contract verified through real HTTP

| Route | Result |
|---|---|
| `/wp-json/wc/store/products?per_page=20&page=1` | 200, JSON, product families, prices, images, categories, variations, totals |
| `/wp-json/wc/store/products?per_page=20&page=2` | 200; distinct second page |
| `/wp-json/wc/store/v1/products` | 404 `rest_no_route` on this installation |
| `/wp-json/wc/store/products/1521733` | 200, real Melango parent and variation IDs |
| `/wp-json/wc/store/products?type=variation&per_page=1` | 200; parent relationship, variant price/image; numeric stock absent |
| `/wp-json/wc/store/products?type=variation&parent=333292&per_page=2` | 200; real parent filter returns IDs 333296 and 333294 |
| `/product/ryukzak-meshok-melango/` | 200; published `data-product_variations` with individual stock, price and image |
| `add-to-cart-variation.min.js?ver=5.3.0` | Public script explicitly uses `get_variation` |
| `POST /?wc-ajax=get_variation` for product 1961209, navy/A5 | 200; ID 1961214, SKU 3-516.08, published quantity 12 |

Initial collection response declared 19,031 parents and 952 pages at size 20;
later responses declared 19,030. These are observations, not constants in code.
Two pages at size 100 returned 100 distinct IDs each with no overlap, in 12.00s
and 13.59s. The local deployment was then configured to size 100, without
increasing concurrency. Explicit `page` and `per_page` are always sent; `Link`
is not trusted as a traversal cursor.

Some `orderby=id&order=asc` pages returned HTTP 502. Verified
`orderby=date&order=desc` traversal replaces that ordering. Changing ordering or
page size restarts collection traversal while retaining already ingested IDs;
duplicates are prevented by external IDs. Completion requires unique processed
parent count and declared totals at both ends to agree, with no pending errors.
A subsequent API-only ID enumeration must match the complete processed parent
ID set; equal counts with replaced IDs do not qualify as full coverage.

No administrative WooCommerce credentials, cart changes, orders, browser,
CAPTCHA bypass, proxy rotation or JavaScript execution are used.

## Storage and provenance

- `IndexedProduct.id = vto:parent:<product_id>` identifies one family.
- `SupplierOffer.id = vto:<variation_id>` identifies a real variant. A simple
  product instead uses its real product ID and has no fabricated variation ID.
- All original VTO categories remain metadata. Known manufacturer roots are
  excluded from product-type concepts; existing semantic resolution is reused.
- Parent title, brand, material and dimensions are inherited from public HTML.
  Variant attributes take precedence. Color/size combinations come from actual
  published variation IDs; no Cartesian product is invented.
- Store API minor units are converted with `Decimal`. HTML variation
  `display_price` is already a major-unit price. Exact original values remain in
  observation payloads; displayed KZT uses the existing half-up integer rounding.
- VTO sometimes serializes its images as an object with non-zero numeric keys;
  normalization supports both that observed form and arrays.
- A live SQL failure exposed another normalization defect: concatenating all
  supplier categories created a ProductConcept name longer than its 300-character
  column. Categories are now individual concepts; the complete category list is
  retained as metadata. Per-card data errors are checkpointed without blocking
  unrelated cards. The failed page was resumed after this repair.
- Missing variant images use a placeholder, without copying another color's
  parent photograph. Only `vto.kz/wp-content/uploads/**` and the observed
  `s.a-5.ru` CDN are configured in Next.js.
- Query attributes did not produce a selected option in the tested VTO HTML.
  Links therefore open the parent page. The app retains variant color and SKU;
  SKU is displayed in details.

Numeric availability comes from the variant's explicit `stock_quantity` when
published, otherwise its own `availability_html`. `max_qty`, purchasing limits
and `is_in_stock=true` do not create quantities. Zero is stored as zero;
missing quantities stay NULL. Preorder is not ON_HAND. Parent-shared stock is
recorded as shared evidence without assigning it to every child or adding it
multiple times. UI says “по данным VTO”, rather than claiming reserves were
deducted. Existing LAST_KNOWN_STOCK_ALLOWED remains active with original dates.

An empty parent `variations` list or an empty HTML form is not proof that no
children exist. The adapter also enumerates Store API `type=variation&parent=ID`
through its declared pages and verifies every returned parent relationship.
Published HTML/AJAX evidence takes precedence for a matching real variant ID;
other real API children retain their own prices, color, SKU and images with
NULL quantity when the API does not publish a number. For STOCKHOLM this was
verified live: 333296 / 56-0819659 / серый and 333294 / 56-0819658 / чёрный,
each at 10,733 KZT, different images, both with unknown numeric quantity.

The A5 Trend card publishes a wildcard size for one variant but has exactly
one form size, A5. Public `get_variation` confirmed that combination and ID.
Unique published choices may therefore be inherited with provenance; ambiguous
wildcards with several choices remain unresolved and never generate products.

## Melango live result

Parent: **1521733**, “Рюкзак-мешок Melango”. Brand: **Molti**.
Material: **полиэстер, 300D**. Public attributes also contain size “Без размера”
and dimensions “33×44 см”. All four public IDs were obtained and imported.

| Variation ID | SKU | Color | KZT | Published quantity | Scope | Image suffix |
|---|---|---|---:|---:|---|---|
| 1823107 | GF-12449.30 | чёрный | 2084 | 1739 | VARIANT | `12449.30_3_1000x1000.jpg` |
| 1823108 | GF-12449.10 | серый | 2084 | 2074 | VARIANT | `12449.10_3_1000x1000.jpg` |
| 1823109 | GF-12449.40 | синий | 2084 | 2349 | VARIANT | `12449.40_4_1000x1000.jpg` |
| 1823110 | GF-12449.50 | красный | 2084 | 977 | VARIANT | `12449.50_3_1000x1000.jpg` |

Fields above were observed on VTO on this run. The blue quantity is not a
permanent test expectation. Repeating ingest produced 0 changed products and
4 deduplicated observations: **1 family, 4 offers, 4 availability records**.
A regression test confirms updating one variant to zero leaves its siblings
unchanged. Composite DTO now reads name, color, SKU, image, price and stock from
the specific offer/observation instead of the last-updated family name.

## One-time database transition

Verified target: `127.0.0.1:55432`, database `souvenir`, owner `souvenir`.
Original database size was 818,820,787 bytes. Application writers were stopped.
Live variant extraction and two idempotent imports into an isolated SQLite
database passed before reset.

Backup: `C:\Users\Ocheri\AI-VTO-backups\souvenir-before-vto-20261030T100651Z.dump`.
`pg_dump` exited successfully; `pg_restore --list` verified readable structure,
including table data and SupplierOffer. The dump is outside Git and cleanup.
No claim of a full restore rehearsal is made.

Only the verified `souvenir` database was dropped/recreated. The server,
cluster, roles and other databases were retained. All Alembic migrations were
applied. The historical disabled legacy owner created by an old migration was
removed from the empty new database. Old suppliers, observations, jobs, users,
projects and research were not migrated. A fresh account was registered with a
random password; local credentials are in
`C:\Users\Ocheri\AI-VTO-backups\vto-account.txt`.

`scripts/reset_vto_database.py --execute-reset souvenir` is an explicit guarded
one-time command, not startup logic. Its receipt in `.local` prevents repeating
this transition. PostgreSQL dump utilities are in `.local/postgres-tools`.

## Background behavior

The existing queue, claims, supplier lease, backoff and checkpoints are reused.
HTTP connection pooling and two request slots bound source concurrency; a
request-start delay applies. 429/5xx/timeouts have bounded retries. Each product
batch commits before its checkpoint advances; no DB transaction waits on HTTP.
Individual failed cards remain durable retry records while other cards proceed.
After a card retry budget is exhausted, a failed scan waits for the next daily
schedule. Explicit manual retry retains its cursor and retries failed records.

Ordinary requests query PostgreSQL. The full scan and availability refresh run
separately. Incomplete responses do not clear the index or overwrite old
observations. Zero-stock updates are real observations, not parser failures.
Daily intervals and HTTP page size/delay are configurable. Search pagination
remains 100; backend candidate pools are retained in full.

## Verification

- Real HTTP: Melango variants, BLUE filter, quantity >=300, selection persistence
  and Undo were checked. An explicit new quantity search preserved quantity 300.
- Composite was awaited through terminal state and its persisted cases read.
  One run assembled 1 of 2 (`COMPLETED_PARTIAL`); a concurrent one-set request
  completed `COMPLETED_FULL`. A pen request with no indexed writing pens
  correctly ended NO_MATCH; this was not called successful assembly.
- Four concurrent users during an active VTO sync: SIMPLE latencies 5.469s,
  6.578s, 5.500s; COMPOSITE submission 0.734s and final COMPLETED_FULL. All four
  foreign-project access checks returned 404. No HTTP errors occurred.
- Melango cold parsing involved local Qwen (12–15s); a repeated deterministic
  path was measured at 0.047s. This is a small smoke sample, not load-test p95.
- ESLint, TypeScript and Next production build passed.
- Browser rendering: **NOT TESTED**; Chromium was not installed again.
- Pool wait and broad load capacity: **NOT MEASURED** in this smoke run.

## Cleanup and remaining gaps

Six old adapters, their provider-only fixtures/tests, the browser engine,
Playwright/pyee dependencies and obsolete browser scripts were removed. The
project Chromium runtime/profile was removed. User Chrome/Edge and shared
browser installations were not touched. PostgreSQL and Ollama components remain.
Business regression suites and VTO tests are retained. Disposable HTTP users
and projects were deleted by exact recorded IDs.

The full import is **not complete**. Publicly missing variation combinations
are never invented; failures remain visible in the durable job. No source
coverage percentage is claimed from an unfinished scan. Color+size and
parent-shared inventory are tested with fixtures; only types actually present
in the live snapshot below are asserted as live. Model-name text is not always
an exclusive filter: category retrieval can also return another backpack.

## Measured database snapshot

Measured with read-only SQL on **2026-10-30 11:25:11 UTC** (16:25:11 local).
These are observations during the running scan, not the final catalog size.

| Metric | Value |
|---|---:|
| SupplierOffer, all from VTO | 3,122 |
| Indexed product families with offers | 1,117 |
| Real variation offers | 2,617 |
| Simple-product offers | 505 |
| Offers with exact KZT price | 3,122 / 3,122 |
| Offers with image | 3,113 / 3,122 |
| Offers with recognized normalized color | 2,621 / 3,122 |
| Offers with published positive numeric quantity | 2,523 |
| Offers with known numeric zero | 113 |
| Offers with unknown numeric quantity | 486 |
| Offers from another supplier | 0 |
| Offer IDs inconsistent with source product/variation ID | 0 |

Full-scan job: `3af11531-88ce-4c5f-b33d-869c4045d606`, **RUNNING**, active
error NULL, parser `vto-http-v3`. Checkpoint: 1,145 processed parents, next
collection page 18 at size 100, 30 pending cards in the current page. The latest
source response declared 19,025 parents / 191 pages. During this run VTO's
declared total changed; complete coverage therefore requires the final ID-set
verification, not division of these counters.

Four cards remain failed/retryable: `334080`, `334150`, `334702`, `335344`.
Their public responses did not confirm sellable variations. Each currently has
one v3 attempt with `VTO_PARSING_ERROR`; no synthetic child or parent offer was
created to disguise this gap. Older failures for STOCKHOLM and other families
were recovered by the verified parent-filter API fallback.

Persisted job metrics at this snapshot: 1,680 HTTP requests, 3 HTTP errors,
43 failed card attempts across parser versions, and 2,570,217 ms cumulative
slice time (42m50s). Active-slice HTTP counters/time are checkpointed at slice
completion, so these lag the current running work. Collection requests include
replayed pages after ordering/page-size changes. Twenty manually verified seed
offers are outside the job's 3,102-offer counter. Parent and variant totals are
not interchangeable; 34 processed parents had no sellable offers.

An initial-import defect joined unrelated category labels into one concept.
The importer now retains individual source categories, and ingest replaces
obsolete concept links and recomputes their counts. A provenance-based local
repair reindexed 943 affected offers from their stored VTO category records,
retaining commercial values and original observation times. Read-only SQL then
confirmed zero active joined-path concepts. An explicit regression covers
category replacement, including the old concept's zero reference count.

## Acceptance outcome

| Check | Actual result | Status |
|---|---|---|
| Fresh schema | Alembic head 0020 on recreated PostgreSQL | PASS |
| Source and isolated repeated ingest | Real Melango; second ingest creates no duplicate offers | PASS |
| HTTP SIMPLE after final restart | 4 Melango IDs, 10.453s | PASS |
| HTTP BLUE view filter | 1 card, `vto:1823109` | PASS |
| HTTP quantity filter | Effective quantity 300; every returned quantity >=300 | PASS |
| HTTP selection persistence / Undo | Read-back selected blue ID; Undo clears selection | PASS |
| HTTP COMPOSITE | 2 requested / 2 assembled, `COMPLETED_FULL`; persisted VTO candidates read | PASS |
| Concurrent users during sync | Four requests, no HTTP failures; foreign projects return 404 | PASS |
| Restart and health | API, frontend and frontend API proxy HTTP 200; only supplier `vto` | PASS |
| VTO-specific regression suite | 33 tests | PASS |
| Full backend regression suite | 305 passed, 1 deselected; 128.15s | PASS |
| Ruff | API app/tests and all three permanent VTO scripts | PASS |
| Frontend ESLint / TypeScript / production build | Completed successfully | PASS |
| Browser DOM / interactive visual flows | Chromium was not used | NOT TESTED |
| Full catalog completion | Scan running; outstanding cards and final verification remain | PARTIAL |
| Daily refresh over a real 24-hour interval | Scheduler policy and queue tested; no 24-hour observation | NOT TESTED |

Four Melango photos were downloaded and visually inspected: their black, grey,
blue and red products match their own variant IDs. This checks the actual image
content, not rendered browser layout. Color plus A5 size was confirmed through
the public variation response for 1961214. Price differences, zero quantity,
unknown quantity and missing images are present in the live index; for example,
STOCKHOLM quantities remain unknown and `vto:2033580` has no image. Parent-shared
stock behavior was exercised with fixtures, not claimed as a live discovery.

The API was restarted after the final indexing correction. PostgreSQL, Ollama,
API and frontend remain running; the existing sole supplier worker continues
the checkpointed scan. `scripts/sync_vto.py` can monitor/resume through the same
queue and supplier lease. Diagnostics expose `catalog_import` with processed,
declared, pending, failed and coverage-complete fields. A future terminal
`COMPLETED` is valid only after the ID verification succeeds.

Temporary acceptance JSON, downloaded verification photos and stopped CLI-sync
logs were removed after recording this evidence. Permanent tests/fixtures,
scripts, active service logs, the reset receipt, new database and old backup
were retained. Exact-ID HTTP cleanup left zero disposable acceptance accounts.
