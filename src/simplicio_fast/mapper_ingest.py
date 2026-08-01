"""Fail-closed validation for Mapper-owned Fast handoff artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "simplicio.fast.mapper-ingest/v1"
HANDOFF_SCHEMA = "simplicio.mapper-fast-handoff/v1"


class MapperIngestError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _head(root: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="simplicio-fast-git-") as directory:
        stdout_path = Path(directory) / "stdout.txt"
        stderr_path = Path(directory) / "stderr.txt"
        with (
            stdout_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
                close_fds=True,
            )
        commit = stdout_path.read_text(encoding="utf-8").strip()
    if result.returncode != 0 or len(commit) != 40:
        raise MapperIngestError("mapper_commit_mismatch")
    return commit


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
    if receipt.get("status") not in {"parsed", "reused"} or not _is_digest(
        receipt.get("handoff_sha256")
    ):
        raise MapperIngestError("mapper_incomplete")
    if handoff.get("repository_id") != root.name:
        raise MapperIngestError("mapper_repository_mismatch")
    revision = handoff.get("revision")
    if revision != _head(root):
        raise MapperIngestError("mapper_commit_mismatch")
    generation = handoff.get("generation")
    if not isinstance(generation, str) or not generation.strip():
        raise MapperIngestError("mapper_generation_stale")
    producer = handoff.get("producer")
    if producer is not None and (
        not isinstance(producer, dict)
        or producer.get("name") not in {None, "simplicio-mapper"}
        or (
            producer.get("version") is not None
            and not isinstance(producer.get("version"), str)
        )
    ):
        raise MapperIngestError("mapper_schema_unsupported")
    fidelity = handoff.get("fidelity")
    if not isinstance(fidelity, dict):
        counters = receipt.get("counters")
        if (
            not isinstance(counters, dict)
            or counters.get("parsed", 0) + counters.get("reused", 0) < 1
            or counters.get("degraded", 1) != 0
            or counters.get("fallback", 1) != 0
        ):
            raise MapperIngestError("mapper_incomplete")
        fidelity = {"gate": "ready", "source": "receipt-counters"}
    if fidelity.get("gate") != "ready":
        raise MapperIngestError("mapper_incomplete")
    artifacts = handoff.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise MapperIngestError("mapper_incomplete")
    checked: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise MapperIngestError("mapper_schema_unsupported")
        relative = artifact.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(artifact.get("bytes"), int)
            or isinstance(artifact.get("bytes"), bool)
            or artifact.get("bytes") < 0
            or not _is_digest(artifact.get("sha256"))
        ):
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
        "generation": generation,
        "fidelity": fidelity,
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
