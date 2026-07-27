"""Offline installation and artifact diagnostics for packaging/rollback."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__


SCHEMA = "simplicio.fast.installation/v1"
ENGINE_MANIFEST_SCHEMA = "simplicio.fast.engine-manifest/v1"


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rust_candidate() -> Path | None:
    configured = os.environ.get("SIMPLICIO_FAST_RUST")
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None
    found = shutil.which("simplicio-fast-rs")
    return Path(found) if found else None


def _manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = subprocess.run([str(path), "--version", "--json"], capture_output=True, text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, type(error).__name__
    if result.returncode != 0:
        return None, f"returncode:{result.returncode}"
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(value, dict):
        return None, "manifest_not_object"
    if value.get("schema") != ENGINE_MANIFEST_SCHEMA:
        return None, "manifest_schema_mismatch"
    if value.get("engine") != "rust":
        return None, "manifest_engine_mismatch"
    if value.get("status") != "available":
        return None, "manifest_not_available"
    return value, None


def report() -> dict[str, Any]:
    rust = _rust_candidate()
    rust_manifest = None
    rust_reason = "artifact_missing"
    if rust:
        rust_manifest, rust_reason = _manifest(rust)
        if rust_manifest is not None:
            rust_reason = None
    rust_status = "pass" if rust_manifest else ("info" if rust is None else "fail")
    overall_status = "ready" if rust_status != "fail" else "degraded"
    python_only_check = {"name": "python_only_path", "status": "pass", "detail": "supported"}
    rust_check = {
        "name": "rust_artifact",
        "status": rust_status,
        "path": str(rust) if rust else None,
        "sha256": _digest(rust) if rust else None,
        "manifest": rust_manifest,
        "reason": rust_reason,
    }
    checks = [
        {"name": "python_package", "status": "pass", "version": __version__},
        python_only_check,
        rust_check,
    ]
    if rust_check["status"] == "pass":
        selected_engine = "rust"
        reason_code = "rust_artifact_available"
    elif python_only_check["status"] == "pass":
        selected_engine = "python"
        reason_code = rust_check["reason"] or "rust_artifact_unavailable"
    else:
        selected_engine = None
        reason_code = "no_usable_engine"
    checks.append(
        {
            "name": "offline_resolution",
            "status": "pass",
            "detail": "no download performed",
            "receipt": {
                "requested_engine": "auto",
                "selected_engine": selected_engine,
                "reason_code": reason_code,
            },
        }
    )
    return {
        "schema": SCHEMA,
        "status": overall_status,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "package": {"name": "simplicio-fast", "version": __version__},
        "checks": checks,
        "rollback": {"supported": False, "reason": "packaging_matrix_not_yet_published"},
    }
