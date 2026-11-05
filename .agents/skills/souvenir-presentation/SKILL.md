---
name: souvenir-presentation
description: Build and visually verify Souvenir Studio project collections, product PDF styles, their editor, and the closed local AI design planner from real saved VTO variants.
---

# Souvenir presentations

Use the existing collection, document and rendering implementation:

Backend paths below are relative to `apps/api/app/`.

- `application/collections.py` and `/api/projects/{id}/collection`: authoritative project selection.
- `application/presentations.py`: snapshots, versions, ownership and durable claims.
- `application/presentation_assets.py`: original bytes, hashes, masks and explicit fallbacks.
- `application/presentation_render.py` / `presentation_layouts.py`: shared PDF primitives and compositions.
- `domain/presentation_design.py`: closed page grammar and deterministic planning.
- `application/presentation_design.py`: bounded local Qwen planning with explicit fallback.
- `domain/presentation_palette.py`: authoritative OKLCH library and frozen role colors.
- `application/presentation_decoration.py` / `presentation_contrast.py`: measured ornament placement and text contrast.
- `apps/web/src/components/presentations/`: document workspace.
- `docs/project-collection-pdf-v2.md`: current contract and measured acceptance.

## Collection and truth

Selection belongs to the project. Searches, filters, active-query changes and
Undo do not clear it. Use idempotent add/remove by exact offer ID; clear requires
confirmation and collection version. Migrate only the canonical active research
selection, never the union of historical selections. Preserve unavailable items
with an explicit state, without silently removing them. Read the whole collection
independently of loaded grid cards. Wait for pending writes and snapshot under
the project collection version lock. Two colors of one model remain two items.

Copy price, photograph, color and brand from the same real VTO variant. Retain
source name, ID, SKU, URL and observation time internally. Display Decimal half-up
KZT per unit; NULL means «Цена по запросу». Do not invent brands, models, material,
features or marketing claims. Omit unknown brands. No SKU/source URLs, warehouse
figures or search statuses in client product blocks.

There is no required presentation-title input. Use an automatic internal name
and company/subtitle for the cover. Old titles and saved files remain readable.
User edits affect document display names/captions/order/theme, never the catalog.
Collection changes never rewrite a snapshot. Composition refresh is explicit and
creates a new draft version; existing PDF revisions remain available.

## Composition and styles

Use 960 × 540 pt landscape, 48 pt content margins and embedded Noto Sans with OFL.
Cover headings 28–40 pt; category 30–38 pt; hero names 24–25 pt; pair names 17 pt;
prices 20 pt; support 11–16 pt; folios 10–15 pt. Preserve readable names/prices:
wrap measured text and change composition before reducing type. Never silently
trim a user's edited caption or allow a footer rule to cross prices.

Choose hero, asymmetric pair, same-model variants, lineup or category components
from the validated plan. Three Melango colors normally share a comparison page,
retaining independent photos/colors/prices. Long copy receives a hero page.

Editorial uses asymmetry, varied object scale and open paper. Premium Dark V2 uses
alternating dark/light pages; V3 uses the illuminated dark system described below.
Modern Graphic uses a light cover with a colored plane, modules and larger page
numbers. Compare styles on the same snapshot and palette. Geometry, scale and
text placement must differ. Do not clone renderers: photo, caption, price, logo,
footer, text-bound validation and PDFium export remain shared components.

Preserve aspect ratio, straps, handles and protruding elements. Crop transparent
margins only, with padding. Use support shapes where contrast requires them;
avoid compulsory grey rounded cards under every object. Do not recolor photos.
Map palette to safe background/text/accent roles, including pale/midtone colors.
Client palette is not a product filter. Preserve uploaded logo aspect and color;
company logo and product brand are separate. Do not stamp a logo onto a product.
No placeholder logo frame when absent/disabled. Ending pages need useful content.

### Premium Dark V3

Use a deep palette-derived base plus one smooth local light field and sparse
contour arcs confined to the photo field. Keep headings/captions clear of pattern.
Do not add standard pale ovals or artificial outlines behind dark objects: in V2
the ovals were renderer ellipses selected by mean image brightness, not PNG content.
Never erase pale pixels or alter product colors to remove a renderer artifact.

For legacy inputs without cover_policy=1, reserve the cover left column for company/neutral heading and optional
subtitle/logo; use one leading object at right with deliberately smaller supporting
variants. Do not repeat the equal-size comparison row. Without a logo/subtitle,
the same composition must remain complete. Technical test names are not template copy.

For same-model variants use a small category at 10 pt, one model name at 28 pt
(24 pt for a wrapping heading), and aligned visible alpha bounds. Crop only fully
transparent margins; retain faint nonzero-alpha straps/edges. Melango comparison
objects occupy about 252 pt in height, captions start at 132 pt, with measured
color/brand flow followed by an 18 pt price. Keep the caption group above the
44 pt footer rule. A wrapping model name reduces the photo field, not its margins.
White/glass masks require separate visual review; illumination cannot repair them.

New Premium Dark generations use style_version=3. Saved V2 plans/retries keep 2;
the same shared painter branches by persisted version. Do not overwrite old PDFs.
Check V2 replay and other styles on unchanged inputs, preferably by PDF/pixel hashes.

### Title-only policy for new generations

Every new explicit generation, including one from an old draft, uses cover_policy=1,
DesignPlan schema_version=2 and renderer_version=1. Keep style_version=3 for Premium
Dark. The first page is layout=title, product_refs=[], with one validated logo_ref
and a closed title_family. Planner, server validator and renderer must enforce this.
Never call product photo/caption/price components on TITLE, or use catalog silhouettes,
watermarks/model names as a workaround. All selected variants belong on later pages.
The new title replaces the old cover; there is no optional cover checkbox or title
input. Use the existing company and subtitle, with a neutral automatic heading.

Resolve exactly one logo: active valid custom first, otherwise the actual bundled
app/assets/presentations/251.png (original project 251.png). Missing/disabled custom
is normal fallback; broken active custom is a visible error, never silent fallback.
Freeze source, content hash, immutable storage key and preparation version in the
revision. Renderer reads those frozen bytes, not the current application logo or
frontend dev-server. Preserve alpha, colors/aspect; use a local backing for contrast,
never inversion. Publish immutable hash files without replacing concurrent readers.
Keep the original root 251.png and historical logo bytes.

New explicit generation gets a new persisted request UUID and seed. Replayed request
keys/double clicks return the same revision; failed-revision retry uses that revision's
same input/seed/plan/assets. Download, reopen and F5 do not generate. Use the existing
document/advisory locks. Compare closed geometry features against the three recent
document plans, with a bounded ten-candidate budget. Structural fingerprints exclude
seed/UUID/time. Seek at least two real feature changes; report CONSTRAINED honestly
if no safe distinct plan exists. Keep product order, palette, masks and facts fixed.
Five title families combine shared logo/text/graphic primitives; do not fork renderers.

Use the common PaletteSelect for direction/character/application. Its select-only
combobox/listbox has explicit compatible text/background, selected checkmark,
aria-activedescendant, typeahead, arrow/Enter/Tab/Escape handling and scroll-to-active.
The popup stays inside the modal DOM and uses the native popover top layer, with
viewport-bounded geometry. First Escape closes only the list. Do not disable real
directions to disguise low contrast or use a global form override. Review actual
open lists in installed Edge; SSR and closed-control screenshots are insufficient.
See [title/dropdown acceptance](../../../docs/title-cover-variation.md).

## Plan and local AI

Persist style ID/version, design seed, page plan and prepared-asset version in
each revision. Another design is a new revision. Retry reuses its saved plan.
Optional Autodesign makes one bounded request through the existing local model
queue after preparation supplies image dimensions. Text Qwen is not a vision
model. Client identity, prices, logos, source URLs and SKU are not model inputs.
Treat catalog strings as untrusted data, not instructions. Constrain references
to selected offer IDs during decoding; parent IDs are grouping metadata only.
Validate full selection coverage, uniqueness, density, headings, layouts, seed and
palette roles again on the server. Only known geometry components are allowed.

Use deterministic planning when disabled, unavailable, invalid or timed out.
Record actual ai_used/fallback_reason; never call a fallback successful AI.
No executable code, arbitrary URLs, external inference or unverified claims.
Do not generate many speculative variants in advance. Snapshot facts are always
bound by the renderer, not copied from model-generated text.

## Photo preparation

### Custom presentation materials

The existing editor has one optional “Свои материалы” section: COVER_PHOTO,
BACKGROUND and multiple DECORATION entries. LOGO stays in its existing control.
Use `presentation_materials.py`, `presentation_material_layout.py` and the shared
asset service/painter. Do not create another renderer, catalog item or upload stack.

Accept actual PNG/JPEG/WebP bytes only; preserve original bytes, EXIF orientation,
ICC-to-sRGB conversion, alpha, hashes and preparation version. No implicit rembg,
white-pixel removal, SVG, external processing or filenames as filesystem paths.
Prepare through the existing bounded private queue, outside DB transactions and
API image decoding. Enforce configured bytes/pixels/file-count/retained-byte/time
budgets. Preparation does not generate a PDF; failed replacement keeps the old file.
Successful replacement inherits its settings and leaves historical bytes readable.

Measure real wrapped text, photo bounds, logo and footer first. COVER_PHOTO uses
the reserved cover field, or safe photo-background fields on product pages when
that scope is selected; it never replaces a product photograph. BACKGROUND stays
beneath content with local contrast protection. DECORATION fits free corridors,
preserves aspect/alpha and yields to prices and products. Honor crop permission,
focal point and position preferences; skip an impossible fit. Tone down busy
textures while retaining visible texture; suppress native arcs on custom-art pages.
Never restore pale ovals or use decoration to hide a bad product mask. Only explicit
decoration harmonization may recolor an uploaded material; logos remain unchanged.

Freeze material refs/config/hashes in each revision; record actual pages, placements
and skip reasons. DOCUMENT is permission, not mandatory repetition. Required unused
materials produce NEEDS_REVIEW and block approval/download. Disabled/failed/previous
usage must not be called current successful application. Keep the previous preview
visible during generation, clearly labeled. Removal affects the current draft only.
All originals, thumbnails, preview and PDF routes require document ownership.

Keep style_version=3 and separate material_schema_version=1. Legacy inputs without
palette_plan/auto_decoration must reproduce the approved PDF with empty/disabled
materials. New documents explicitly enable subtle automatic decoration. Qwen receives only verified metadata/descriptions,
not images or private paths; constrain refs to exact enabled owned IDs and validate
scope again. Descriptions are data, never instructions. Deterministic placement and
timeout fallback remain functional. See [custom-material acceptance](../../../docs/custom-presentation-assets.md).

### Palette explorer and automatic decoration

Use the server library and role generator; do not implement another color algorithm
in TypeScript. The explorer renders the returned roles as a schematic, never as an
actual PDF preview. Browse/filter/more/cancel must not save a draft, call a model,
prepare photographs or enqueue a render. Apply is explicit. Retain selection while
appending results, abort stale reads, and keep the native dialog usable on mobile.

Freeze all resolved HEX roles, palette version, preset origin, exact primary HEX,
lock and edited slots in the revision. A manual edit retains provenance and becomes
"Своя палитра". Preserve exact primary input; derive contrast-safe role shades
without silently changing that primary. Use OKLCH hue-aware recipes, chroma-reduced
sRGB gamut mapping, neutral handling and global duplicate checking on actual rendered
roles. Palette direction must reach the background/local illumination, not merely
an unused accent. Server validation replaces untrusted role values.

Palette/decor algorithm versions are separate from style_version=3. Missing new
fields retain the old render path; never rebuild old palette roles from a preset ID.
Persist the closed DecorationPlan and actual per-page safe regions, primitives and
skip reasons. Alternate design may change composition/motif/seed, but must retain
the exact products, order, photos, brands and commercial facts. Retry uses frozen
inputs; neither palette browsing nor design changes rerun cached matting.

Automatic decoration is independent of optional AI. Off retains base styling; on
uses bounded deterministic motifs in corridors outside measured text, photos, logo
and footer. Only successfully placed enabled COVER_PHOTO/BACKGROUND/DECORATION
suppresses automatic detail on that page. Logo alone and disabled files do not.
An impossible required material remains NEEDS_REVIEW and blocks download/approval;
automatic ornament must not conceal that failure.

Check every distinct composed background color under each measured text block.
Require 4.5:1 for normal text, 3:1 only at actual large-text sizes (18 pt regular
or 14 pt bold); apply local protection when needed. This is contrast checking,
not a claim of complete PDF accessibility. Validate all library entries numerically,
then inspect representative real PDF pages including multiple reds, low-chroma,
dark/light palettes and black/white products. Preserve V2/V3 reference hashes.
Use the shared frozen library fixture in frontend/backend tests. See
[palette and decoration acceptance](../../../docs/palette-explorer-decoration.md)
and the permanent check_palette_decoration.py / check_palette_browser.py scripts.

Check actual alpha before reusing PNGs. Otherwise use explicitly installed local
U2-Net weights with the pinned checksum, provenance and CPU process limits.
Never erase white pixels globally, use generative replacement, remove markings
or send images to a service. Keep original bytes and processing metadata; cache
by source hash and pipeline version. Freeze originals across design revisions
until explicit source refresh. Validate uploads by format, size, pixels and alpha.
Do not accept arbitrary network URLs.

Inspect white-on-white, black, glass, metal, thin pens, backpack straps, holes and
fabric edges. Geometry cannot certify a mask. Inferred masks need review; bad
masks require another same-variant photo, prepared upload or explicit original
fallback. Never present an opaque white rectangle as successful removal.

## Verify actual output

Inspect reference images and actual rendered PDF pages. Look for an attachment file
in the current environment; distinguish a visible inline reference from an opened
local file, and do not claim pixel comparison when the original bytes are absent.
Read the original control PDFs when comparing versions. Halyk/Visa/event identities
are not client assets.

Rasterize every final PDF page with the same PDFium pipeline as preview, then
visually inspect Cyrillic/₸, contours, color, crop, contrast, names, prices, logo,
margins, overlap, page order and complete selection coverage. Re-render after
layout changes. HTTP success and text extraction supplement visual review.
Inspect selected-card DOM in an available browser: images stay inside a fixed
image area; information/actions use normal flow; footer does not cover the final
card; mobile has no horizontal overflow. Installed system Edge may use a task-owned
temporary profile. Do not restore project Chromium/Playwright dependencies.
Report NOT TESTED explicitly, never infer visual acceptance from SSR/build alone.

Good: real blue/grey/black Melango variants compared on one page with their own
recorded unit prices, readable captions and a clear common model heading.
Bad: a sibling's photo, invented brand, theme-recolored product, tiny repeated
cards, dropped variants, or an old PDF offered as an updated revision.

Relevant upstream guidance: [PDF skill](https://github.com/openai/skills/tree/main/skills/.curated/pdf),
[frontend design](https://github.com/anthropics/skills/tree/main/skills/frontend-design),
[ReportLab](https://docs.reportlab.com/), [PDFium](https://pypdfium2.readthedocs.io/),
[rembg](https://github.com/danielgatis/rembg). Both upstream skills use Apache-2.0.
This project skill guides Codex; runtime uses actual code, not automatic Qwen
interpretation of SKILL.md. Preserve user data and clean only proven task-owned
resources, never the managed image cache, real documents or import checkpoints.
