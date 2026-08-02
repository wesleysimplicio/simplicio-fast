import pytest

from simplicio_fast.federation import FederatedEdge, FederationMember, compile_federation
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


def test_diff_rejects_incoherent_shapes_and_duplicate_overlay_records() -> None:
    from simplicio_fast.semantic_diff import DiffRecord, WhatIfOverlay

    with pytest.raises(SemanticDiffError, match="diff_shape_invalid"):
        DiffRecord("a", "add", {"old": 1}, {"new": 1}, "g1", "g2", "bad")
    record = DiffRecord("a", "add", None, {"new": 1}, "g1", "g2", "handle_added")
    with pytest.raises(SemanticDiffError, match="duplicate_diff_record"):
        WhatIfOverlay("g1", [record, record])
    with pytest.raises(SemanticDiffError, match="overlay_generation_mismatch"):
        WhatIfOverlay(
            "g2",
            [DiffRecord("b", "add", None, {"new": 1}, "g1", "g2", "handle_added")],
        )
    with pytest.raises(SemanticDiffError, match="diff_payload_invalid"):
        DiffRecord("c", "add", None, [], "g1", "g2", "handle_added")


def test_federated_impact_requires_and_records_pinned_manifest() -> None:
    result = diff_generations({"repo-a:schema": {}}, {"repo-a:schema": {"v": 2}}, source_generation="g1", proposed_generation="g2")
    federation = compile_federation(
        [FederationMember("repo-a", "commit-a", "g1", "projection/v1", "sha256:a"), FederationMember("repo-b", "commit-b", "g1", "projection/v1", "sha256:b")],
        [FederatedEdge("repo-a:schema", "repo-b:consumer", "depends", 1.0, ("fixture:edge",))],
    )
    impact = result.impact_federated(federation)
    assert impact["nodes"] == ["repo-a:schema", "repo-b:consumer"]
    assert impact["reasons"]["repo-b:consumer"] == "federated_consumer"
    assert impact["federation_generation"] == federation.generation
