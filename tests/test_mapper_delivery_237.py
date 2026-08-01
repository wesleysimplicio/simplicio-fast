import json
from pathlib import Path

import pytest

from simplicio_fast.delivery import _mapper_symbol_handles
from simplicio_fast.mapper_ingest import MapperIngestError


def _provenance(path: str = ".simplicio/context-snapshot.json") -> dict[str, object]:
    return {"artifacts": [{"name": "context_snapshot", "path": path}]}


def test_mapper_symbol_handles_preserve_public_ids(tmp_path: Path) -> None:
    artifact = tmp_path / ".simplicio" / "context-snapshot.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "schema": "simplicio.context-snapshot/v1",
                "graph": {
                    "nodes": [
                        {
                            "id": "symbol:service.py::run",
                            "source": {"file": "service.py", "line": 7},
                        },
                        {
                            "id": "file:service.py",
                            "source": {"file": "service.py", "line": 1},
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    assert _mapper_symbol_handles(tmp_path, _provenance()) == {
        ("service.py", 7): "symbol:service.py::run"
    }


def test_integrated_traceability_fails_closed_without_symbol_nodes(tmp_path: Path) -> None:
    artifact = tmp_path / ".simplicio" / "context-snapshot.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "schema": "simplicio.context-snapshot/v1",
                "graph": {"nodes": [{"id": "file:service.py"}]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(MapperIngestError, match="mapper_graph_missing"):
        _mapper_symbol_handles(tmp_path, _provenance())
