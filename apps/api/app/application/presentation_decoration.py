"""Compatibility import for archived presentation designer; not the new workflow."""
import sys

from app.legacy.presentation_designer.application import presentation_decoration as _legacy

sys.modules[__name__] = _legacy
