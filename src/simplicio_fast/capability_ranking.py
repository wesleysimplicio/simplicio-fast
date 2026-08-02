"""Explainable, read-only ranking of capability candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


FACT_SCHEMA = "simplicio.fast.capability-fact/v1"
CATALOG_SCHEMA = "simplicio.fast.capability-catalog-projection/v1"
MAX_CANDIDATES = 100_000
MAX_RESULTS = 10_000
_TRUST_RANK = {
    "untrusted": 0,
    "derived_fact": 1,
    "advisory": 2,
    "verified": 3,
    "authoritative": 4,
}


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
    freshness_seconds: int | None = None
    health: str = "unknown"

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.handle, self.kind, self.version, self.trust)
        ):
            raise CapabilityRankingError("candidate_identity_invalid")
        if not isinstance(self.capabilities, (tuple, list)):
            raise CapabilityRankingError("candidate_capabilities_invalid")
        if any(not isinstance(item, str) or not item.strip() for item in self.capabilities):
            raise CapabilityRankingError("candidate_capabilities_invalid")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        if not isinstance(self.provenance, (tuple, list)):
            raise CapabilityRankingError("candidate_provenance_invalid")
        if any(not isinstance(item, str) or not item.strip() for item in self.provenance):
            raise CapabilityRankingError("candidate_provenance_invalid")
        object.__setattr__(self, "provenance", tuple(self.provenance))
        if not isinstance(self.scope, str) or not self.scope.strip():
            raise CapabilityRankingError("candidate_scope_invalid")
        if not isinstance(self.available, bool):
            raise CapabilityRankingError("candidate_availability_invalid")
        if self.policy_eligible is not None and not isinstance(self.policy_eligible, bool):
            raise CapabilityRankingError("candidate_policy_invalid")
        if (
            (
                self.estimated_cost is not None
                and (
                    isinstance(self.estimated_cost, bool)
                    or not isinstance(self.estimated_cost, int)
                    or self.estimated_cost < 0
                )
            )
            or (
                self.estimated_latency_ms is not None
                and (
                    isinstance(self.estimated_latency_ms, bool)
                    or not isinstance(self.estimated_latency_ms, int)
                    or self.estimated_latency_ms < 0
                )
            )
        ):
            raise CapabilityRankingError("candidate_cost_invalid")
        if not isinstance(self.metric_class, str) or self.metric_class not in {"unknown", "estimated", "measured", "simulated"}:
            raise CapabilityRankingError("candidate_metric_class_invalid")
        if (
            self.freshness_seconds is not None
            and (
                isinstance(self.freshness_seconds, bool)
                or not isinstance(self.freshness_seconds, int)
                or self.freshness_seconds < 0
            )
        ):
            raise CapabilityRankingError("candidate_freshness_invalid")
        if not isinstance(self.health, str) or self.health not in {
            "unknown",
            "healthy",
            "degraded",
            "unhealthy",
        }:
            raise CapabilityRankingError("candidate_health_invalid")


def rank_capabilities(
    candidates: Iterable[CapabilityCandidate],
    required: Sequence[str],
    *,
    max_results: int = 32,
    required_scope: str | None = None,
    required_trust: str | None = None,
    max_freshness_seconds: int | None = None,
) -> dict[str, Any]:
    invalid_required_trust = (
        required_trust is not None and required_trust not in _TRUST_RANK
    )
    invalid_max_freshness = (
        max_freshness_seconds is not None
        and (
            isinstance(max_freshness_seconds, bool)
            or not isinstance(max_freshness_seconds, int)
            or max_freshness_seconds < 0
        )
    )
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes))
        or not required
        or any(not isinstance(item, str) or not item.strip() for item in required)
        or isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 0 < max_results <= MAX_RESULTS
        or (
            required_scope is not None
            and (not isinstance(required_scope, str) or not required_scope.strip())
        )
        or (
            invalid_required_trust
        )
        or invalid_max_freshness
    ):
        raise CapabilityRankingError(
            "ranking_trust_invalid"
            if invalid_required_trust
            else "ranking_freshness_invalid"
            if invalid_max_freshness
            else "ranking_request_invalid"
        )
    required_set = set(required)
    facts: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, CapabilityCandidate):
            raise CapabilityRankingError("candidate_type_invalid")
        if len(facts) >= MAX_CANDIDATES:
            raise CapabilityRankingError("candidate_count_limit")
        matched = sorted(required_set.intersection(candidate.capabilities))
        missing = sorted(required_set.difference(candidate.capabilities))
        scope_match = required_scope is None or candidate.scope in {"*", required_scope}
        trust_match = (
            required_trust is None
            or _TRUST_RANK.get(candidate.trust, -1) >= _TRUST_RANK[required_trust]
        )
        freshness_match = (
            max_freshness_seconds is None
            or (
                candidate.freshness_seconds is not None
                and candidate.freshness_seconds <= max_freshness_seconds
            )
        )
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
            "trust": trust_match,
            "freshness": freshness_match,
        }
        eligible = all(hard_filter.values())
        score_components = {
            "matched_capabilities": len(matched) * 100,
            "missing_capabilities": -len(missing) * 1000,
            "cost": -candidate.estimated_cost if candidate.estimated_cost is not None else None,
            "latency": -candidate.estimated_latency_ms if candidate.estimated_latency_ms is not None else None,
            "availability": 0 if candidate.available else -10000,
            "policy": 0 if policy == "eligible" else -10000 if policy == "rejected" else -5000,
            "scope": 0 if scope_match else -10000,
            "trust": 0 if trust_match else -10000,
            "freshness": 0 if freshness_match else -10000,
        }
        score = sum(value for value in score_components.values() if isinstance(value, int))
        if missing:
            reason = "missing_required_capabilities"
        elif not candidate.available:
            reason = "unavailable"
        elif policy != "eligible":
            reason = f"policy_{policy}"
        elif not scope_match:
            reason = "scope_mismatch"
        elif not trust_match:
            reason = "trust_below_floor"
        elif not freshness_match:
            reason = (
                "freshness_unknown"
                if candidate.freshness_seconds is None
                else "freshness_stale"
            )
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
            "health": candidate.health,
            "freshness_seconds": candidate.freshness_seconds,
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
    measured = [
        item for item in facts
        if item["eligible"]
        and item["estimated_cost"] is not None
        and item["estimated_latency_ms"] is not None
    ]
    frontier = []
    for item in measured:
        dominated = any(
            other is not item
            and other["estimated_cost"] <= item["estimated_cost"]
            and other["estimated_latency_ms"] <= item["estimated_latency_ms"]
            and (
                other["estimated_cost"] < item["estimated_cost"]
                or other["estimated_latency_ms"] < item["estimated_latency_ms"]
            )
            for other in measured
        )
        if not dominated:
            frontier.append({
                "handle": item["handle"],
                "version": item["version"],
                "estimated_cost": item["estimated_cost"],
                "estimated_latency_ms": item["estimated_latency_ms"],
                "metric_class": item["metric_class"],
            })
    frontier.sort(key=lambda item: (item["handle"], item["version"]))
    return {
        "schema": CATALOG_SCHEMA,
        "required_capabilities": sorted(required_set),
        "required_scope": required_scope,
        "required_trust": required_trust,
        "max_freshness_seconds": max_freshness_seconds,
        "candidates": facts[:max_results],
        "pareto_frontier": frontier,
        "truncated": len(facts) > max_results,
        "authority": "advisory_only",
        "authorization_owner": "agent-loop-runtime",
    }


__all__ = [
    "CATALOG_SCHEMA", "CapabilityCandidate", "CapabilityRankingError", "FACT_SCHEMA",
    "MAX_CANDIDATES", "MAX_RESULTS", "rank_capabilities",
]
