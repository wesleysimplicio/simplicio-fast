import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from simplicio_fast.snapshot import build_snapshot
from simplicio_fast.rust_session import RustCoreSession


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


def _run_session(executable: Path, requests: list[dict[str, object]]) -> list[dict[str, object]]:
    process = subprocess.Popen(
        [str(executable), "--session"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    handshake = json.loads(process.stdout.readline())
    assert handshake["schema"] == "simplicio.fast.engine-session/v1"
    responses = []
    for request in requests:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        responses.append(json.loads(process.stdout.readline()))
    process.stdin.close()
    process.wait(timeout=10)
    assert process.returncode == 0
    return responses


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
    (tmp_path / "a.py").write_text(
        "def helper():\n    return True\n\ndef helper_two():\n    return True\n",
        encoding="utf-8",
    )
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
        "hel",
        "--limit",
        "3",
    )
    assert context["planner"]["source_files_read"] == 2
    assert context["planner"]["source_cache_hits"] == 1
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

    session_responses = _run_session(
        executable,
        [
            {
                "operation": "query",
                "payload": {"snapshot": str(snapshot), "term": "helper", "limit": 1},
            },
            {
                "operation": "query",
                "payload": {"snapshot": str(snapshot), "term": "help", "limit": 1},
            },
            {"operation": "session_cache_stats", "payload": {}},
        ],
    )
    assert session_responses[0]["ok"] and session_responses[1]["ok"]
    assert session_responses[2] == {"ok": True, "result": {"snapshots": 1}}

    with RustCoreSession(executable) as session:
        result = session.call(
            "query",
            {"snapshot": str(snapshot), "term": "helper", "limit": 1},
        )
        assert result["planner"]["records_decoded"] == 1
        assert session.call("session_cache_stats", {}) == {"snapshots": 1}
