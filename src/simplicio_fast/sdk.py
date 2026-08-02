"""Small embeddable Python SDK facade over the public projection contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterable

from .projection import ProjectionEnvelope, ProjectionStore
from .universal_context import compile_context


SDK_SCHEMA = "simplicio.fast.sdk/v1"
SDK_SUPPORT_MATRIX = (
    {
        "surface": "python",
        "status": "supported",
        "operations": (
            "publish", "compile_delta", "query", "query_async", "snapshot",
            "save", "open", "context", "context_async",
        ),
        "reason": None,
    },
    {
        "surface": "rust",
        "status": "partial",
        "operations": ("read", "query", "context"),
        "reason": "crate_conformance_and_installed_matrix_pending",
    },
    {
        "surface": "session",
        "status": "partial",
        "operations": ("stats", "query", "context"),
        "reason": "resident_transport_and_cross_platform_receipts_pending",
    },
    {
        "surface": "cli",
        "status": "partial",
        "operations": ("diagnose", "json_receipts"),
        "reason": "equivalent_sdk_surface_and_installed_receipts_pending",
    },
)


class SDKError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ProjectionSDK:
    """In-process projection operations with explicit repository scope."""

    def __init__(self, repository: str) -> None:
        self.store = ProjectionStore(repository)

    @property
    def repository(self) -> str:
        return self.store.repository

    @property
    def generation(self) -> str | None:
        return self.store.generation

    def publish(self, envelope: ProjectionEnvelope) -> dict[str, Any]:
        self.store.publish(envelope)
        return {"schema": SDK_SCHEMA, "operation": "publish", "repository": self.repository, "generation": self.generation, "handle": envelope.stable_handle}

    def compile_delta(self, generation: str, *, changed: Iterable[ProjectionEnvelope] = (), deleted_handles: Iterable[str] = (), closure_handles: Iterable[str] = ()) -> dict[str, Any]:
        return self.store.apply_delta(generation, changed=tuple(changed), deleted_handles=tuple(deleted_handles), closure_handles=tuple(closure_handles))

    def query(self, handle: str) -> dict[str, Any] | None:
        return next((item for item in self.store.snapshot() if item["stable_handle"] == handle), None)

    def snapshot(self) -> list[dict[str, Any]]:
        return self.store.snapshot()

    def save(self, path: Path) -> dict[str, Any]:
        return self.store.save(path)

    @classmethod
    def open(cls, path: Path, repository: str) -> "ProjectionSDK":
        instance = cls(repository)
        instance.store = ProjectionStore.load(path, repository)
        return instance

    def context(self, *, max_bytes: int = 256 * 1024, max_tokens: int = 4096, max_items: int = 128) -> dict[str, Any]:
        envelopes = [ProjectionEnvelope.decode(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()) for item in self.store.snapshot()]
        return compile_context(envelopes, repository_scope=self.repository, max_bytes=max_bytes, max_tokens=max_tokens, max_items=max_items)

    async def context_async(self, *, max_bytes: int = 256 * 1024, max_tokens: int = 4096, max_items: int = 128) -> dict[str, Any]:
        """Run bounded local compilation without blocking the event loop."""
        return await asyncio.to_thread(self.context, max_bytes=max_bytes, max_tokens=max_tokens, max_items=max_items)

    async def query_async(self, handle: str) -> dict[str, Any] | None:
        """Async-safe read-only query using the same synchronous contract."""
        return await asyncio.to_thread(self.query, handle)

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema": "simplicio.fast.sdk-capabilities/v1",
            "sdk": SDK_SCHEMA,
            "operations": [
                "publish", "compile_delta", "query", "query_async", "snapshot",
                "save", "open", "context", "context_async",
            ],
            "support_matrix": [
                {**item, "operations": list(item["operations"])}
                for item in SDK_SUPPORT_MATRIX
            ],
            "authority": "derived_read_only",
        }


__all__ = ["ProjectionSDK", "SDKError", "SDK_SCHEMA", "SDK_SUPPORT_MATRIX"]
