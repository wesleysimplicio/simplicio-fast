from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from simplicio_fast.mapper_ingest import MapperIngestError, validate_handoff


def _envelope(root: Path, commit: str) -> dict[str, object]:
    artifact = root / ".simplicio" / "context-snapshot.json"
    artifact.parent.mkdir()
    artifact.write_text(
        '{"schema":"simplicio.context-snapshot/v1"}\n', encoding="utf-8"
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return {
        "handoff": {
            "schema": "simplicio.mapper-fast-handoff/v1",
            "repository_id": root.name,
            "revision": commit,
            "generation": "g1",
            "producer": {"name": "simplicio-mapper", "version": "0.26.5"},
            "fidelity": {"gate": "ready"},
            "artifacts": [
                {
                    "name": "context_snapshot",
                    "path": ".simplicio/context-snapshot.json",
                    "bytes": artifact.stat().st_size,
                    "sha256": digest,
                }
            ],
            "delta": {"changed_paths": []},
        },
        "receipt": {
            "schema": "simplicio.mapper-fast-handoff-receipt/v1",
            "status": "parsed",
            "handoff_sha256": "a" * 64,
        },
    }


def test_validates_mapper_owned_handoff_and_artifact_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with patch("simplicio_fast.mapper_ingest._head", return_value="a" * 40):
        provenance = validate_handoff(root, _envelope(root, "a" * 40))
    assert provenance["mode"] == "integrated"
    assert provenance["producer"]["name"] == "simplicio-mapper"
    assert provenance["generation"] == "g1"


def test_tampered_mapper_artifact_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with patch("simplicio_fast.mapper_ingest._head", return_value="a" * 40):
        envelope = _envelope(root, "a" * 40)
    artifact = root / ".simplicio" / "context-snapshot.json"
    artifact.write_text('{"tampered":true}\n', encoding="utf-8")
    with patch("simplicio_fast.mapper_ingest._head", return_value="a" * 40):
        with pytest.raises(MapperIngestError, match="mapper_digest_mismatch"):
            validate_handoff(root, envelope)
