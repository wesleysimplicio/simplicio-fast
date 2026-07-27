"""Simplicio Fast proof of concept."""

__version__ = "2.0.7"

from .engine_selection import EngineSelection, EngineSelectionError, select_engine
from .workspace import GenerationId, Manifest, WorkspaceStore
from .navigation import (
    NavigationBudget,
    NavigationError,
    NavigationIndex,
    NavigationItem,
    NavigationPage,
    navigate,
)

__all__ = [
    "EngineSelection",
    "EngineSelectionError",
    "GenerationId",
    "Manifest",
    "NavigationBudget",
    "NavigationError",
    "NavigationIndex",
    "NavigationItem",
    "NavigationPage",
    "WorkspaceStore",
    "select_engine",
    "__version__",
    "navigate",
]
