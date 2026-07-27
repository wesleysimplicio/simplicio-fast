from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


MINIMUM_MAPPER = (0, 24, 2)
MINIMUM_DEV_CLI = (0, 16, 3)


def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    parts: list[int] = []
    for part in value.split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _executable(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        sibling = Path(sys.executable).with_name(name)
        if sibling.is_file():
            return str(sibling)
    return None


def integration_status() -> dict[str, Any]:
    """Return the single compatibility decision used by doctor and receipts.

    The status is deliberately fail-closed: package metadata without an
    executable, or an executable below the tested contract, is not integrated.
    """
    mapper_version = _distribution_version("simplicio-mapper")
    dev_cli_version = _distribution_version("simplicio-cli")
    dev_cli_executable = _executable("simplicio-dev-cli", "simplicio-cli")
    mapper_ok = bool(
        mapper_version
        and _executable("simplicio-mapper")
        and (_version_tuple(mapper_version) or ()) >= MINIMUM_MAPPER
    )
    dev_cli_ok = bool(
        dev_cli_version
        and dev_cli_executable
        and (_version_tuple(dev_cli_version) or ()) >= MINIMUM_DEV_CLI
    )
    return {
        "schema": "simplicio.fast.integration-status/v1",
        "mapper": {
            "package": "simplicio-mapper",
            "version": mapper_version,
            "minimum": ".".join(map(str, MINIMUM_MAPPER)),
            "executable": _executable("simplicio-mapper"),
            "compatible": mapper_ok,
        },
        "dev_cli": {
            "package": "simplicio-cli",
            "version": dev_cli_version,
            "minimum": ".".join(map(str, MINIMUM_DEV_CLI)),
            "executable": dev_cli_executable,
            "compatible": dev_cli_ok,
        },
        "integrated_ready": mapper_ok and dev_cli_ok,
    }


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
        close_fds=True,
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
