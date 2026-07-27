"""Deterministic, budget-aware query planning over a validated Fast snapshot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .snapshot import SYMBOL_RECORD, Snapshot


PLAN_SCHEMA = "simplicio.fast.query-plan/v1"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    schema: str
    generation: str
    operation: str
    term: str
    selected_index: str
    candidate_records: int
    estimated_bytes: int
    max_results: int
    max_bytes: int
    max_tokens: int
    prefetch: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["prefetch"] = list(self.prefetch)
        return value


def _intersection(*values: list[int]) -> list[int]:
    if not values:
        return []
    result = set(values[0])
    for value in values[1:]:
        result.intersection_update(value)
    return sorted(result)


def plan_query(
    snapshot: Snapshot,
    term: str,
    *,
    operation: str = "query",
    prefix: bool = False,
    path: str | None = None,
    kind: str | None = None,
    max_results: int = 50,
    max_bytes: int = 32_000,
    max_tokens: int = 8_000,
) -> QueryPlan:
    if operation not in {"query", "search", "context", "impact"}:
        raise ValueError("unsupported query-plan operation")
    if min(max_results, max_bytes, max_tokens) < 1:
        raise ValueError("query-plan budgets must be positive")
    if snapshot.format_version == 1:
        selected = "legacy-linear-scan"
        candidate_records = snapshot.symbol_count if operation != "impact" else snapshot.relation_count
        reason = "legacy_snapshot_has_no_direct_indexes"
    elif operation == "impact":
        selected = "relation-scan"
        candidate_records = snapshot.relation_count
        reason = "impact_requires_typed_relation_filter"
    else:
        indexes = snapshot._indexes()
        exact_values = indexes["exact"].get(term.casefold(), [])
        filters: list[list[int]] = []
        if exact_values and not prefix:
            selected = "exact"
            filters.append(exact_values)
            reason = "exact_index_hit"
        elif prefix:
            selected = "name-prefix"
            filters.append([index for name, values in indexes["names"].items() if name.startswith(term.casefold()) for index in values])
            reason = "prefix_index_scan"
        else:
            selected = "name-substring"
            filters.append([index for name, values in indexes["names"].items() if term.casefold() in name for index in values])
            filters.append(
                [index for name, values in indexes["exact"].items() if term.casefold() in name for index in values]
            )
            reason = "bounded_name_indexes"
        if path is not None:
            selected += "+path"
            filters.append(indexes["paths"].get(path, []))
        if kind is not None:
            selected += "+kind"
            filters.append(indexes["kinds"].get(kind, []))
        candidate_records = len(_intersection(*filters)) if filters else 0
    estimated_bytes = min(max_bytes, candidate_records * SYMBOL_RECORD.size)
    return QueryPlan(
        schema=PLAN_SCHEMA,
        generation=snapshot.generation,
        operation=operation,
        term=term,
        selected_index=selected,
        candidate_records=candidate_records,
        estimated_bytes=estimated_bytes,
        max_results=max_results,
        max_bytes=max_bytes,
        max_tokens=max_tokens,
        prefetch=(),
        reason=reason,
    )
