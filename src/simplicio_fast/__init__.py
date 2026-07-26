"""Simplicio Fast proof of concept."""

__version__ = "2.0.6"

from .engine_selection import EngineSelection, EngineSelectionError, select_engine
from .workspace import GenerationId, Manifest, WorkspaceStore

__all__ = [
    "EngineSelection",
    "EngineSelectionError",
    "GenerationId",
    "Manifest",
    "WorkspaceStore",
    "select_engine",
    "__version__",
]
