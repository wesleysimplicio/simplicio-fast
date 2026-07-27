"""Engine manifests and fail-closed selection for the V3 Fast data plane.

The Python implementation is the reference engine.  A Rust engine may only be
selected after a real executable proves its versioned capability handshake;
selection never imports the Python processor as part of the Rust probe.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .snapshot import MAX_FILES, MAX_RELATIONS, MAX_SNAPSHOT_BYTES, MAX_SYMBOLS


MANIFEST_SCHEMA = "simplicio.fast.engine-manifest/v1"
SELECTION_SCHEMA = "simplicio.fast.engine-selection/v1"
ENGINE_CHOICES = ("auto", "rust", "python", "off")


class EngineSelectionError(RuntimeError):
    """Raised when an explicitly requested engine cannot be proven usable."""

    def __init__(self, receipt: dict[str, Any]) -> None:
        self.receipt = receipt
        super().__init__(receipt["reason"])


@dataclass(frozen=True, slots=True)
class EngineSelection:
    requested: str
    selected: str
    reason: str
    executable: str | None
    manifest: dict[str, Any]
    probe_ms: float | None = None

    def receipt(self) -> dict[str, Any]:
        conformance = self.manifest.get("conformance")
        if not isinstance(conformance, dict):
            conformance = {}
        capabilities = self.manifest.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = None
        return {
            "schema": SELECTION_SCHEMA,
            "requested": self.requested,
            "selected": self.selected,
            "requested_engine": self.requested,
            "selected_engine": self.selected,
            "version": self.manifest.get("version"),
            "capabilities": capabilities,
            "reason": self.reason,
            "conformance_digest": conformance.get("digest"),
            "timings": {"probe_ms": self.probe_ms},
            "executable": self.executable,
            "manifest": self.manifest,
        }


def python_manifest() -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "engine": "python",
        "version": __version__,
        "status": "available",
        "reference": True,
        "fallback": True,
        "capabilities": [
            "build",
            "refresh",
            "query",
            "search",
            "context",
            "impact",
            "understand",
            "plan",
            "apply",
            "overlay",
            "lease",
            "doctor",
            "receipts",
        ],
        "formats": ["SFAST001/v1", "SFAST001/v2"],
        "source_languages": ["python"],
        "minimum_python": "3.11",
        "limits": {
            "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
            "max_files": MAX_FILES,
            "max_symbols": MAX_SYMBOLS,
            "max_relations": MAX_RELATIONS,
        },
    }


def _rust_executable() -> str | None:
    configured = os.environ.get("SIMPLICIO_FAST_RUST")
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    return shutil.which("simplicio-fast-rs")


def probe_rust() -> tuple[dict[str, Any] | None, str | None]:
    executable = _rust_executable()
    if executable is None:
        return None, "rust_executable_missing"
    try:
        completed = subprocess.run(
            [executable, "--version", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "rust_probe_failed"
    if completed.returncode != 0:
        return None, "rust_probe_nonzero"
    try:
        manifest = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, "rust_probe_invalid_json"
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return None, "rust_manifest_schema_mismatch"
    if manifest.get("engine") != "rust" or manifest.get("status") != "available":
        return None, "rust_manifest_not_healthy"
    conformance = manifest.get("conformance")
    if not isinstance(conformance, dict) or conformance.get("passed") is not True:
        return None, "rust_conformance_missing"
    return manifest, None


def _probe_rust_timed() -> tuple[dict[str, Any] | None, str | None, float]:
    started = time.perf_counter()
    manifest, reason = probe_rust()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return manifest, reason, elapsed_ms


def select_engine(requested: str = "auto") -> EngineSelection:
    normalized = requested.strip().lower()
    if normalized not in ENGINE_CHOICES:
        raise ValueError(f"unsupported fast engine: {requested}")
    if normalized == "off":
        return EngineSelection(normalized, "off", "explicitly_disabled", None, {})
    if normalized == "python":
        return EngineSelection(
            normalized, "python", "explicitly_selected", None, python_manifest()
        )
    rust_manifest, rust_reason, probe_ms = _probe_rust_timed()
    rust_path = _rust_executable()
    if normalized in {"auto", "rust"} and rust_manifest is not None:
        return EngineSelection(
            normalized, "rust", "rust_probe_passed", rust_path, rust_manifest, probe_ms
        )
    if normalized == "rust":
        selection = EngineSelection(
            normalized,
            "unavailable",
            rust_reason or "rust_unavailable",
            rust_path,
            {"schema": MANIFEST_SCHEMA, "engine": "rust", "status": "unavailable"},
            probe_ms,
        )
        raise EngineSelectionError(selection.receipt())
    return EngineSelection(
        normalized,
        "python",
        rust_reason or "rust_not_selected",
        None,
        python_manifest(),
        probe_ms,
    )
