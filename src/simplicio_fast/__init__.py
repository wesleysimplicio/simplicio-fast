"""Lightweight public facade for Simplicio Fast.

Importing the package exposes its version without eagerly loading mmap,
Runtime subprocess, workspace, navigation, or cache implementations. Public
symbols remain source-compatible and are loaded on first access.
"""

from __future__ import annotations

from importlib import import_module


__version__ = "2.0.16"

_EXPORTS = {
    "ArenaError": (".prism_arena", "ArenaError"),
    "EngineSelection": (".engine_selection", "EngineSelection"),
    "EngineSelectionError": (".engine_selection", "EngineSelectionError"),
    "GenerationId": (".workspace", "GenerationId"),
    "KnowledgeFacade": (".knowledge", "KnowledgeFacade"),
    "Manifest": (".workspace", "Manifest"),
    "NavigationBudget": (".navigation", "NavigationBudget"),
    "NavigationError": (".navigation", "NavigationError"),
    "NavigationIndex": (".navigation", "NavigationIndex"),
    "NavigationItem": (".navigation", "NavigationItem"),
    "NavigationPage": (".navigation", "NavigationPage"),
    "PrismArena": (".prism_arena", "PrismArena"),
    "PrismWorkDelta": (".prism_arena", "PrismWorkDelta"),
    "RequestKey": (".pager", "RequestKey"),
    "RuntimeArtifact": (".runtime_backend", "RuntimeArtifact"),
    "RuntimeBackendError": (".runtime_backend", "RuntimeBackendError"),
    "RuntimeFastBackend": (".runtime_backend", "RuntimeFastBackend"),
    "RuntimeSelection": (".runtime_backend", "RuntimeSelection"),
    "SingleFlightCoordinator": (".pager", "SingleFlightCoordinator"),
    "SingleFlightError": (".pager", "SingleFlightError"),
    "SlotView": (".prism_arena", "SlotView"),
    "TaskOverlay": (".prism_arena", "TaskOverlay"),
    "WorkspaceStore": (".workspace", "WorkspaceStore"),
    "make_request_key": (".pager", "make_request_key"),
    "navigate": (".navigation", "navigate"),
    "select_engine": (".engine_selection", "select_engine"),
    "select_runtime_backend": (".runtime_backend", "select_runtime_backend"),
}

__all__ = [*_EXPORTS, "__version__"]


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
