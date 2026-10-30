---
name: souvenir-presentation
description: Build and visually verify Souvenir Studio product PDFs and their document editor from real saved VTO selections. Use for this project's presentation templates, image preparation, previews, and export regression work.
---

# Souvenir presentations

Use the existing implementation, not a parallel export engine:

- `apps/api/app/application/presentations.py`: snapshots, versions, ownership, durable claims.
- `presentation_assets.py`: original bytes, content hashes, masks and explicit fallbacks.
- `presentation_render.py`: one ReportLab template family and PDFium previews.
- `apps/web/src/components/presentations/`: project document workspace.
- `docs/presentations.md`: installation, provenance, measured acceptance and limitations.

The reference attachment available for the first implementation contained text
only. Its stated visual direction is large isolated objects, generous whitespace,
short captions, light product pages and a contrasting cover. Do not claim a
pixel comparison to an unavailable image. Halyk/Visa/event names mentioned in
the brief are not client assets. Never introduce those identities into output.

## Composition

Use landscape 960 × 540 pt (16:9). Keep a 48 pt outer margin and an aligned
category heading. Default to two products; allocate a full composition to a
single item, long name, or long caption. Additional products create pages.
Do not squeeze typography to fit an arbitrary item count. The page number
denotes actual document order; no fake section numbers.

The embedded Noto Sans family is supplied with OFL.txt. Cover heading 38 pt,
category 28 pt, hero name 24 pt, pair name 17 pt, price 19–22 pt, supporting
text 10–13 pt, page folio 9 pt. Maintain at least 17 pt product names and
readable prices; adjust the composition before reducing type. Wrap long text
with measured Paragraphs and reject overflow. Never silently trim a user's
edited caption. Source names remain in the internal snapshot even when a
shorter presentation name is edited.

Place generous object regions before captions. Preserve image aspect ratio and
all straps, handles and protruding elements; crop only transparent margins with
padding. Use neutral image surfaces to separate white, black and transparent
items from the page. Photograph tint and texture remain unchanged.

## Palette and logo

Map user hex colors to cover, accent and secondary accent; derive safe text,
paper, surface, muted and line roles. Verify text contrast, including pale and
nearly black palettes. The palette never becomes a product filter.

Use only a client-uploaded logo for document branding. A product brand is a
separate optional caption. Place the logo at its native aspect ratio in the
reserved cover area, without recoloring or stamping it onto a product.
Choose a supporting neutral surface for contrast. No empty logo frame when
absent/disabled. Omit a closing page unless an actual ending message exists.

## Commercial truth

Build from the server's entire saved selection, not loaded React cards. Every
offer is one selected real variant, including variants of the same model.
Retain exact offer/product/variation IDs and original source fields internally.
Price, photograph and color must come from that same variant. Display KZT per
unit with Decimal half-up rounding; NULL becomes «Цена по запросу», never zero.
Never infer a brand from the model name or invent commercial claims.

Only document display name/caption/order/theme are editable. No source URLs,
SKU, warehouse figures or search statuses in client product blocks. Qwen is
optional editorial assistance, not required by the renderer and not trusted
with prices, brands, stock, product selection or image substitutions.

## Image preparation

Check actual alpha before reusing a prepared PNG. Otherwise use the explicitly
installed local U2-Net weights with the pinned checksum and bounded CPU process.
Do not erase white pixels globally, use generative replacement, remove marks or
send client images to a service. Keep originals and processing metadata; cache
by source bytes and pipeline/model version. Freeze original bytes across document
revisions until explicit source refresh. Client uploads are format/pixel/size
validated; arbitrary network URLs are not accepted.

Inspect white-on-white products, black products, transparent bottles, metal,
thin pens, backpack straps, holes and fabric edges. Geometry checks cannot
certify these. Generated masks require review; a bad mask must be replaced or
an original-photo fallback explicitly accepted. Never present an opaque white
rectangle as successful background removal.

## Verify the actual PDF

Generate real PDFs, rasterize every page with the same PDFium pipeline used
for preview, and inspect the images. Re-render after template changes. Review
full-size pages for Cyrillic/₸, crop, contrast, photo identity, price placement,
long names, logo, margins, overlap, order and complete selection coverage.
Text extraction/HTTP success is supplemental evidence. List NOT TESTED checks
explicitly; do not report visual review based only on SSR or a build.

Good: a blue selected backpack with its real photo, its recorded unit price,
optional known brand, clear category and an uncluttered caption.
Bad: a white sibling's photo, made-up brand, an image recolored to the theme,
three tiny cards in a drawer, UI screenshots as PDF pages, missing items, or
an old preview downloaded as a newly edited document.

Relevant upstream guidance (read the current applicable portion before changing
the workflow): [PDF skill](https://github.com/openai/skills/tree/main/skills/.curated/pdf),
[frontend design](https://github.com/anthropics/skills/tree/main/skills/frontend-design),
[ReportLab](https://docs.reportlab.com/), [PDFium](https://pypdfium2.readthedocs.io/),
[rembg](https://github.com/danielgatis/rembg). The two upstream skills use
Apache-2.0. This project skill is original project guidance, not an application
runtime dependency; Qwen does not read it automatically.
