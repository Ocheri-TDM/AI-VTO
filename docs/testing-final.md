# Final verification

Stage 4 baseline: **116 passed, 4 opt-in tests deselected**. Final regression: **147 passed, 4 deselected**, 51.42 seconds. Read `.local/final/tests-final.txt` for the last actual run. Deselected tests are not counted as passed. Provider contracts cover all six capability declarations and product normalization for each adapter family; multiwarehouse stock cannot be summed. Timeout fairness verifies that a slow first branch is checkpointed and rotated rather than starving subsequent slices. Published branch identity, old-checkpoint repair and coverage of every observed root have dedicated regressions.

New safety acceptance verifies: supplier-first 400 offers including unmapped branches; aggregate stock-collapse quarantine; only complete safe runs increment missing counters; offers are never physically removed; paginated 62 results; bounded session JSON with IDs instead of full products; new matches require acceptance; filters and selection survive merge; hard versus soft material; undo and original intent preservation.

The reproducible query dataset is `apps/api/tests/fixtures/final_queries.json`: seven literal intents, sixteen local commands and three ambiguous category discoveries. Embedding semantic relevance is separately labelled in `scripts/eval_final_semantics.py`; this small synthetic relevance dataset is not live relevance validation.

`scripts/check_final_restart.py` uses a **real terminated Python worker process** and real isolated PostgreSQL, with a synthetic supplier. Ten committed products survive; the replacement resumes at product 10, with 20 unique offers. Lease expiry is accelerated explicitly. This is not a live supplier restart test.

Migrations `0006_catalog_safety`, `0007_taxonomy_research` were applied on the existing index. `scripts/verify_final_migrations.py` also upgraded an empty isolated database to `0007_taxonomy_research`, with 19 tables. No user data was dropped.

Chromium checked 1920×1080, 1440×900, 1280×800, 768×1024, 430×932, 390×844: visible product cards, no card supplier/stock/SKU text, no horizontal overflow; mobile chat sheet and Escape; developer diagnostics. Artifacts: `.local/final/ui`. The first dashboard check failed on an ambiguous test locator, then was corrected and rerun successfully.

Commands (PowerShell, repository root):

```powershell
$env:PYTHONPATH='apps/api'
.venv/Scripts/python.exe -m pytest apps/api/tests -q
.venv/Scripts/python.exe -m ruff check apps/api/app
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
```

Live provider, background, HTTP and browser checks are separate scripts; they should not be confused with fixtures or unit tests. Detailed results and external limitations are in `provider-status.md` and the final audit.
