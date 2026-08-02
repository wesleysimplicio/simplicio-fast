import pytest

from simplicio_fast.capability_ranking import CapabilityCandidate, CapabilityRankingError, rank_capabilities


def test_capability_ranking_is_explainable_and_advisory() -> None:
    result = rank_capabilities(
        [
            CapabilityCandidate("worker:b", "worker", "1", ("query",), estimated_cost=1, provenance=("manifest:b",)),
            CapabilityCandidate("worker:a", "worker", "1", ("query", "rust"), estimated_cost=4, provenance=("manifest:a",)),
        ],
        ("query", "rust"),
    )
    assert result["authority"] == "advisory_only"
    assert result["candidates"][0]["handle"] == "worker:a"
    assert result["candidates"][0]["missing_capabilities"] == []
    assert result["candidates"][1]["selection_reason"] == "missing_required_capabilities"


def test_capability_ranking_is_bounded_and_rejects_invalid_requests() -> None:
    result = rank_capabilities([CapabilityCandidate("a", "tool", "1", ("x",))], ("x",), max_results=1)
    assert result["truncated"] is False
    with pytest.raises(CapabilityRankingError, match="ranking_request_invalid"):
        rank_capabilities([], (), max_results=1)
