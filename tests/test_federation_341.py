import pytest

from simplicio_fast.federation import (
    FederatedEdge,
    FederationError,
    FederationMember,
    compile_federation,
)


def member(repo: str, generation: str = "g1") -> FederationMember:
    return FederationMember(repo, "commit-" + repo, generation, "projection/v1", "sha256:" + repo)


def test_federation_is_pinned_deterministic_and_bounded() -> None:
    edge = FederatedEdge("repo-a:schema/x", "repo-b:consumer/y", "depends", 1.0, ("fixture:1",))
    first = compile_federation([member("repo-b"), member("repo-a")], [edge])
    second = compile_federation([member("repo-a"), member("repo-b")], [edge])
    assert first.encode() == second.encode()
    assert first.manifest()["body"]["generation"] == first.generation
    assert first.consumers("repo-b:consumer/y")[0]["source_handle"] == "repo-a:schema/x"
    assert first.dependencies("repo-a:schema/x")[0]["target_handle"] == "repo-b:consumer/y"


def test_federation_traversal_preserves_provenance_paths() -> None:
    edges = [
        FederatedEdge("a", "b", "depends", 0.9, ("a.json:1",)),
        FederatedEdge("b", "c", "depends", 0.8, ("b.json:2",), derived=True),
    ]
    result = compile_federation([member("a"), member("b"), member("c")], edges).traverse("a")
    assert result["nodes"] == ["a", "b", "c"]
    assert result["paths"]["c"] == ["a", "b", "c"]
    assert result["complete"] is True


def test_federation_rejects_duplicate_scope_tombstone_and_unproven_edge() -> None:
    with pytest.raises(FederationError, match="duplicate_member_repository"):
        compile_federation([member("Repo"), member("repo")])
    with pytest.raises(FederationError, match="member_tombstone_present"):
        compile_federation([FederationMember("repo", "c", "g", "s", "sha256:x", tombstone=True)])
    with pytest.raises(FederationError, match="derived_edge_evidence_missing"):
        FederatedEdge("a", "b", "depends", 1.0, derived=True)


def test_federation_rejects_unbounded_traversal() -> None:
    with pytest.raises(FederationError, match="traversal_budget_invalid"):
        compile_federation([member("repo")]).traverse("repo", max_nodes=0)
