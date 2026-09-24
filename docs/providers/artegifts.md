# ArteGifts browser findings — 2026-09-15

Verified https://kz.artegifts.by/ after one connection timeout; retry succeeded.
Category `/catalog/butylki/` reports 56 families, three pages.

- Search uses `input.js-ajax-search[name=q]` with Alpine interaction.
- Listing links `.title.title-base`; next/load-more `a.load-more[href]`,
  observed `?PAGEN_1=2`. Page three is also linked.
- Exact variant URL has `active_sku_id`. The descriptive path can name a
  different color: trust selected variant data, never the URL slug.
- Product page includes `data[data]` JSON with `id`, `stocks`, `simple`,
  public `priceRrc`, `priceCurrency`, and selected product information.
  `dataconf` has a `dataсonfigurator` attribute (Cyrillic с) containing product
  name, ID, stores and free quantity. Parse JSON, never execute the attribute.
- Verified Mystik variant 135834: black, KZT, priceRrc 1505. The source exposed several store values; strict stock now uses the largest single `available` value (1976 in the capture), never their sum.
  available1976, Novosibirsk available1. Arrival counts are separate.
- Attributes: `.product-features__item`, `.product-features__prop`,
  `.product-features__val`: PET, 730 ml, size7x10.5x23.5cm. The structured
  `volume=0.001596` is shipping volume, NOT bottle capacity.
- Images already appear as supplier URLs under `/upload/`. Select the current
  product gallery, excluding recommendation cards and color thumbnails.
- Observed JSON-LD is **unsuitable**: blank product identity and AggregateOffer
  in BYN derived from recommended products while current page is KZT.
  Prefer selected-variant JSON and DOM.

Evidence: `.local/research/six/artegifts/{retry,bottles,product}.{html,json}`.
# Final verification — 2026-09-18

Supplier-first navigation reads published `/catalog/…` links. Existing PAGEN_1/load-more and configurator active_sku extraction retained. Longer live sample succeeded after initial timeout. Two resumed 45-second slices retained the same run/133 roots but accepted no additional products. Live index separately contains 80 offers. Process-kill test uses a fixture supplier, not ArteGifts. Coverage PARTIAL. See `../provider-status.md`.
