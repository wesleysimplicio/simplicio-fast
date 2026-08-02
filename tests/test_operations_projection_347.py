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
