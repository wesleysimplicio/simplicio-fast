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
