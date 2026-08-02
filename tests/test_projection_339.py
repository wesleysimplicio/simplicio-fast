import pytest

from simplicio_fast.projection import ProjectionEnvelope, ProjectionError


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
