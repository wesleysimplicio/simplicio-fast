from pathlib import Path
import hashlib
from scripts.build_native_manifest import ABI, build_manifest


def test_manifest_is_deterministic_complete_and_hash_pinned(tmp_path):
    binary = tmp_path / "simplicio-fast-native"
    binary.write_bytes(b"precompiled")
    args = dict(platform="linux-x86_64", version="1.2.3",
                source_commit="a" * 40, toolchain="rustc 1.88.0")
    left = build_manifest(binary, **args)
    assert left == build_manifest(binary, **args)
    assert left == {
        "abi": ABI, "platform": "linux-x86_64",
        "filename": "simplicio-fast-native", "version": "1.2.3",
        "source_commit": "a" * 40, "toolchain": "rustc 1.88.0",
        "size": 11, "sha256": hashlib.sha256(b"precompiled").hexdigest(),
    }


def test_consumer_source_never_invokes_local_rust_toolchain():
    source = Path("src/simplicio_fast/native_backend.py").read_text()
    assert '["cargo"' not in source.lower()
    assert '["rustc"' not in source.lower()
    assert "subprocess.run" in source  # selected precompiled executable only


def test_workflow_declares_all_supported_release_targets():
    workflow = Path(".github/workflows/native-release.yml").read_text()
    for target in (
        "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
        "aarch64-apple-darwin", "x86_64-pc-windows-msvc",
    ):
        assert target in workflow
    assert "actions/upload-artifact@" in workflow
    assert "manifest.json" in workflow
