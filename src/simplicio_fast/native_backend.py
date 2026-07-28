"""Fail-closed optional native hot paths with a complete Python fallback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Any, Mapping, Sequence

from . import __version__

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


def select_backend(
    artifact: str | Path | None,
    manifest: Mapping[str, Any] | None,
    *,
    expected_platform: str | None = None,
) -> tuple[PythonBackend | RustBackend, str | None]:
    if artifact is None or manifest is None:
        return PythonBackend(), "RUST_UNAVAILABLE"
    path = Path(artifact)
    if manifest.get("abi") != ABI:
        return PythonBackend(), "RUST_ABI_INCOMPATIBLE"
    supported = {"linux-x86_64", "linux-aarch64",
                 "macos-aarch64", "windows-x86_64"}
    manifest_platform = manifest.get("platform")
    if manifest_platform not in supported:
        return PythonBackend(), "RUST_PLATFORM_UNSUPPORTED"
    expected_platform = expected_platform or platform_tag()
    if expected_platform is None or manifest_platform != expected_platform:
        return PythonBackend(), "RUST_PLATFORM_MISMATCH"
    if manifest.get("version") != __version__:
        return PythonBackend(), "RUST_VERSION_MISMATCH"
    source_commit = manifest.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        return PythonBackend(), "RUST_MANIFEST_INVALID"
    if not path.is_file():
        return PythonBackend(), "RUST_ARTIFACT_MISSING"
    try:
        content = path.read_bytes()
    except OSError:
        return PythonBackend(), "RUST_ARTIFACT_MISSING"
    if manifest.get("size") != len(content):
        return PythonBackend(), "RUST_ARTIFACT_SIZE_MISMATCH"
    actual = hashlib.sha256(content).hexdigest()
    if actual != manifest.get("sha256"):
        return PythonBackend(), "RUST_ARTIFACT_HASH_MISMATCH"
    return RustBackend(path, manifest), None


def platform_tag(*, system: str | None = None,
                 machine: str | None = None) -> str | None:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    aliases = {
        ("linux", "x86_64"): "linux-x86_64",
        ("linux", "amd64"): "linux-x86_64",
        ("linux", "aarch64"): "linux-aarch64",
        ("linux", "arm64"): "linux-aarch64",
        ("darwin", "arm64"): "macos-aarch64",
        ("windows", "amd64"): "windows-x86_64",
        ("windows", "x86_64"): "windows-x86_64",
    }
    return aliases.get((system, machine))


def resolve_packaged_backend(root: str | Path, *,
                             system: str | None = None,
                             machine: str | None = None
                             ) -> tuple[PythonBackend | RustBackend, str | None]:
    """Resolve `artifacts/<platform>/<ABI>/manifest.json` without a toolchain.

    Absence is a normal, explicit Python fallback. This function never invokes
    cargo or rustc; only a manifest-bound executable can be selected.
    """
    tag = platform_tag(system=system, machine=machine)
    if tag is None:
        return PythonBackend(), "RUST_PLATFORM_UNSUPPORTED"
    abi_dir = ABI.replace("/", "_")
    directory = Path(root) / "artifacts" / tag / abi_dir
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PythonBackend(), "RUST_ARTIFACT_MISSING"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return PythonBackend(), "RUST_MANIFEST_INVALID"
    filename = manifest.get("filename")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return PythonBackend(), "RUST_MANIFEST_INVALID"
    manifest = dict(manifest)
    manifest.setdefault("platform", tag)
    return select_backend(directory / filename, manifest, expected_platform=tag)


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
