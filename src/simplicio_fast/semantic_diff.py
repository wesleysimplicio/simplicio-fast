"""Deterministic, read-only semantic diff and what-if overlays."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


DIFF_SCHEMA = "simplicio.fast.semantic-diff/v1"
OVERLAY_SCHEMA = "simplicio.fast.what-if-overlay/v1"
RECEIPT_SCHEMA = "simplicio.fast.simulation-receipt/v1"
_KINDS = {"add", "remove", "update"}


class SemanticDiffError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SemanticDiffError("diff_not_json") from error


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class DiffRecord:
    handle: str
    kind: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    source_generation: str
    proposed_generation: str
    reason_code: str
    confidence: float = 1.0
    derived: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.handle, str) or not self.handle.strip():
            raise SemanticDiffError("stable_handle_invalid")
        if self.kind not in _KINDS:
            raise SemanticDiffError("diff_kind_invalid")
        if (
            (self.kind == "add" and (self.before is not None or self.after is None))
            or (self.kind == "remove" and (self.before is None or self.after is not None))
            or (self.kind == "update" and (self.before is None or self.after is None))
        ):
            raise SemanticDiffError("diff_shape_invalid")
        if not self.source_generation or not self.proposed_generation:
            raise SemanticDiffError("generation_invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise SemanticDiffError("confidence_invalid")
        if self.derived and self.confidence >= 1.0:
            raise SemanticDiffError("derived_confidence_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "kind": self.kind,
            "before": dict(self.before) if self.before is not None else None,
            "after": dict(self.after) if self.after is not None else None,
            "source_generation": self.source_generation,
            "proposed_generation": self.proposed_generation,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "derived": self.derived,
        }


class WhatIfOverlay:
    """Immutable proposal view; no source or base records are modified."""

    def __init__(self, base_generation: str, records: Sequence[DiffRecord]) -> None:
        if not base_generation:
            raise SemanticDiffError("generation_invalid")
        keys = [(item.handle, item.kind) for item in records]
        if len(keys) != len(set(keys)):
            raise SemanticDiffError("duplicate_diff_record")
        self.base_generation = base_generation
        self.records = tuple(sorted(records, key=lambda item: (item.handle, item.kind)))

    @property
    def digest(self) -> str:
        return _digest({"base_generation": self.base_generation, "records": [item.to_dict() for item in self.records]})

    def encode(self) -> bytes:
        return _canonical(self.to_dict()) + b"\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OVERLAY_SCHEMA,
            "base_generation": self.base_generation,
            "overlay_digest": self.digest,
            "records": [item.to_dict() for item in self.records],
        }


class SemanticDiff:
    def __init__(self, source_generation: str, proposed_generation: str, records: Sequence[DiffRecord], *, complete: bool = True, truncation_reasons: Sequence[str] = ()) -> None:
        if not source_generation or not proposed_generation:
            raise SemanticDiffError("generation_invalid")
        self.source_generation = source_generation
        self.proposed_generation = proposed_generation
        self.records = tuple(sorted(records, key=lambda item: (item.handle, item.kind)))
        self.complete = bool(complete)
        self.truncation_reasons = tuple(truncation_reasons)

    @property
    def digest(self) -> str:
        return _digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        value = {
            "schema": DIFF_SCHEMA,
            "source_generation": self.source_generation,
            "proposed_generation": self.proposed_generation,
            "records": [item.to_dict() for item in self.records],
            "complete": self.complete,
            "truncation_reasons": list(self.truncation_reasons),
        }
        if include_digest:
            value["diff_digest"] = self.digest
        return value

    def encode(self) -> bytes:
        return _canonical(self.to_dict()) + b"\n"

    def overlay(self) -> WhatIfOverlay:
        return WhatIfOverlay(self.source_generation, self.records)

    def impact(self, adjacency: Mapping[str, Sequence[str]], *, max_nodes: int = 1000) -> dict[str, Any]:
        if max_nodes <= 0:
            raise SemanticDiffError("impact_budget_invalid")
        changed = {item.handle for item in self.records}
        queue = list(sorted(changed))
        included: set[str] = set()
        reasons: dict[str, str] = {}
        while queue and len(included) < max_nodes:
            node = queue.pop(0)
            if node in included:
                continue
            included.add(node)
            reasons[node] = "direct_change" if node in changed else "dependency_closure"
            queue.extend(sorted(adjacency.get(node, ())))
        return {"schema": "simplicio.fast.impact-explanation/v1", "nodes": sorted(included), "reasons": reasons, "complete": not queue}

    def impact_federated(self, federation: Any, *, max_nodes: int = 1000) -> dict[str, Any]:
        """Traverse pinned federation consumers; implicit latest is impossible."""
        if max_nodes <= 0:
            raise SemanticDiffError("impact_budget_invalid")
        queue = sorted(item.handle for item in self.records)
        included: set[str] = set()
        reasons: dict[str, str] = {}
        paths: dict[str, list[str]] = {handle: [handle] for handle in queue}
        while queue and len(included) < max_nodes:
            current = queue.pop(0)
            if current in included:
                continue
            included.add(current)
            reasons[current] = "direct_change" if current in paths and len(paths[current]) == 1 else "federated_consumer"
            for edge in federation.dependencies(current):
                target = edge["target_handle"]
                if target not in paths:
                    paths[target] = paths[current] + [target]
                    queue.append(target)
        return {
            "schema": "simplicio.fast.impact-explanation/v1",
            "federation_generation": federation.generation,
            "nodes": sorted(included),
            "reasons": reasons,
            "paths": paths,
            "complete": not queue and self.complete,
            "truncation_reasons": list(self.truncation_reasons) if not self.complete else [],
        }


def diff_generations(source: Mapping[str, Mapping[str, Any]], proposed: Mapping[str, Mapping[str, Any]], *, source_generation: str, proposed_generation: str) -> SemanticDiff:
    records: list[DiffRecord] = []
    for handle in sorted(set(source) | set(proposed)):
        before = source.get(handle)
        after = proposed.get(handle)
        if before is None:
            kind, reason = "add", "handle_added"
        elif after is None:
            kind, reason = "remove", "handle_removed"
        elif _canonical(before) != _canonical(after):
            kind, reason = "update", "payload_changed"
        else:
            continue
        records.append(DiffRecord(handle, kind, before, after, source_generation, proposed_generation, reason))
    return SemanticDiff(source_generation, proposed_generation, records)


__all__ = ["DIFF_SCHEMA", "DiffRecord", "SemanticDiff", "SemanticDiffError", "WhatIfOverlay", "diff_generations"]
