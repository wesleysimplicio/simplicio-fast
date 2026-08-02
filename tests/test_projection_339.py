from pathlib import Path

import pytest

from simplicio_fast.knowledge import KnowledgeFacade
from simplicio_fast.prism_arena import PrismArena
from simplicio_fast.parser_adapter import build_projection
from simplicio_fast.projection import (
    ProjectionEnvelope,
    ProjectionError,
    ProjectionStore,
    contract_manifest,
)
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


def test_shared_python_rust_projection_fixture_decodes() -> None:
    fixture = Path("fixtures/projection/v1/code-symbol.json").read_bytes()
    envelope = ProjectionEnvelope.decode(fixture)
    assert envelope.stable_handle == "code:symbol"
    assert envelope.encode() == fixture


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


def test_projection_manifest_and_provenance_fields_are_strict_and_deterministic() -> None:
    manifest = contract_manifest()
    assert manifest["envelope"] == {"schema": "simplicio.fast.projection/v1", "major": 1, "minor": 0}
    assert manifest["type_manifest"]["types"] == ["code", "knowledge", "operations"]
    envelope = ProjectionEnvelope.create(
        "code",
        producer="mapper",
        producer_schema="mapper.context/v1",
        generation="g1",
        stable_handle="symbol:abc",
        producer_version="2.0.20",
        repository_scope="org/repo",
        tenant_scope="tenant-a",
        domain_scope="code",
        capabilities_required=("projection.decode.v1",),
        budgets={"bytes": 1024},
        truncation_reasons=("budget",),
        payload={"name": "abc"},
    )
    decoded = ProjectionEnvelope.decode(envelope.encode())
    assert decoded.repository_scope == "org/repo"
    assert decoded.capabilities_required == ("projection.decode.v1",)
    assert decoded.budgets == {"bytes": 1024}
    assert decoded.truncation_reasons == ("budget",)
    with pytest.raises(ProjectionError, match="projection_depth_limit"):
        deep: object = "x"
        for _ in range(34):
            deep = {"deep": deep}
        ProjectionEnvelope.create(
            "code",
            producer="mapper",
            producer_schema="mapper.context/v1",
            generation="g1",
            stable_handle="symbol:abc",
            payload=deep,
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


def test_projection_store_applies_bounded_delta_and_reports_closure() -> None:
    store = ProjectionStore("repo-a")
    first = ProjectionEnvelope.create(
        "code",
        producer="mapper",
        producer_schema="mapper.context/v1",
        generation="g1",
        stable_handle="symbol:a",
        payload={"repository": "repo-a", "name": "a"},
    )
    second = ProjectionEnvelope.create(
        "knowledge",
        producer="skills",
        producer_schema="skills/v1",
        generation="g1",
        stable_handle="skill:b",
        payload={"repository": "repo-a", "name": "b"},
    )
    store.publish(first)
    receipt = store.apply_delta(
        "g1", changed=(second,), deleted_handles=("symbol:a",), closure_handles=("symbol:c",)
    )
    assert receipt["changed_handles"] == ["skill:b"]
    assert receipt["deleted_handles"] == ["symbol:a"]
    assert receipt["closure_handles"] == ["skill:b", "symbol:a", "symbol:c"]
    assert [item["stable_handle"] for item in store.snapshot()] == ["skill:b"]


def test_projection_store_rejects_stale_and_cross_repository_data() -> None:
    store = ProjectionStore("repo-a")
    foreign = ProjectionEnvelope.create(
        "operations",
        producer="runtime",
        producer_schema="runtime/v1",
        generation="g2",
        stable_handle="slot:1",
        payload={"repository": "repo-b"},
    )
    with pytest.raises(ProjectionError, match="projection_repository_mismatch"):
        store.publish(foreign)
    local = ProjectionEnvelope.create(
        "operations",
        producer="runtime",
        producer_schema="runtime/v1",
        generation="g1",
        stable_handle="slot:1",
        payload={"repository": "repo-a"},
    )
    store.publish(local)
    with pytest.raises(ProjectionError, match="projection_generation_stale"):
        store.apply_delta("g2", changed=())


def test_projection_store_save_load_is_atomic_and_tamper_evident(tmp_path) -> None:
    store = ProjectionStore("repo-a")
    record = ProjectionEnvelope.create(
        "knowledge",
        producer="skills",
        producer_schema="skills/v1",
        generation="g1",
        stable_handle="skill:a",
        payload={"repository": "repo-a", "name": "a"},
    )
    store.publish(record)
    path = tmp_path / "derived" / "projection.json"
    receipt = store.save(path)
    assert receipt["status"] == "saved"
    restored = ProjectionStore.load(path, "repo-a")
    assert restored.snapshot() == store.snapshot()
    path.write_text(path.read_text(encoding="utf-8").replace("skill:a", "skill:b"), encoding="utf-8")
    with pytest.raises(ProjectionError, match="projection_store_digest_mismatch"):
        ProjectionStore.load(path, "repo-a")
    with pytest.raises(ProjectionError, match="projection_repository_mismatch"):
        ProjectionStore.load(tmp_path / "derived" / "projection.json", "repo-b")
