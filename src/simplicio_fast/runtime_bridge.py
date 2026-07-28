"""Runtime-first native engine bridge — never invokes cargo/rustc locally (#215)."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .native_backend import ABI, PythonBackend

BRIDGE_SCHEMA = "simplicio.fast.runtime-bridge/v1"


class RuntimeBridgeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_native_binary() -> dict[str, Any]:
    """Locate a prebuilt native binary without cargo/rustc."""
    # Explicit env wins.
    configured = os.environ.get("SIMPLICIO_FAST_NATIVE_BIN") or os.environ.get("SIMPLICIO_FAST_RUST")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    which = shutil.which("simplicio-fast-rs")
    if which:
        candidates.append(Path(which))
    cache = os.environ.get("SIMPLICIO_FAST_NATIVE_CACHE")
    if cache:
        candidates.append(Path(cache) / "simplicio-fast-rs")
        if platform.system() == "Windows":
            candidates.append(Path(cache) / "simplicio-fast-rs.exe")

    for path in candidates:
        if path.is_file():
            return {
                "schema": BRIDGE_SCHEMA,
                "status": "found",
                "path": str(path),
                "sha256": _sha_file(path),
                "abi": ABI,
                "platform": platform.system(),
                "machine": platform.machine(),
                "backend": "rust",
                "cargo_used": False,
            }
    return {
        "schema": BRIDGE_SCHEMA,
        "status": "missing",
        "path": None,
        "sha256": None,
        "abi": ABI,
        "platform": platform.system(),
        "machine": platform.machine(),
        "backend": "python",
        "reason_code": "RUST_UNAVAILABLE",
        "cargo_used": False,
    }


def execute_via_bridge(request: Mapping[str, Any], *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Prefer Runtime-provided native binary; otherwise deterministic Python fallback."""
    discovery = discover_native_binary()
    if discovery["status"] != "found":
        # Pure Python fallback — never call cargo.
        py = PythonBackend()
        payload = dict(request)
        result = {
            "schema": BRIDGE_SCHEMA,
            "status": "fallback",
            "backend": "python",
            "reason_code": discovery.get("reason_code", "RUST_UNAVAILABLE"),
            "request_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "result": {"echo": payload.get("op", "noop"), "sha": py.sha256(b"ok")},
            "cargo_used": False,
            "discovery": discovery,
        }
        return result

    path = Path(discovery["path"])
    try:
        completed = subprocess.run(
            [str(path), "--capabilities"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "schema": BRIDGE_SCHEMA,
            "status": "fallback",
            "backend": "python",
            "reason_code": type(exc).__name__,
            "cargo_used": False,
            "discovery": discovery,
            "result": {"echo": request.get("op", "noop")},
        }
    if completed.returncode != 0:
        return {
            "schema": BRIDGE_SCHEMA,
            "status": "fallback",
            "backend": "python",
            "reason_code": f"native_rc:{completed.returncode}",
            "cargo_used": False,
            "discovery": discovery,
            "result": {"echo": request.get("op", "noop")},
        }
    return {
        "schema": BRIDGE_SCHEMA,
        "status": "native",
        "backend": "rust",
        "capabilities": (completed.stdout or "").strip()[:500],
        "artifact_sha256": discovery["sha256"],
        "abi": ABI,
        "cargo_used": False,
        "discovery": discovery,
        "result": {"op": request.get("op", "capabilities")},
    }
