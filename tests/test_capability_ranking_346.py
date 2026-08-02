import pytest

from simplicio_fast.capability_ranking import CapabilityCandidate, CapabilityRankingError, rank_capabilities


def test_capability_ranking_is_explainable_and_advisory() -> None:
    result = rank_capabilities(
        [
            CapabilityCandidate("worker:b", "worker", "1", ("query",), estimated_cost=1, provenance=("manifest:b",), policy_eligible=True),
            CapabilityCandidate("worker:a", "worker", "1", ("query", "rust"), estimated_cost=4, provenance=("manifest:a",), policy_eligible=True),
        ],
        ("query", "rust"),
    )
    assert result["authority"] == "advisory_only"
    assert result["candidates"][0]["handle"] == "worker:a"
    assert result["candidates"][0]["missing_capabilities"] == []
    assert result["candidates"][0]["eligible"] is True
    assert result["candidates"][0]["score_components"]["matched_capabilities"] == 200
    assert result["candidates"][1]["selection_reason"] == "missing_required_capabilities"


def test_capability_ranking_is_bounded_and_rejects_invalid_requests() -> None:
    result = rank_capabilities([CapabilityCandidate("a", "tool", "1", ("x",), policy_eligible=True)], ("x",), max_results=1)
    assert result["truncated"] is False
    with pytest.raises(CapabilityRankingError, match="ranking_request_invalid"):
        rank_capabilities([], (), max_results=1)


def test_capability_ranking_separates_hard_filters_and_unknown_metrics() -> None:
    result = rank_capabilities(
        [
            CapabilityCandidate("unknown", "worker", "1", ("query",), policy_eligible=None),
            CapabilityCandidate("other-tenant", "worker", "1", ("query",), policy_eligible=True, scope="tenant-b"),
        ],
        ("query",),
        required_scope="tenant-a",
    )
    assert all(item["eligible"] is False for item in result["candidates"])
    by_handle = {item["handle"]: item for item in result["candidates"]}
    assert by_handle["unknown"]["selection_reason"] == "policy_unknown"
    assert by_handle["unknown"]["metric_class"] == "unknown"
    assert by_handle["other-tenant"]["selection_reason"] == "scope_mismatch"


def test_capability_ranking_accepts_explicit_global_scope_and_rejects_bad_inputs() -> None:
    global_candidate = CapabilityCandidate(
        "global", "tool", "1", ("query",), policy_eligible=True, scope="*"
    )
    result = rank_capabilities([global_candidate], ("query",), required_scope="tenant-a")
    assert result["candidates"][0]["eligible"] is True
    with pytest.raises(CapabilityRankingError, match="ranking_request_invalid"):
        rank_capabilities([], (1,))
    with pytest.raises(CapabilityRankingError, match="candidate_scope_invalid"):
        CapabilityCandidate("bad", "tool", "1", ("query",), scope="")


def test_capability_ranking_rejects_coercible_invalid_types() -> None:
    with pytest.raises(CapabilityRankingError, match="candidate_cost_invalid"):
        CapabilityCandidate("float", "tool", "1", ("query",), estimated_cost=1.5)
    with pytest.raises(CapabilityRankingError, match="candidate_policy_invalid"):
        CapabilityCandidate("policy", "tool", "1", ("query",), policy_eligible="yes")
    with pytest.raises(CapabilityRankingError, match="candidate_provenance_invalid"):
        CapabilityCandidate("provenance", "tool", "1", ("query",), provenance=(1,))
    with pytest.raises(CapabilityRankingError, match="ranking_request_invalid"):
        rank_capabilities([], ("query",), max_results=True)
    with pytest.raises(CapabilityRankingError, match="ranking_request_invalid"):
        rank_capabilities([], ("query",), required_scope=True)
