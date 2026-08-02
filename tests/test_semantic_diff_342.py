from types import SimpleNamespace

import pytest

from simplicio_fast.federation import FederatedEdge, FederationMember, compile_federation
from simplicio_fast.semantic_diff import DiffRecord, SemanticDiff, SemanticDiffError, WhatIfOverlay, _canonical, diff_generations


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
        [FederationMember("repo-a", "commit-a", "g1", "projection/v1", "sha256:" + "a" * 64), FederationMember("repo-b", "commit-b", "g1", "projection/v1", "sha256:" + "b" * 64)],
        [FederatedEdge("repo-a:schema", "repo-b:consumer", "depends", 1.0, ("fixture:edge",))],
    )
    impact = result.impact_federated(federation)
    assert impact["nodes"] == ["repo-a:schema", "repo-b:consumer"]
    assert impact["reasons"]["repo-b:consumer"] == "federated_consumer"
    assert impact["federation_generation"] == federation.generation


def test_diff_contract_rejects_invalid_identity_generation_confidence_and_reason() -> None:
    cases = [
        ({"handle": "", "kind": "add", "before": None, "after": {}, "reason_code": "add"}, "stable_handle_invalid"),
        ({"handle": "h", "kind": "other", "before": None, "after": {}, "reason_code": "add"}, "diff_kind_invalid"),
        ({"handle": "h", "kind": "add", "before": None, "after": {}, "source_generation": "", "reason_code": "add"}, "generation_invalid"),
        ({"handle": "h", "kind": "add", "before": None, "after": {}, "confidence": True, "reason_code": "add"}, "confidence_invalid"),
        ({"handle": "h", "kind": "add", "before": None, "after": {}, "reason_code": ""}, "reason_code_invalid"),
    ]
    for values, reason in cases:
        values = {"source_generation": "g1", "proposed_generation": "g2", **values}
        with pytest.raises(SemanticDiffError, match=reason):
            DiffRecord(**values)
    with pytest.raises(SemanticDiffError, match="derived_confidence_invalid"):
        DiffRecord("h", "add", None, {}, "g1", "g2", "add", derived=True)
    with pytest.raises(SemanticDiffError, match="diff_not_json"):
        _canonical({"bad": object()})


def test_diff_overlay_impact_budget_and_duplicate_walk_edges() -> None:
    record = DiffRecord("a", "update", {}, {"x": 1}, "g1", "g2", "update")
    with pytest.raises(SemanticDiffError, match="generation_invalid"):
        WhatIfOverlay("", [])
    with pytest.raises(SemanticDiffError, match="generation_invalid"):
        SemanticDiff("", "g2", [])
    result = SemanticDiff("g1", "g2", [record], complete=False, truncation_reasons=("budget",))
    overlay = result.overlay()
    assert overlay.to_dict()["schema"] == "simplicio.fast.what-if-overlay/v1"
    assert overlay.digest.startswith("sha256:")
    assert overlay.encode().endswith(b"\n")
    with pytest.raises(SemanticDiffError, match="impact_budget_invalid"):
        result.impact({}, max_nodes=0)
    with pytest.raises(SemanticDiffError, match="impact_budget_invalid"):
        result.impact_federated(SimpleNamespace(generation="g1", dependencies=lambda _: []), max_nodes=0)
    impact = SemanticDiff("g1", "g2", [record, record]).impact({"a": ["a", "b"], "b": ["a"]}, max_nodes=10)
    assert impact["complete"] is True
    federation = SimpleNamespace(
        generation="g1",
        dependencies=lambda handle: (
            [{"target_handle": "b"}, {"target_handle": "b"}] if handle == "a" else []
        ),
    )
    federated = SemanticDiff("g1", "g2", [record, record], complete=False, truncation_reasons=("budget",)).impact_federated(federation)
    assert federated["complete"] is False
    assert federated["truncation_reasons"] == ["budget"]
    assert diff_generations({"same": {"x": 1}}, {"same": {"x": 1}}, source_generation="g1", proposed_generation="g2").records == ()
