# Composite query regression report

Date: 2026-10-13  
Catalog snapshot: 41,987 active `SupplierOffer` rows in the existing PostgreSQL database.  
Status: **PASS for intent equivalence and state isolation; catalog-proven `NO_MATCH` for A/B/C/D.**

## Root causes and fixes

### BUG-01 — `шопперов` and `шоперов` diverged

The first divergence was **Product Concept Resolution**. The legacy resolver recognized only the doubled-consonant spelling and mapped it to the broad `bag` concept; the single-consonant spelling disappeared before the effective intent was built. SQL, allocator, persistence, HTTP and React merely propagated that difference.

The fix introduces the narrow existing-taxonomy concept `shopperbag`, with a morphological pattern that accepts `шопер/шоппер` and their inflections. Generic `bag` evidence is removed when the narrow concept is present. `tote bag` and `сумка для покупок` remain `RELATED`, not `EQUIVALENT`, so the query is not widened to backpacks or arbitrary bags. Retrieval expands both spellings, while the user text remains unchanged in `original_query`.

A precision regression was also found during the full suite: adding the new term made fuzzy correction rewrite unknown `шопбер` to `шоппер`. Query Understanding now rejects a one-letter fuzzy mutation inside a doubled consonant. Exact supported single-consonant forms still resolve deterministically.

After the fix A and B have identical canonical positions, hard constraints, retrieval plans and eligible `SupplierOffer` ID sets for all four positions.

### BUG-02 — valid C intent returns `NO_MATCH`

The parser and position scoping are correct. The blocker is the red T-shirt position. SQL finds 6,813 category candidates and 596 red candidates, but category evidence validation retains one and that offer has no availability observation. Consequently zero offers satisfy red T-shirt plus free quantity ≥200. Powerbanks and pens have 38 and 86 eligible offers respectively. The allocator receives no complete three-position input, so `0/8 NO_MATCH` is correct for this snapshot.

### BUG-03 — multi-item D returns `NO_MATCH`

The multi-item parser creates four positions with three required items each, or 12 slots per set. The failure occurs before allocation: socks have four category-valid offers and **zero** offers with an availability observation, hence zero with free quantity ≥300. Umbrellas, raincoats and thermoses have 265, 32 and 279 eligible unique offers. D1 (one set, one product per category) already fails on socks, so the proven maximum number of complete sets under the original constraints is zero. D2–D4 do not reveal an allocator threshold; they inherit the same catalog/availability bottleneck.

The diagnostic payload now records concept/category/color counts, eligible stable IDs, and candidates passed to, selected by and rejected by the allocator. No hard constraint was relaxed and `LAST_KNOWN_STOCK_ALLOWED` remains active.

## Differential trace A/B

| Stage | A: `шопперов` | B: `шоперов` | Result |
|---|---|---|---|
| Raw query | original spelling retained | original spelling retained | intentionally different |
| Router | `NEW_SEARCH`, COMPOSITE | `NEW_SEARCH`, COMPOSITE | equal |
| Typo/morphology | `шопперов` | `шоперов` | accepted forms |
| Product concepts | powerbank, shopperbag, thermomug, pencil | same | equal |
| Effective intent | 10 sets, NAVY, minimum 150, 1 item/position | same | equal |
| SQL candidate sets | 343 / 70 / 841 / 279 | same | equal IDs |
| Eligible IDs | 0 / 0 / 1 / 0 | same | equal IDs |
| Assembly | 4 slots, maximum 0 | same | equal |
| Persisted/HTTP/UI | `NO_MATCH`, 0/10 | same | equal |

The one eligible thermomug in both requests is `artegifts:234614`. No first-page or UI pagination data was used for this comparison.

## Candidate funnels

Every table uses the same 41,987-offer catalog snapshot. `Category valid` is evidence validation after the SQL color filter; `Stock known` is the selected latest observation under `LAST_KNOWN_STOCK_ALLOWED`; `Eligible/allocator` is the stable-ID pool passed to allocation.

### A and B — NAVY, minimum 150

| Stage | POWER_BANK | SHOPPER_BAG | THERMAL_MUG | PENCIL |
|---|---:|---:|---:|---:|
| Concept/category SQL candidates | 343 | 70 | 841 | 279 |
| Color matched | 0 | 0 | 3 | 0 |
| Category valid | 0 | 0 | 1 | 0 |
| Stock known | 0 | 0 | 1 | 0 |
| Quantity sufficient | 0 | 0 | 1 | 0 |
| Eligible / passed to allocator | 0 | 0 | 1 | 0 |
| Selected by allocator | 0 | 0 | 0 | 0 |

### C — position-level color and quantity

| Stage | Red T-shirt ≥200 | Black powerbank ≥100 | Blue pen ≥500 |
|---|---:|---:|---:|
| Concept/category SQL candidates | 6,813 | 343 | 3,175 |
| Color matched | 596 | 108 | 325 |
| Category valid | 1 | 87 | 309 |
| Stock known | 0 | 70 | 132 |
| Quantity sufficient | 0 | 38 | 87 |
| Eligible / passed to allocator | 0 | 38 | 86 |
| Selected by allocator | 0 | 0 | 0 |

Example eligible stable IDs: powerbank `artegifts:235224`, `ucontay:678422172`, `gifts:265026`, `happygifts:646885147`; pen `oasis:1-000115949`, `artegifts:136764`, `ucontay:710269654`, `portobello:00090996`.

### D1–D4 — minimum 300

| Stage | UMBRELLA | RAINCOAT | SOCKS | THERMOS |
|---|---:|---:|---:|---:|
| Concept/category SQL candidates | 885 | 156 | 4 | 674 |
| Category valid | 885 | 156 | 4 | 627 |
| Stock known | 453 | 46 | 0 | 470 |
| Quantity sufficient | 267 | 32 | 0 | 319 |
| Eligible / passed to allocator | 265 | 32 | 0 | 279 |
| Selected by allocator | 0 | 0 | 0 | 0 |

| Variant | Sets | Items/category | Slots/set | Actual | Proven blocker |
|---|---:|---:|---:|---:|---|
| D1 | 1 | 1 | 4 | 0 | socks: 0 eligible |
| D2 | 1 | 3 | 12 | 0 | socks: 0 eligible |
| D3 | 3 | 3 | 12 | 0 | socks: 0 eligible |
| D4 | 30 | 3 | 12 | 0 | socks: 0 eligible |

Because D1 cannot form one complete set, zero is an exact maximum for the current snapshot, rather than a lower-bound estimate.

## Before / after

| Case | Before | After |
|---|---|---|
| A | broad `bag` position | narrow `shopperbag`; 4 correct positions |
| B | shopper position missing | same effective intent and IDs as A |
| C | unexplained 0/8 | correct position constraints; red T-shirt funnel proves 0 eligible |
| D | unexplained 0/30 | 12 slots persisted; D1–D4 prove socks availability is the first blocker |

## Changed files for this repair

- `apps/api/app/domain/intent.py` — narrow shopper concept and inflection pattern.
- `apps/api/app/domain/taxonomy.py` — multilingual concept relations and evidence matching.
- `apps/api/app/domain/query_understanding.py` — conservative doubled-consonant fuzzy safeguard.
- `apps/api/app/application/research_planner.py` — retrieval expansions for both shopper spellings.
- `apps/api/app/application/hard_requests.py` — complete candidate and allocator diagnostics.
- `apps/api/tests/test_domain.py` — equivalence and negative category controls.
- `apps/api/tests/test_universal_natural_intent.py` — A/B effective-intent regression.

## Validation

| Check | Result |
|---|---|
| Focused Query Understanding/taxonomy/intent | PASS, 112 tests |
| Full backend pytest | PASS, 335 passed, 4 deselected, 129.26 s |
| Ruff | PASS |
| ESLint | PASS |
| TypeScript | PASS |
| Next.js production build | PASS |
| Live HTTP A/B/C/D1–D4 | PASS transport/state; all HTTP 202; catalog-proven `NO_MATCH` |
| A/B SupplierOffer set parity | PASS for all four positions |
| Chromium A→B→C→D | PASS; correct headings/categories/counts; one active workspace |
| Chromium F5 | PASS; D persisted |
| Browser errors / horizontal overflow | 0 / false |
| Concurrent-user suite | PASS, 84/84; p50 2,918.98 ms, p95 8,648.34 ms, p99 16,474.97 ms |

Live API elapsed time was 607 ms for A, 685 ms for B, 929–1,083 ms for D1–D4. C took 23,995 ms end-to-end although its candidate search took 726 ms; the remaining latency is upstream intent/LLM processing and is a measured performance limitation, not a retrieval or allocator failure.

## State, safety and remaining limitations

Sequential browser execution A→B→C→D showed no inherited categories, no duplicate active workspace and no stale result after refresh. Isolated live resources were deleted by exact IDs. The main database, users, catalog, observations, jobs and crawler checkpoints were not reset or modified.

Remaining limitations:

- The live catalog cannot satisfy A/B, C or D under their exact hard constraints; no synthetic products or stock were added.
- Query Understanding diagnostics still contain noisy catalog-derived alternative concepts in `detected_subjects`, although the validated effective intent excludes them. This did not affect retrieval or persisted positions in the acceptance cases.
- C's roughly 24-second intent path remains too slow and should be profiled separately; changing LLM orchestration was outside this regression repair.
- Rebuild and Undo remain covered by permanent backend regressions from the preceding stabilization suite; this run's rendered Chromium chain specifically validated replacement and F5, not a manual rendered Undo click.
