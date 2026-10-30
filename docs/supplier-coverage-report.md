# Supplier catalog coverage and stock recovery

Audit date: 2026-10-16.  The protected PostgreSQL database, catalog, users,
projects, jobs, and checkpoints were retained.  All catalog counts below are
read from the live PostgreSQL index.  They describe the observed index, not an
assertion that the corresponding supplier's complete public catalog is known.

## Observed coverage

| Supplier | Active offers | Detail | Price | Image | Color | Offers with stock | No availability observation | State |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Oasis | 118 | 100% | 100% | 100% | 100% | 118 | 0 | Access limited / circuit protection preserved |
| Gifts | 8,191 | 100% | 100% | 100% | 100% | 5,265 | 3,973 | Discovery has pending branches |
| Ucontay | 25,863 | 100% | 100% | 98.49% | 99.17% | 9,504 | 17,288 | Active discovery lease; no second crawler started |
| Portobello | 741 | 100% | 100% | 100% | 100% | 616 | 125 | Partial: parser/time-budget checkpoints retained |
| HappyGifts | 3,049 | 100% | 100% | 100% | 100% | 1,408 | 1,869 | Targeted stock recovery completed |
| ArteGifts | 4,070 | 100% | 100% | 100% | 100% | 3,930 | 138 | Partial: parser/time-budget checkpoints retained |

`Detail`, `Price`, `Image`, and `Color` are `covered active offers / active
offers`.  `Offers with stock` means a current `ProductObservation.stock` value
exists.  A supplier's actual total catalog size is not available from its
public crawl state, so true supplier discovery coverage is **NOT MEASURABLE**;
the table deliberately does not label these ratios as 100% supplier coverage.

The diagnostic endpoint now additionally exposes the same observed ratios as
`observed_detail_coverage`, `observed_price_coverage`,
`observed_image_coverage`, `observed_color_coverage`, and
`observed_stock_coverage`, plus `offers_with_stock` and
`offers_without_availability`.

## Checkpoint findings

- **Ucontay** had a non-expired active discovery lease.  Its 72 roots and 347
  known branches include completed, pending, and failed branches.  It was left
  to resume through its own checkpoint, preventing a conflicting worker.
- **Oasis** was unavailable through its existing circuit-breaker path after
  supplier HTTP failures.  No bypass, proxy rotation, or CAPTCHA workaround
  was attempted.
- **Gifts**, **Portobello**, and **ArteGifts** retain their incomplete branches
  and parser/time-budget diagnostics.  A worker exit is not treated as catalog
  completeness.
- **HappyGifts** had product cards indexed without availability observations.

## Confirmed HappyGifts defect and recovery

The provider previously scanned all availability tables in the page DOM.  A
HappyGifts product page keeps tables for inactive colour variants in hidden
DOM nodes.  The old parser could attach a hidden variant's stock to the active
variant and omitted a zero-stock observation entirely.

The provider now selects the visible availability table whose variant id
matches the active colour, writes a zero value as a real `ON_HAND` observation
with `free=0`, and uses parser version `happygifts-availability-v4`.  The
change was checked with a permanent contract test for both a positive active
variant and a zero-stock active variant.

Targeted, rate-limited recovery used the existing HappyGifts adapter and the
idempotent index ingest path for exactly four already-indexed SOCKS offers.
No discovery job was started and no unrelated offer was updated.

| Offer | Free | Total | Availability |
|---|---:|---:|---|
| `happygifts:648120016` | 1 | 1 | AVAILABLE_NOW |
| `happygifts:68803733` | 4 | 4 | AVAILABLE_NOW |
| `happygifts:68803735` | 0 | 0 | UNAVAILABLE_NOW |
| `happygifts:68803737` | 0 | 0 | UNAVAILABLE_NOW |

Recovery result: **4 observations created or refreshed**.  The SOCKS position
in the 30-set request still has no offer with free stock of at least 300, so
the requested assembly cannot be produced.  This is now a demonstrated stock
shortage rather than an unknown-stock inference.

## Red T-shirt investigation

The strict normalized `RED` pool is present in the index.  Before any recovery
it included 1 ArteGifts, 82 Gifts, 28 HappyGifts, 5 Portobello, and 295 Ucontay
red variants; the live SQL audit found 66 Ucontay/ArteGifts/Portobello variants
with variant-level free stock of at least 200.

The previous empty composite position was caused by category resolution:
`ResearchPlanner.parse("tshirt")` did not recognize its own canonical node id
and generated a dynamic category.  The category matcher then rejected real
`tshirt` offers after lexical retrieval.  `UniversalCategoryResolver` now
accepts exact canonical node ids, with a regression test covering every seeded
canonical id.  This is an index-query correctness repair; it did not modify
stock policy or synthesize inventory.

Colour values currently normalize supplier red shade labels to `RED`; strict
separation of red from burgundy/maroon requires supplier-specific shade data
that is not consistently present and remains a documented limitation.

## Validation and limitations

- Focused regression suite: **74 passed**.
- Ruff on changed provider, taxonomy, and tests: **passed**.
- Live HTTP: purpose discovery was a standalone `SIMPLE` workspace and did not
  inherit a prior composite.  The four-category socks request routed to
  `COMPOSITE`; its live result was `NO_MATCH` because the recovered SOCKS
  quantities are below 300.
- The initial API restart failed only because the local PostgreSQL service had
  been stopped; PostgreSQL, Ollama, and the API were restarted before live
  validation.
- Browser rendering, broad six-supplier recovery, and a full concurrent-load
  run are **NOT TESTED in this audit**.  Their absence is not presented as a
  pass.
