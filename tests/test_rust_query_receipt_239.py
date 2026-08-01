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
    (tmp_path / "a.py").write_text("def helper():\n    return True\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def helper():\n    return False\n", encoding="utf-8")
    snapshot = tmp_path / "service.sfast"
    build_snapshot(tmp_path, snapshot)

    exact = _run_json(executable, "--query", str(snapshot), "helper", "--limit", "1")
    prefix = _run_json(executable, "--query", str(snapshot), "help", "--limit", "1")
    by_path = _run_json(
        executable,
        "--query",
        str(snapshot),
        "helper",
        "--path",
        "b.py",
        "--limit",
        "1",
    )
    by_kind = _run_json(
        executable,
        "--query",
        str(snapshot),
        "helper",
        "--kind",
        "function",
        "--limit",
        "1",
    )
    assert exact["planner"]["selected_index"] == "persisted.exact"
    assert prefix["planner"]["selected_index"] == "persisted.prefix"
    assert by_path["planner"]["selected_index"] == "persisted.exact+path"
    assert by_kind["planner"]["selected_index"] == "persisted.exact+kind"
    assert by_path["matches"][0]["file"] == "b.py"
    assert by_kind["matches"][0]["kind"] == "function"
    assert exact["planner"]["records_decoded"] == 1
    assert prefix["planner"]["records_decoded"] == 1

    context = _run_json(
        executable,
        "--context",
        str(snapshot),
        str(tmp_path),
        "helper",
        "--limit",
        "2",
    )
    assert context["planner"]["source_files_read"] == 2
    assert context["planner"]["source_cache_hits"] == 0
    assert context["planner"]["source_bytes_read"] > 0

    first_page = _run_json(
        executable, "--query", str(snapshot), "helper", "--limit", "1"
    )
    cursor = first_page["planner"]["next_cursor"]
    assert isinstance(cursor, str) and cursor.isdigit()
    second_page = _run_json(
        executable,
        "--query",
        str(snapshot),
        "helper",
        "--limit",
        "1",
        "--cursor",
        cursor,
    )
    assert second_page["matches"]
    assert second_page["matches"][0]["file"] != first_page["matches"][0]["file"]
    assert second_page["planner"]["records_decoded"] == 1
