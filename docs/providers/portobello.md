# Portobello browser findings — 2026-09-15

Verified in local Chromium: https://portobello.ru/ and
`/catalog/termoproduktsiya-i-butylki-dlya-vody`, `/catalog/offer/00101018`.

- Angular SSR exposes `script#ng-state` JSON with `apollo.state`. This is data,
  not JavaScript to evaluate. `ROOT_QUERY.Catalog_Products_Paginator(...)` contains
  `totalCount` (families), `totalDocumentsCount` (offers), `totalPages`, and item refs.
  The observed category reports 36 families, 129 offers, two pages, page size 30.
- `CatalogProduct` groups reference `CatalogNomenclature` variants by code.
  Each variant includes name, article, public RUB price, brand, color reference,
  category chain and original image paths. Product pages additionally expose
  material, capacity, dimensions, coating and descriptions.
- Browser calls `https://api.portobello.ru/graphql`, operation `StocksDataLoader`.
  Stock rows contain offerCode, balance, reserve, **free**, storage type and date.
  Do not treat balance, samples or future arrivals as available quantity.
  Follow-up check: `inStock` is FALSE even for MAIN and TRUE for one COMING
  row; it is not a reliable on-hand marker. Use storage **type MAIN** and free.
  REMOTE is deliberately excluded from strict immediate stock.
  If more than one MAIN storage row exists, strict stock uses the largest single integer `free` value. It never sums warehouses whose overlap/fulfilment relationship is not proven.
- Product links are `/catalog/offer/{code}`. Images use `cdn.portobello.ru`
  with `plain/s3://portobello-catalog/...` paths already present in the DOM.
- Search input placeholder is `Поиск`; pagination is an Angular control, not an
  ordinary next-page anchor. Verified click on button 2 navigates to `?page=1`
  (zero-based). Search Enter navigates to `/catalog?filter-query=...&order=STOCKS_DESC`.
- Public UI makes anonymous cart/token requests itself. Research must never
  explicitly invoke cart mutations or attempt authentication.

Local evidence: `portobello/{bottles.html,graphql.json,product.html}`.
# Final verification — 2026-09-18

Supplier-first navigation follows published `/catalog/…` URLs. Listing extraction now follows active Angular paginator `items → family nomenclatures → offer code`, excluding unrelated SSR recommendations. Regression and live sample passed. Discovery still has failed branches and cannot claim COMPLETE. See `../provider-status.md`, `.local/final/provider-live-portobello.json`.
