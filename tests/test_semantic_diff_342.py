import pytest

from simplicio_fast.semantic_diff import SemanticDiffError, diff_generations


def test_diff_and_overlay_are_deterministic_without_mutating_inputs() -> None:
    before = {"a": {"name": "a"}, "gone": {"name": "gone"}}
    after = {"a": {"name": "changed"}, "new": {"name": "new"}}
    result = diff_generations(before, after, source_generation="g1", proposed_generation="g2")
    assert [item.kind for item in result.records] == ["update", "remove", "add"]
    assert result.encode()
    assert result.overlay().base_generation == "g1"
    assert before == {"a": {"name": "a"}, "gone": {"name": "gone"}}


def test_impact_explains_bounded_dependency_closure_and_partial_state() -> None:
    result = diff_generations({"a": {}}, {"a": {"x": 1}}, source_generation="g1", proposed_generation="g2")
    impact = result.impact({"a": ["b"], "b": ["c"]}, max_nodes=2)
    assert impact["nodes"] == ["a", "b"]
    assert impact["reasons"]["b"] == "dependency_closure"
    assert impact["complete"] is False


def test_derived_changes_require_uncertainty_and_invalid_budget_fails() -> None:
    with pytest.raises(SemanticDiffError, match="derived_confidence_invalid"):
        from simplicio_fast.semantic_diff import DiffRecord

        DiffRecord("a", "update", {}, {}, "g1", "g2", "rename", derived=True)
    result = diff_generations({}, {"a": {}}, source_generation="g1", proposed_generation="g2")
    with pytest.raises(SemanticDiffError, match="impact_budget_invalid"):
        result.impact({}, max_nodes=0)
