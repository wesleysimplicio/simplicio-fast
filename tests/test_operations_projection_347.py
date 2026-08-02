import pytest

from simplicio_fast.operations_projection import OperationReceipt, OperationsProjection, OperationsProjectionError


def receipt(handle: str, sequence: int, status: str = "running") -> OperationReceipt:
    return OperationReceipt(handle, "attempt", status, "g1", sequence, "runtime.receipt/v1", {"slot": handle})


def test_operations_projection_is_read_only_bounded_and_incremental() -> None:
    projection = OperationsProjection("repo", "g1")
    delta = projection.ingest([receipt("a", 1), receipt("b", 2, "done")])
    assert delta["changed_handles"] == ["a", "b"]
    assert projection.query(status="done")[0]["handle"] == "b"
    assert projection.snapshot()["schema"] == "simplicio.fast.operations-projection/v1"


def test_operations_projection_rejects_stale_receipts_and_unbounded_queries() -> None:
    projection = OperationsProjection("repo", "g1")
    projection.ingest([receipt("a", 2)])
    with pytest.raises(OperationsProjectionError, match="receipt_sequence_regression"):
        projection.ingest([receipt("a", 1)])
    with pytest.raises(OperationsProjectionError, match="receipt_generation_mismatch"):
        projection.ingest([OperationReceipt("b", "attempt", "queued", "g2", 1, "x/v1", {})])
    with pytest.raises(OperationsProjectionError, match="query_budget_invalid"):
        projection.query(max_results=0)


def test_operations_projection_reports_slots_leases_and_read_only_stats() -> None:
    projection = OperationsProjection("repo", "g1")
    projection.ingest([
        OperationReceipt("slot:1", "slot", "active", "g1", 1, "loop/v1", {}),
        OperationReceipt("lease:1", "lease", "held", "g1", 2, "loop/v1", {"owner": "worker-a", "fence": "f1", "expires_at": 100}),
    ])
    assert projection.query_slots()[0]["handle"] == "lease:1"
    lease = projection.query_leases(50)[0]["lease"]
    assert lease["active"] is True
    assert lease["authority"] == "producer"
    assert projection.stats()["authority"] == "derived_read_only"
    assert projection.query_leases(100)[0]["lease"]["active"] is False


def test_operations_projection_blocks_complete_status_on_causal_gap() -> None:
    projection = OperationsProjection("repo", "g1")
    projection.ingest([
        OperationReceipt(
            "attempt:child",
            "attempt",
            "complete",
            "g1",
            2,
            "loop/v1",
            {"causal_parent": "attempt:missing"},
        )
    ])
    assert projection.query(status="complete") == []
    assert projection.query()[0]["consistency"] == "causal_gap"
    assert projection.snapshot()["consistency"] == {
        "causal_gaps": ["attempt:child"],
        "forks": [],
    }


def test_operations_projection_rejects_same_sequence_fork_but_allows_duplicate() -> None:
    projection = OperationsProjection("repo", "g1")
    item = receipt("a", 1)
    projection.ingest([item, item])
    with pytest.raises(OperationsProjectionError, match="receipt_fork_detected"):
        projection.ingest([OperationReceipt("a", "attempt", "failed", "g1", 1, "loop/v1", {})])
