# Provider status — live evidence, September 17–18 2026

All six adapters remain integrated. Root/navigation/listing/detail observations were run with ordinary local Chromium. Raw reports: `.local/final/provider-live.json`, `provider-live-portobello-artegifts.json`, `catalogs/*.json`; discovery execution slices: `background-catalog_discovery.json`.

| Supplier | Published discovery mechanism | Extraction / refresh | Final live evidence |
|---|---|---|---|
| Gifts | `/catalog/…` links on home/category pages, ordinary next links | ArticlesData exact variant refs; exact page RUB/free stock/gallery | Navigation + listing + product observed; partial traversal |
| Ucontay | `/collection/…` links, pagination-next | Main document InSales variant JSON; KZT; one family document refreshes variants | Navigation + listing + variants observed; partial traversal |
| Portobello | `/catalog/…` links, zero-based paginator | Angular ng-state, browser StocksDataLoader MAIN/free (max one MAIN store), RUB | Initial detail attempts failed; recheck read product successfully; partial traversal |
| HappyGifts | `/catalog/…` links, next-pag | Active color DOM and central warehouse Свободно, RUB | Navigation + listing + product observed; partial traversal |
| ArteGifts | `/catalog/…` links, load-more PAGEN_1 | Configurator JSON, exact active_sku_id, KZT; max one store `available` | Initial timeout; longer recheck read product; live process-kill/resume kept run_id/133 roots and 80 unique offers at that checkpoint; later index contains 285 offers, coverage still partial |
| Oasis | `/categories/…` navigation, page-N | Exact data-product sizes stock excluding reserve/transit, RUB | Initial home 403; later navigation/listing/product succeeded; partial traversal |

No supplier's full public catalog exhaustion is established by these short live runs. Contract observations are sample-level evidence, not a full guarantee that every branch or variant parses. Deterministic tests cover URL acceptance for all six, while richer extraction fixtures remain concentrated in Oasis/Ucontay/Gifts; live artifacts supplement that gap.

Oasis diagnosis: an ordinary Chromium home navigation initially returned HTTP **403**, no redirect, HTML content type, title `403 Forbidden`, no catalog links, about 237 ms. A later ordinary browser context read roots and a real product without identity spoofing, auth, protection bypass or CAPTCHA solving. Therefore the evidence supports intermittent access denial, not a proven permanent outage or an invented parser fix. The server-side reason for the initial 403 is unknown.

Navigation-only pages and configurators can currently remain failed/unresolved branches. Their failure prevents COMPLETE. ArteGifts timeout preserves checkpoint/run identity; the generic process-kill/resume acceptance uses a fixture provider, so it must not be described as an ArteGifts process-kill acceptance.

Supplier-specific warehouse/currency/variant semantics are documented in `docs/providers/*.md`. These pages contain historical observations; final evidence above supersedes blanket descriptions of unavailable Oasis or taxonomy-seeded discovery.
