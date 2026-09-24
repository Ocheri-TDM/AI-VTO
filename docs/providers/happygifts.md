# HappyGifts browser findings — 2026-09-15

Verified https://happygifts.ru/ and `/catalog/butylki_dlya_vody/`.
The observed category reports 25 families over two pages.

- Search form action `/catalog/`, mobile input `name=q`; desktop input has a
  misspelled `ame=q`, so JS interaction must be checked rather than assuming form
  submission. Product cards use `a.product-card__title`.
- Next page: `a.next-pag[href]`, observed `?PAGEN_1=2`.
- Exact variants have `/catalog/.../color_.../` URLs.
- Product `/.../butylka_dlya_vody_kasfol_steklo_bambuk_500_ml_articul_346581/color_bezhevyy/`
  shows public retail price in `#vu-price_0`, RUB.
- `.avilability-table` separates Центральный, Образцы, Европа, В пути.
  Центральный tooltip explicitly labels Свободно and В резерве. Observed KASFOL
  had total 1, free 0, Europe 30000. It must not qualify for quantity 300.
- Literal JSON `Itms_price` and `Itms_num` is embedded in scripts; parse literals
  without eval. Confirm semantics against the exact variant warehouse table.
- Product facts include color, material, dimensions, litres, brand, description.
  Shipping volume in cubic metres is distinct from bottle capacity.
- JSON-LD covers WebSite/Organization, not reliable per-variant product stock.
- Public browser requests include filter props and group-offer endpoints.
  Login CAPTCHA elements exist hidden in the page: their mere presence is not
  a blocking CAPTCHA. Detect visible challenges only.

Evidence: `.local/research/six/happygifts/{bottles,product}.{html,json}`.
# Final verification — 2026-09-18

Supplier-first navigation reads published `/catalog/…` links; existing next-page DOM and exact active-color/free-stock extraction retained. Navigation/listing/product observed. Coverage PARTIAL; no full pagination-exhaustion claim. See `../provider-status.md` and `.local/final/quality.json`.
