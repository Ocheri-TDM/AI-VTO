# Public VTO visual control

`melango-three-colors.pdf` is a real HTTP-generated, visually inspected PDF
from the populated PostgreSQL on 2026-10-30. It contains three selected real
Melango variants (blue, grey, black), each exactly once on the product pages.
The cover reuses two images decoratively. `melango-cover.png` was rasterized
from that same PDF by PDFium. The JSON manifest contains only public catalog
fields and the final PDF hash, without account IDs, cookies or private logos.

Prices are a historical observation. This fixture demonstrates composition
and variant identity; do not make changing source stock or dates permanent
expected values. Current automated rendering tests generate their own PDFs
and inspect layout, extracted text and full selected-ID coverage.
