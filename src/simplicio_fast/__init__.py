"""Simplicio Fast proof of concept."""

__version__ = "2.0.9"

from .engine_selection import EngineSelection, EngineSelectionError, select_engine
from .pager import RequestKey, SingleFlightCoordinator, SingleFlightError, make_request_key
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
    "RequestKey",
    "SingleFlightCoordinator",
    "SingleFlightError",
    "make_request_key",
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
