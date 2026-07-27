"""Simplicio Fast proof of concept. Bounded Knowledge Facade"""

__version__ = "2.0.13"

from .engine_selection import EngineSelection, EngineSelectionError, select_engine
from .knowledge import KnowledgeFacade
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
    "KnowledgeFacade",
    "__version__",
    "navigate",
]
