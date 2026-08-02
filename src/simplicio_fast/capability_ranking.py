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
    estimated_cost: int | None = None
    estimated_latency_ms: int | None = None
    provenance: tuple[str, ...] = ()
    policy_eligible: bool | None = None
    scope: str = "*"
    metric_class: str = "unknown"

    def __post_init__(self) -> None:
        if not self.handle or not self.kind or not self.version:
            raise CapabilityRankingError("candidate_identity_invalid")
        if any(not item for item in self.capabilities):
            raise CapabilityRankingError("candidate_capabilities_invalid")
        if (
            (self.estimated_cost is not None and self.estimated_cost < 0)
            or (self.estimated_latency_ms is not None and self.estimated_latency_ms < 0)
        ):
            raise CapabilityRankingError("candidate_cost_invalid")
        if self.metric_class not in {"unknown", "estimated", "measured", "simulated"}:
            raise CapabilityRankingError("candidate_metric_class_invalid")


def rank_capabilities(
    candidates: Iterable[CapabilityCandidate],
    required: Sequence[str],
    *,
    max_results: int = 32,
    required_scope: str | None = None,
) -> dict[str, Any]:
    if not required or max_results <= 0:
        raise CapabilityRankingError("ranking_request_invalid")
    required_set = set(required)
    facts: list[dict[str, Any]] = []
    for candidate in candidates:
        matched = sorted(required_set.intersection(candidate.capabilities))
        missing = sorted(required_set.difference(candidate.capabilities))
        scope_match = required_scope is None or candidate.scope == required_scope
        policy = (
            "eligible"
            if candidate.policy_eligible is True
            else "rejected"
            if candidate.policy_eligible is False
            else "unknown"
        )
        hard_filter = {
            "missing_capabilities": not missing,
            "available": candidate.available,
            "policy_eligibility": policy == "eligible",
            "scope": scope_match,
        }
        eligible = all(hard_filter.values())
        score_components = {
            "matched_capabilities": len(matched) * 100,
            "missing_capabilities": -len(missing) * 1000,
            "cost": -(candidate.estimated_cost or 0),
            "latency": -(candidate.estimated_latency_ms or 0),
            "availability": 0 if candidate.available else -10000,
            "policy": 0 if policy == "eligible" else -10000 if policy == "rejected" else -5000,
            "scope": 0 if scope_match else -10000,
        }
        score = sum(score_components.values())
        if missing:
            reason = "missing_required_capabilities"
        elif not candidate.available:
            reason = "unavailable"
        elif policy != "eligible":
            reason = f"policy_{policy}"
        elif not scope_match:
            reason = "scope_mismatch"
        else:
            reason = "eligible"
        facts.append({
            "schema": FACT_SCHEMA,
            "handle": candidate.handle,
            "kind": candidate.kind,
            "version": candidate.version,
            "matched_capabilities": matched,
            "missing_capabilities": missing,
            "trust": candidate.trust,
            "available": candidate.available,
            "scope": candidate.scope,
            "policy_eligibility": policy,
            "eligible": eligible,
            "hard_filter": hard_filter,
            "metric_class": candidate.metric_class,
            "estimated_cost": candidate.estimated_cost,
            "estimated_latency_ms": candidate.estimated_latency_ms,
            "score": score,
            "score_components": score_components,
            "selection_reason": reason,
            "provenance": list(candidate.provenance),
        })
    facts.sort(key=lambda item: (-item["eligible"], -item["score"], item["handle"], item["version"]))
    return {
        "schema": CATALOG_SCHEMA,
        "required_capabilities": sorted(required_set),
        "required_scope": required_scope,
        "candidates": facts[:max_results],
        "truncated": len(facts) > max_results,
        "authority": "advisory_only",
        "authorization_owner": "agent-loop-runtime",
    }


__all__ = ["CATALOG_SCHEMA", "CapabilityCandidate", "CapabilityRankingError", "FACT_SCHEMA", "rank_capabilities"]
