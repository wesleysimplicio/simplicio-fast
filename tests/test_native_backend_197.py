import hashlib
from pathlib import Path
import pytest
from simplicio_fast import __version__
from simplicio_fast.native_backend import (
    ABI,
    PythonBackend,
    RustBackend,
    backend_receipt_fields,
    execute_with_fallback,
    platform_tag,
    resolve_packaged_backend,
    select_backend,
)


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("sha256", {"hex": b"abc".hex()}),
        ("catalog_lookup", {"catalog": {"a": "1"}, "key": "a"}),
        ("page", {"hex": b"abcdef".hex(), "offset": 1, "limit": 3}),
        (
            "overlay_merge",
            {
                "base": {"a": b"1".hex(), "b": b"2".hex()},
                "overlay": {"a": b"3".hex(), "b": None},
            },
        ),
    ],
)
def test_python_standalone_hot_paths(operation, payload):
    result, backend, reason = execute_with_fallback(PythonBackend(), operation, payload)
    assert backend == "python" and reason == "RUST_UNAVAILABLE"
    assert result is not None


def test_incompatible_or_tampered_artifact_is_never_used(tmp_path):
    artifact = tmp_path / "fast-native"
    artifact.write_bytes(b"binary")
    valid = {
        "abi": ABI,
        "platform": "linux-x86_64",
        "version": __version__,
        "source_commit": "a" * 40,
        "size": len(b"binary"),
        "sha256": hashlib.sha256(b"binary").hexdigest(),
    }
    backend, reason = select_backend(
        artifact,
        {
            "abi": "old",
            "platform": "linux-x86_64",
            "sha256": hashlib.sha256(b"binary").hexdigest(),
        },
    )
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ABI_INCOMPATIBLE"
    backend, reason = select_backend(artifact, {**valid, "sha256": "0" * 64})
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ARTIFACT_HASH_MISMATCH"
    backend, reason = select_backend(artifact, {**valid, "version": "0.0.0"})
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_VERSION_MISMATCH"
    backend, reason = select_backend(artifact, {**valid, "size": 999})
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ARTIFACT_SIZE_MISMATCH"


class GoldenRust(RustBackend):
    def __init__(self):
        pass

    def call(self, operation, payload):
        value, _, _ = execute_with_fallback(PythonBackend(), operation, payload)
        return value


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("sha256", {"hex": b"portable fixture".hex()}),
        ("page", {"hex": bytes(range(64)).hex(), "offset": 7, "limit": 19}),
        ("overlay_merge", {"base": {"a": "31"}, "overlay": {"b": "32"}}),
    ],
)
def test_differential_golden_python_rust_semantics(operation, payload):
    expected = execute_with_fallback(PythonBackend(), operation, payload)[0]
    actual, backend, reason = execute_with_fallback(GoldenRust(), operation, payload)
    assert actual == expected and backend == "rust" and reason is None


class CrashingRust(GoldenRust):
    def call(self, operation, payload):
        from simplicio_fast.native_backend import NativeBackendError

        raise NativeBackendError("native_crash")


def test_native_crash_degrades_without_mutating_source():
    source = {"a": "31"}
    result, backend, reason = execute_with_fallback(
        CrashingRust(), "overlay_merge", {"base": source, "overlay": {"b": "32"}}
    )
    assert result == {"a": "31", "b": "32"}
    assert source == {"a": "31"}
    assert backend == "python" and reason == "NATIVE_CRASH"


def test_selected_backend_is_serializable_in_generation_receipt():
    assert backend_receipt_fields(PythonBackend(), "RUST_UNAVAILABLE") == {
        "backend": "python",
        "backend_artifact_hash": None,
        "fallback_reason": "RUST_UNAVAILABLE",
    }


def test_packaged_resolver_is_explicit_when_no_precompiled_binary_exists(tmp_path):
    backend, reason = resolve_packaged_backend(
        tmp_path, system="Linux", machine="x86_64"
    )
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ARTIFACT_MISSING"


def test_packaged_resolver_validates_manifest_and_sha_without_toolchain(tmp_path):
    directory = tmp_path / "artifacts" / "linux-x86_64" / ABI.replace("/", "_")
    directory.mkdir(parents=True)
    artifact = directory / "simplicio-fast-native"
    artifact.write_bytes(b"precompiled-fixture")
    (directory / "manifest.json").write_text(
        '{"abi":"simplicio.fast-native/v1","filename":"simplicio-fast-native",'
        f'"platform":"linux-x86_64","version":"{__version__}",'
        '"source_commit":"'
        + ("a" * 40)
        + '","size":19,"sha256":"'
        + hashlib.sha256(artifact.read_bytes()).hexdigest()
        + '"}',
        encoding="utf-8",
    )
    backend, reason = resolve_packaged_backend(
        tmp_path, system="linux", machine="amd64"
    )
    assert isinstance(backend, RustBackend) and reason is None
    artifact.write_bytes(b"x" * len(b"precompiled-fixture"))
    backend, reason = resolve_packaged_backend(
        tmp_path, system="linux", machine="x86_64"
    )
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ARTIFACT_HASH_MISMATCH"


def test_platform_aliases_are_canonical_and_unknown_is_rejected():
    assert platform_tag(system="Darwin", machine="arm64") == "macos-aarch64"
    assert platform_tag(system="plan9", machine="mips") is None


def test_artifact_hashing_is_streamed_and_size_bounded(tmp_path, monkeypatch):
    artifact = tmp_path / "fast-native"
    artifact.write_bytes(b"streamed-artifact")
    manifest = {
        "abi": ABI,
        "platform": "linux-x86_64",
        "version": __version__,
        "source_commit": "a" * 40,
        "size": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("whole-file read is forbidden")
        ),
    )
    backend, reason = select_backend(artifact, manifest)
    assert isinstance(backend, RustBackend)
    assert reason is None
    monkeypatch.setattr("simplicio_fast.native_backend.MAX_NATIVE_ARTIFACT_BYTES", 4)
    backend, reason = select_backend(artifact, manifest)
    assert isinstance(backend, PythonBackend)
    assert reason == "RUST_ARTIFACT_TOO_LARGE"
