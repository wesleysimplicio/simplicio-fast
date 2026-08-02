"""Explainable, read-only ranking of capability candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


FACT_SCHEMA = "simplicio.fast.capability-fact/v1"
CATALOG_SCHEMA = "simplicio.fast.capability-catalog-projection/v1"


class CapabilityRankingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    handle: str
    kind: str
    version: str
    capabilities: tuple[str, ...]
    trust: str = "unknown"
    available: bool = True
    estimated_cost: int = 0
    estimated_latency_ms: int = 0
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.handle or not self.kind or not self.version:
            raise CapabilityRankingError("candidate_identity_invalid")
        if any(not item for item in self.capabilities):
            raise CapabilityRankingError("candidate_capabilities_invalid")
        if self.estimated_cost < 0 or self.estimated_latency_ms < 0:
            raise CapabilityRankingError("candidate_cost_invalid")


def rank_capabilities(candidates: Iterable[CapabilityCandidate], required: Sequence[str], *, max_results: int = 32) -> dict[str, Any]:
    if not required or max_results <= 0:
        raise CapabilityRankingError("ranking_request_invalid")
    required_set = set(required)
    facts: list[dict[str, Any]] = []
    for candidate in candidates:
        matched = sorted(required_set.intersection(candidate.capabilities))
        missing = sorted(required_set.difference(candidate.capabilities))
        score = len(matched) * 100 - len(missing) * 1000 - candidate.estimated_cost - candidate.estimated_latency_ms
        if not candidate.available:
            score -= 10000
        facts.append({
            "schema": FACT_SCHEMA,
            "handle": candidate.handle,
            "kind": candidate.kind,
            "version": candidate.version,
            "matched_capabilities": matched,
            "missing_capabilities": missing,
            "trust": candidate.trust,
            "available": candidate.available,
            "estimated_cost": candidate.estimated_cost,
            "estimated_latency_ms": candidate.estimated_latency_ms,
            "score": score,
            "selection_reason": "all_required_capabilities" if not missing else "missing_required_capabilities",
            "provenance": list(candidate.provenance),
        })
    facts.sort(key=lambda item: (-item["score"], item["handle"], item["version"]))
    return {
        "schema": CATALOG_SCHEMA,
        "required_capabilities": sorted(required_set),
        "candidates": facts[:max_results],
        "truncated": len(facts) > max_results,
        "authority": "advisory_only",
        "authorization_owner": "agent-loop-runtime",
    }


__all__ = ["CATALOG_SCHEMA", "CapabilityCandidate", "CapabilityRankingError", "FACT_SCHEMA", "rank_capabilities"]
