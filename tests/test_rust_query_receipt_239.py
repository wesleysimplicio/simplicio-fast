import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from simplicio_fast.snapshot import build_snapshot


def _run_json(executable: Path, *args: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="simplicio-fast-rust-query-") as directory:
        stdout = Path(directory) / "stdout.json"
        stderr = Path(directory) / "stderr.txt"
        with stdout.open("w", encoding="utf-8") as out, stderr.open(
            "w", encoding="utf-8"
        ) as err:
            result = subprocess.run(
                [str(executable), *args],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                text=True,
                check=False,
            )
        assert result.returncode == 0, stderr.read_text(encoding="utf-8")
        return json.loads(stdout.read_text(encoding="utf-8"))


def test_rust_query_receipt_uses_exact_and_prefix_indexes(tmp_path: Path) -> None:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo is required for Rust query receipt E2E")
    root = Path(__file__).parents[1]
    subprocess.run(
        [cargo, "build", "--manifest-path", "rust/simplicio-fast-core/Cargo.toml", "--locked", "--quiet"],
        cwd=root,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )
    executable = root / "rust" / "target" / "debug" / (
        "simplicio-fast-rs.exe"
        if shutil.which("cmd.exe")
        else "simplicio-fast-rs"
    )
    assert executable.is_file()
    (tmp_path / "service.py").write_text(
        "def helper():\n    return True\n\ndef helper_extra():\n    return False\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "service.sfast"
    build_snapshot(tmp_path, snapshot)

    exact = _run_json(executable, "--query", str(snapshot), "helper", "--limit", "1")
    prefix = _run_json(executable, "--query", str(snapshot), "help", "--limit", "1")
    assert exact["planner"]["selected_index"] == "persisted.exact"
    assert prefix["planner"]["selected_index"] == "persisted.prefix"
    assert exact["planner"]["records_decoded"] == 1
    assert prefix["planner"]["records_decoded"] == 1
