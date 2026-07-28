"""Fail-closed optional native hot paths with a complete Python fallback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

ABI = "simplicio.fast-native/v1"


class NativeBackendError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode()


class PythonBackend:
    name = "python"

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def catalog_lookup(catalog: Mapping[str, str], key: str) -> str | None:
        return catalog.get(key)

    @staticmethod
    def page(data: bytes, offset: int, limit: int) -> bytes:
        if offset < 0 or limit < 1 or limit > 65536:
            raise NativeBackendError("page_bounds_invalid")
        return data[offset:offset + limit]

    @staticmethod
    def overlay_merge(base: Mapping[str, bytes],
                      overlay: Mapping[str, bytes | None]) -> dict[str, bytes]:
        result = dict(base)
        for key, value in sorted(overlay.items()):
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
        return dict(sorted(result.items()))


class RustBackend:
    name = "rust"

    def __init__(self, executable: Path, manifest: Mapping[str, Any]) -> None:
        self.executable = executable
        self.manifest = dict(manifest)

    def call(self, operation: str, payload: Mapping[str, Any]) -> Any:
        request = canonical({"abi": ABI, "operation": operation,
                             "payload": payload})
        try:
            result = subprocess.run(
                [str(self.executable)], input=request, capture_output=True,
                timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeBackendError("native_crash", type(exc).__name__) from exc
        if result.returncode != 0:
            raise NativeBackendError("native_crash", str(result.returncode))
        try:
            response = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeBackendError("native_response_invalid") from exc
        if response.get("abi") != ABI or not response.get("ok"):
            raise NativeBackendError("native_response_invalid")
        return response.get("result")


def select_backend(artifact: str | Path | None,
                   manifest: Mapping[str, Any] | None) -> tuple[PythonBackend | RustBackend, str | None]:
    if artifact is None or manifest is None:
        return PythonBackend(), "RUST_UNAVAILABLE"
    path = Path(artifact)
    if manifest.get("abi") != ABI:
        return PythonBackend(), "RUST_ABI_INCOMPATIBLE"
    if manifest.get("platform") not in {"linux-x86_64", "linux-aarch64",
                                        "macos-aarch64", "windows-x86_64"}:
        return PythonBackend(), "RUST_PLATFORM_UNSUPPORTED"
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return PythonBackend(), "RUST_ARTIFACT_MISSING"
    if actual != manifest.get("sha256"):
        return PythonBackend(), "RUST_ARTIFACT_HASH_MISMATCH"
    if not path.is_file():
        return PythonBackend(), "RUST_ARTIFACT_INVALID"
    return RustBackend(path, manifest), None


def execute_with_fallback(backend: PythonBackend | RustBackend,
                          operation: str, payload: Mapping[str, Any]) -> tuple[Any, str, str | None]:
    python = PythonBackend()
    def py_result() -> Any:
        if operation == "sha256":
            return python.sha256(bytes.fromhex(payload["hex"]))
        if operation == "catalog_lookup":
            return python.catalog_lookup(payload["catalog"], payload["key"])
        if operation == "page":
            return python.page(bytes.fromhex(payload["hex"]),
                               int(payload["offset"]), int(payload["limit"])).hex()
        if operation == "overlay_merge":
            base = {k: bytes.fromhex(v) for k, v in payload["base"].items()}
            overlay = {k: None if v is None else bytes.fromhex(v)
                       for k, v in payload["overlay"].items()}
            return {k: v.hex() for k, v in python.overlay_merge(base, overlay).items()}
        raise NativeBackendError("operation_unknown", operation)
    if isinstance(backend, PythonBackend):
        return py_result(), "python", "RUST_UNAVAILABLE"
    try:
        return backend.call(operation, payload), "rust", None
    except NativeBackendError as exc:
        # Native receives immutable bytes/maps only, so a crash cannot corrupt source.
        return py_result(), "python", exc.reason_code.upper()


def backend_receipt_fields(backend: PythonBackend | RustBackend,
                           fallback_reason: str | None) -> dict[str, Any]:
    artifact_hash = None
    if isinstance(backend, RustBackend):
        artifact_hash = backend.manifest.get("sha256")
    return {
        "backend": backend.name, "backend_artifact_hash": artifact_hash,
        "fallback_reason": fallback_reason,
    }
