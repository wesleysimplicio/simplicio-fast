import pytest

from simplicio_fast.knowledge import KnowledgeFacade
from simplicio_fast.prism_arena import PrismArena
from simplicio_fast.parser_adapter import build_projection
from simplicio_fast.projection import ProjectionEnvelope, ProjectionError
from simplicio_fast.skills import SkillCatalog


def test_code_knowledge_and_operations_share_deterministic_envelope() -> None:
    encoded = []
    for projection_type in ("code", "knowledge", "operations"):
        envelope = ProjectionEnvelope.create(
            projection_type,
            producer="mapper",
            producer_schema="mapper.context/v1",
            generation="g1",
            stable_handle=f"{projection_type}:item-1",
            payload={"items": [{"id": "item-1", "value": 3}]},
        )
        assert ProjectionEnvelope.decode(envelope.encode()) == envelope
        encoded.append(envelope.encode())
    assert len({value for value in encoded}) == 3


def test_projection_bytes_are_deterministic_and_tamper_evident() -> None:
    first = ProjectionEnvelope.create(
        "code",
        producer="mapper",
        producer_schema="mapper.context/v1",
        generation="g1",
        stable_handle="symbol:abc",
        payload={"z": 1, "a": ["x", "y"]},
    )
    second = ProjectionEnvelope.create(
        "code",
        producer="mapper",
        producer_schema="mapper.context/v1",
        generation="g1",
        stable_handle="symbol:abc",
        payload={"a": ["x", "y"], "z": 1},
    )
    assert first.encode() == second.encode()
    tampered = first.encode().replace(b'"z":1', b'"z":2')
    with pytest.raises(ProjectionError, match="payload_digest_mismatch"):
        ProjectionEnvelope.decode(tampered)


def test_projection_rejects_future_schema_and_private_offsets() -> None:
    with pytest.raises(ProjectionError, match="projection_schema_unsupported"):
        ProjectionEnvelope.decode(b'{"schema":"simplicio.fast.projection/v2"}')
    with pytest.raises(ProjectionError, match="projection_exposes_offset"):
        ProjectionEnvelope.create(
            "code",
            producer="mapper",
            producer_schema="mapper.context/v1",
            generation="g1",
            stable_handle="symbol:abc",
            payload={"items": [{"mmap_offset": 4}]},
        )


def test_knowledge_and_operations_producers_use_the_same_abi(tmp_path) -> None:
    catalog = SkillCatalog(tmp_path, generation="g1", scope="repo")
    knowledge = KnowledgeFacade(catalog)
    knowledge_projection = knowledge.projection("find parser", sources=("skills",))
    assert knowledge_projection.projection_type == "knowledge"
    assert ProjectionEnvelope.decode(knowledge_projection.encode()) == knowledge_projection

    arena = PrismArena.publish(
        tmp_path / "arena", "org/repo", "source-1", {"a.py": b"value = 1\n"}
    )
    try:
        operations_projection = arena.projection()
        assert operations_projection.projection_type == "operations"
        assert ProjectionEnvelope.decode(operations_projection.encode()) == operations_projection
    finally:
        arena.close()


def test_code_parser_producer_uses_the_same_abi(tmp_path) -> None:
    (tmp_path / "service.py").write_text(
        "def parse_request(value):\n    return value\n", encoding="utf-8"
    )
    projection = build_projection(tmp_path, mode="bootstrap")
    assert projection.projection_type == "code"
    assert projection.producer_schema == "simplicio.fast.parser-adapter/v1"
    assert ProjectionEnvelope.decode(projection.encode()) == projection
