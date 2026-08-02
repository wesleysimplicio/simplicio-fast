import pytest

from simplicio_fast.projection import ProjectionEnvelope
from simplicio_fast.universal_context import UniversalContextError, compile_context


def projection(kind: str, handle: str) -> ProjectionEnvelope:
    return ProjectionEnvelope.create(
        kind,
        producer="producer",
        producer_schema="producer/v1",
        generation="g1",
        stable_handle=handle,
        payload={"repository": "repo", "value": handle},
    )


def test_universal_context_is_deterministic_and_marks_facts_not_instructions() -> None:
    result = compile_context([projection("operations", "o"), projection("code", "c"), projection("knowledge", "k")], repository_scope="repo")
    assert [item["projection_type"] for item in result["projections"]] == ["code", "knowledge", "operations"]
    assert result["authority"] == "facts_only"
    assert result["instructions"] is False
    assert result["truncated"] is False


def test_universal_context_budget_and_scope_fail_closed() -> None:
    with pytest.raises(UniversalContextError, match="context_scope_mismatch"):
        compile_context([projection("code", "c")], repository_scope="other")
    result = compile_context([projection("code", "c"), projection("knowledge", "k")], max_items=1)
    assert result["truncated"] is True
    assert result["truncation_reasons"] == ["item_budget"]
    with pytest.raises(UniversalContextError, match="context_budget_invalid"):
        compile_context([], max_tokens=0)


def test_universal_context_exposes_boundaries_and_wrapper_accounting() -> None:
    untrusted = ProjectionEnvelope.create(
        "knowledge",
        producer="mapper",
        producer_schema="mapper/v1",
        generation="g2",
        stable_handle="k",
        domain_scope="knowledge",
        payload={
            "repository": "repo",
            "value": "retrieved text",
            "trust": "untrusted",
            "content_class": "untrusted_text",
            "freshness": "stale",
        },
    )
    result = compile_context(
        [untrusted, projection("operations", "o")],
        repository_scope="repo",
        domain_caps={"knowledge": 1},
        wrapper_bytes=7,
        wrapper_tokens=3,
    )
    item = next(item for item in result["projections"] if item["projection_type"] == "knowledge")
    assert item["digest"].startswith("sha256:")
    assert item["trust"] == "untrusted"
    assert item["freshness"] == "stale"
    assert item["trusted_for_instruction"] is False
    assert result["wrapper_bytes"] == 7
    assert result["wrapper_tokens"] == 3
    assert result["source_generations"] == ["g1", "g2"]
    assert item["source_generation"] == "g2"
    assert item["projection_generation"] == "g2"


def test_universal_context_rejects_conflicting_duplicate_and_impossible_wrapper() -> None:
    first = projection("knowledge", "same")
    second = ProjectionEnvelope.create(
        "knowledge",
        producer="producer",
        producer_schema="producer/v1",
        generation="g1",
        stable_handle="same",
        payload={"repository": "repo", "value": "different"},
    )
    with pytest.raises(UniversalContextError, match="context_conflict"):
        compile_context([first, second])
    with pytest.raises(UniversalContextError, match="context_budget_invalid"):
        compile_context([], max_bytes=3, wrapper_bytes=4)


def test_universal_context_rejects_tenant_scope_mismatch() -> None:
    item = ProjectionEnvelope.create(
        "knowledge",
        producer="mapper",
        producer_schema="mapper/v1",
        generation="g1",
        stable_handle="tenant-fact",
        tenant_scope="tenant-a",
        payload={"repository": "repo", "value": "scoped"},
    )
    with pytest.raises(UniversalContextError, match="context_scope_mismatch"):
        compile_context([item], repository_scope="repo", tenant_scope="tenant-b")


def test_universal_context_rejects_boolean_budget_values() -> None:
    with pytest.raises(UniversalContextError, match="context_budget_invalid"):
        compile_context([], max_tokens=True)
    with pytest.raises(UniversalContextError, match="context_budget_invalid"):
        compile_context([], wrapper_bytes=False)
