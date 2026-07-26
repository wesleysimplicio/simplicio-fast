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
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import __version__


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

    def receipt(self) -> dict[str, Any]:
        return {"schema": SELECTION_SCHEMA, **asdict(self)}


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
            close_fds=True,
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
    return manifest, None


def select_engine(requested: str = "auto") -> EngineSelection:
    if requested not in ENGINE_CHOICES:
        raise ValueError(f"unsupported fast engine: {requested}")
    if requested == "off":
        return EngineSelection(requested, "off", "explicitly_disabled", None, {})
    if requested == "python":
        return EngineSelection(requested, "python", "explicitly_selected", None, python_manifest())
    rust_manifest, rust_reason = probe_rust()
    rust_path = _rust_executable()
    if requested in {"auto", "rust"} and rust_manifest is not None:
        return EngineSelection(requested, "rust", "rust_probe_passed", rust_path, rust_manifest)
    if requested == "rust":
        selection = EngineSelection(
            requested,
            "unavailable",
            rust_reason or "rust_unavailable",
            rust_path,
            {"schema": MANIFEST_SCHEMA, "engine": "rust", "status": "unavailable"},
        )
        raise EngineSelectionError(selection.receipt())
    return EngineSelection(
        requested,
        "python",
        rust_reason or "rust_not_selected",
        None,
        python_manifest(),
    )
