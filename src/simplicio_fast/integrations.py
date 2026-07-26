from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _contract_hash(value: bytes | str) -> str:
    """Match simplicio-dev-cli's text hash while preserving Fast's byte guard."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return hashlib.sha256(value).hexdigest()
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run_mapper(root: Path) -> dict[str, Any] | None:
    sibling = Path(sys.executable).with_name("simplicio-mapper")
    executable = shutil.which("simplicio-mapper") or (
        str(sibling) if sibling.is_file() else None
    )
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "index", str(root), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "simplicio-mapper failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    payload: Any
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": completed.stdout.strip()}
    return {
        "adapter": "simplicio-mapper",
        "status": "executed",
        "command": "index",
        "result": payload,
    }


def run_dev_cli_changeset(
    root: Path, changeset: dict[str, Any], *, write: bool
) -> dict[str, Any] | None:
    try:
        from simplicio.mechanical_edit import execute_plan
    except ImportError:
        return None

    operations: list[dict[str, Any]] = []
    touched: list[str] = []
    for change in changeset["changes"]:
        relative = change["path"]
        touched.append(relative)
        path = root / relative
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != change["expected_sha256"]:
            raise ValueError(f"stale source hash for {relative}")
        lines = raw.decode("utf-8").splitlines(keepends=True)
        for replacement in change["replacements"]:
            start = replacement["start_line"]
            end = replacement["end_line"]
            range_text = "".join(lines[start - 1 : end])
            text = replacement["content"]
            if text and not text.endswith("\n"):
                text += "\n"
            operations.append(
                {
                    "op": "replace_range",
                    "path": relative,
                    "start_line": start,
                    "end_line": end,
                    "text": text,
                    "file_sha256": _contract_hash(raw),
                    "range_sha256": _contract_hash(range_text),
                }
            )
    result = execute_plan(
        {
            "schema": "simplicio.mechanical-edit/v1",
            "touched_files": sorted(set(touched)),
            "operations": operations,
        },
        root=root,
        apply=write,
    )
    return {
        "adapter": "simplicio-dev-cli",
        "status": "executed",
        "result": result,
    }
