# Archived presentation designer

Restore reference: Git `22368447f06764be25cada0c685c444683a756b6` plus the
actual moved source files and their original SHA-256 in [manifest.json](manifest.json).
No database or stored document was migrated to another discriminator.

| Concern | Actual archived implementation | Compatibility entry |
|---|---|---|
| PDF compositions, palette, decoration, local AI | `apps/api/app/legacy/presentation_designer/application/` | former `app.application.presentation_*` modules alias the original module objects |
| Design grammar, composition and palette contracts | `apps/api/app/legacy/presentation_designer/domain/` | former `app.domain.presentation_*` modules |
| Designer editor and palette/material UI | `apps/web/src/legacy/presentation-designer/` | former components re-export the archived implementation |

The application no longer offers designer creation from the selected-products
workflow. Existing owner-only documents, drafts, revisions, original assets,
logos, PDF downloads and the `#presentation=ID` entry remain supported.

Shared, active services deliberately stay outside this archive:
`collections.py`, `presentations.py` (document ownership/CAS/durable claims/worker
supervision), `presentation_assets.py`, `presentation_logo.py`,
`presentation_materials.py`, storage, authorization and PDFium preview.
The commercial offer uses these services and a separate explicit document kind.
It does not invoke archived AI composition or require cutout approval.

To restore the old primary workflow, reconnect its creation/list UI to the
compatibility API; do not overwrite current tables, PDFs or storage. To relocate
the archive, update module aliases and the explicit shared font path, then run
legacy reproduction tests before deploying. Manifest hashes identify the
pre-move source; the renderer's font-root expression changes with its location.

Old designer rules (including original commercial fields being non-editable,
omitted SKU, landscape pages and mask review) apply only to these legacy files.
The commercial-offer contract in the new module is authoritative for new offers.
Its default logo is now the user-supplied `VTOlogo.png`. The archived designer
retains its historical `251.png` contract; both originals and frozen bytes remain.
