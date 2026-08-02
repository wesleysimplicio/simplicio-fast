"""Read-only bounded projection of versioned operational receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


RECEIPT_SCHEMA = "simplicio.fast.operations-receipt/v1"
PROJECTION_SCHEMA = "simplicio.fast.operations-projection/v1"


class OperationsProjectionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    handle: str
    kind: str
    status: str
    generation: str
    sequence: int
    source_schema: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.handle, self.kind, self.status, self.generation, self.source_schema)):
            raise OperationsProjectionError("receipt_identity_invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise OperationsProjectionError("receipt_sequence_invalid")
        if not isinstance(self.payload, dict):
            raise OperationsProjectionError("receipt_payload_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "handle": self.handle,
            "kind": self.kind,
            "status": self.status,
            "generation": self.generation,
            "sequence": self.sequence,
            "source_schema": self.source_schema,
            "payload": dict(self.payload),
        }


class OperationsProjection:
    """Derived view fed by explicit exports; it does not own operational state."""

    def __init__(self, repository: str, generation: str) -> None:
        if not repository or not generation:
            raise OperationsProjectionError("projection_scope_invalid")
        self.repository = repository
        self.generation = generation
        self._receipts: dict[str, OperationReceipt] = {}

    def ingest(self, receipts: Iterable[OperationReceipt]) -> dict[str, Any]:
        changed: list[str] = []
        for receipt in receipts:
            if receipt.generation != self.generation:
                raise OperationsProjectionError("receipt_generation_mismatch")
            previous = self._receipts.get(receipt.handle)
            if previous is not None and receipt.sequence < previous.sequence:
                raise OperationsProjectionError("receipt_sequence_regression")
            self._receipts[receipt.handle] = receipt
            changed.append(receipt.handle)
        return {"schema": "simplicio.fast.operations-delta/v1", "repository": self.repository, "generation": self.generation, "changed_handles": sorted(set(changed))}

    def query(self, *, status: str | None = None, kind: str | None = None, max_results: int = 1000) -> list[dict[str, Any]]:
        if max_results <= 0:
            raise OperationsProjectionError("query_budget_invalid")
        values = [item for item in self._receipts.values() if (status is None or item.status == status) and (kind is None or item.kind == kind)]
        return [item.to_dict() for item in sorted(values, key=lambda item: (item.sequence, item.handle), reverse=True)[:max_results]]

    def snapshot(self) -> dict[str, Any]:
        return {"schema": PROJECTION_SCHEMA, "repository": self.repository, "generation": self.generation, "receipts": self.query()}


__all__ = ["OperationReceipt", "OperationsProjection", "OperationsProjectionError", "PROJECTION_SCHEMA", "RECEIPT_SCHEMA"]
