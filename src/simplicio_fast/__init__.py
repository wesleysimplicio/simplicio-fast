"""Simplicio Fast proof of concept. Bounded Knowledge Facade"""

__version__ = "2.0.14"

from .engine_selection import EngineSelection, EngineSelectionError, select_engine
from .runtime_backend import RuntimeArtifact, RuntimeBackendError, RuntimeFastBackend, RuntimeSelection, select_runtime_backend
from .knowledge import KnowledgeFacade
from .pager import RequestKey, SingleFlightCoordinator, SingleFlightError, make_request_key
from .prism_arena import ArenaError, PrismArena, PrismWorkDelta, SlotView, TaskOverlay
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
    "ArenaError",
    "EngineSelection",
    "EngineSelectionError",
    "GenerationId",
    "Manifest",
    "NavigationBudget",
    "NavigationError",
    "NavigationIndex",
    "NavigationItem",
    "NavigationPage",
    "PrismArena",
    "PrismWorkDelta",
    "SlotView",
    "TaskOverlay",
    "WorkspaceStore",
    "select_engine",
    "RuntimeArtifact",
    "RuntimeBackendError",
    "RuntimeFastBackend",
    "RuntimeSelection",
    "select_runtime_backend",
    "KnowledgeFacade",
    "__version__",
    "navigate",
]
