"""Fail-closed validation for Mapper-owned Fast handoff artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA = "simplicio.fast.mapper-ingest/v1"
HANDOFF_SCHEMA = "simplicio.mapper-fast-handoff/v1"


class MapperIngestError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
        close_fds=True,
    )
    if result.returncode != 0 or len(result.stdout.strip()) != 40:
        raise MapperIngestError("mapper_commit_mismatch")
    return result.stdout.strip()


def validate_handoff(root: Path, envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate a real ``simplicio-mapper fast-handoff`` JSON envelope.

    The adapter only consumes documented metadata and artifact digests. It does
    not reinterpret Mapper graph nodes or manufacture stable IDs.
    """
    if not isinstance(envelope, dict):
        raise MapperIngestError("mapper_schema_unsupported")
    handoff = envelope.get("handoff")
    receipt = envelope.get("receipt")
    if not isinstance(handoff, dict) or not isinstance(receipt, dict):
        raise MapperIngestError("mapper_schema_unsupported")
    if handoff.get("schema") != HANDOFF_SCHEMA:
        raise MapperIngestError("mapper_schema_unsupported")
    if receipt.get("schema") != "simplicio.mapper-fast-handoff-receipt/v1":
        raise MapperIngestError("mapper_schema_unsupported")
    if receipt.get("status") != "parsed" or receipt.get("handoff_sha256") is None:
        raise MapperIngestError("mapper_incomplete")
    if handoff.get("repository_id") != root.name:
        raise MapperIngestError("mapper_repository_mismatch")
    revision = handoff.get("revision")
    if revision != _head(root):
        raise MapperIngestError("mapper_commit_mismatch")
    fidelity = handoff.get("fidelity", {})
    if not isinstance(fidelity, dict) or fidelity.get("gate") != "ready":
        raise MapperIngestError("mapper_incomplete")
    artifacts = handoff.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise MapperIngestError("mapper_incomplete")
    checked: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise MapperIngestError("mapper_schema_unsupported")
        relative = artifact.get("path")
        if not isinstance(relative, str):
            raise MapperIngestError("mapper_schema_unsupported")
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise MapperIngestError("mapper_digest_mismatch", relative)
        digest, size = _sha256(path)
        if digest != artifact.get("sha256") or size != artifact.get("bytes"):
            raise MapperIngestError("mapper_digest_mismatch", relative)
        checked.append(
            {"name": artifact.get("name"), "path": relative, "sha256": digest}
        )
    return {
        "schema": SCHEMA,
        "mode": "integrated",
        "producer": {
            "name": "simplicio-mapper",
            "version": handoff.get("producer", {}).get("version"),
        },
        "repository_id": handoff["repository_id"],
        "commit": revision,
        "generation": handoff.get("generation"),
        "handoff_sha256": receipt["handoff_sha256"],
        "artifacts": checked,
        "changed_paths": handoff.get("delta", {}).get("changed_paths", []),
    }


def load_handoff(root: Path, path: Path | None = None) -> dict[str, Any]:
    source = path or root / ".simplicio" / "fast-handoff.json"
    try:
        envelope = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MapperIngestError("mapper_missing", str(source)) from error
    return validate_handoff(root, envelope)


__all__ = [
    "HANDOFF_SCHEMA",
    "MapperIngestError",
    "SCHEMA",
    "load_handoff",
    "validate_handoff",
]
