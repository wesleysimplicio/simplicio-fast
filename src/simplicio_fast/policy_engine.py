"""Fast-side integration for deterministic speculative-decoding policy.

This module composes the existing policy, pressure, profiler, cache, and
Fast--Local contract surfaces.  It decides whether a Local execution plan is
eligible; it never loads a model, reads or writes a KV cache, samples
hardware, or invokes a kernel.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .contract_surface import (
    CONTRACT_SCHEMA,
    CONTRACT_VERSION,
    INVALIDATION_TRIGGERS,
    digest_for,
    validate_decision_receipt,
    validate_telemetry_snapshot,
)
from .decision_cache import DecisionCache, DecisionCacheKey
from .pressure_inputs import (
    PressureInputs,
    PressureScore,
    Recommendation,
    rank_placements,
    score_pressure,
)
from .speculation_policy import (
    EXECUTION_OWNER,
    SpeculationCapabilities,
    SpeculationConfiguration,
    SpeculationPolicy,
    SpeculationResult,
    SpeculationStrategy,
)
from .speculation_profiler import (
    GuardrailResult,
    ProfilingReceipt,
    regression_guardrail,
)

POLICY_ENGINE_SCHEMA = "simplicio.fast.policy-engine/v1"
DEFAULT_MAX_RECEIPT_BYTES = 8_192
DEFAULT_MAX_RECEIPT_ITEMS = 8


class PolicyEngineError(ValueError):
    """Raised when the policy-engine integration cannot accept its inputs."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class PolicyEngineConfig:
    """Small, explicit limits for one bounded policy evaluation."""

    min_metrics: int = 2
    min_confidence: float = 0.60
    max_throughput_regression: float = 0.05
    max_memory_increase: float = 0.20
    max_receipt_bytes: int = DEFAULT_MAX_RECEIPT_BYTES
    max_receipt_items: int = DEFAULT_MAX_RECEIPT_ITEMS

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_metrics, bool)
            or not isinstance(self.min_metrics, int)
            or self.min_metrics < 1
        ):
            raise PolicyEngineError("MIN_METRICS_INVALID")
        if (
            isinstance(self.max_receipt_bytes, bool)
            or not isinstance(self.max_receipt_bytes, int)
            or self.max_receipt_bytes < 256
        ):
            raise PolicyEngineError("MAX_RECEIPT_BYTES_INVALID")
        if (
            isinstance(self.max_receipt_items, bool)
            or not isinstance(self.max_receipt_items, int)
            or self.max_receipt_items < 1
        ):
            raise PolicyEngineError("MAX_RECEIPT_ITEMS_INVALID")
        for name in (
            "min_confidence",
            "max_throughput_regression",
            "max_memory_increase",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PolicyEngineError(f"{name.upper()}_INVALID")
            if not isfinite(float(value)) or float(value) < 0.0:
                raise PolicyEngineError(f"{name.upper()}_INVALID")
        if self.min_confidence > 1.0:
            raise PolicyEngineError("MIN_CONFIDENCE_INVALID")
        if self.max_throughput_regression >= 1.0:
            raise PolicyEngineError("MAX_THROUGHPUT_REGRESSION_INVALID")


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """All Fast-side observations needed for one policy decision."""

    capabilities: SpeculationCapabilities
    pressure: PressureInputs
    profile: ProfilingReceipt | None = None
    cache_key: DecisionCacheKey | None = None
    telemetry_snapshot: Mapping[str, Any] | None = None
    acceptance_rate: float | None = None
    observed: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PolicyReceipt:
    """Explainable, bounded evidence for one policy-engine decision."""

    requested: SpeculationConfiguration
    selected: SpeculationStrategy
    recommendation: Recommendation
    fallback: bool
    reason_codes: tuple[str, ...]
    pressure: Mapping[str, Any]
    policy: Mapping[str, Any] | None
    guardrail: Mapping[str, Any]
    cache: Mapping[str, Any]
    generation: str | None
    telemetry_digest: str | None
    contract_receipt: Mapping[str, Any] | None
    decision_owner: str = "simplicio-fast"
    execution_owner: str = EXECUTION_OWNER

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_ENGINE_SCHEMA,
            "requested": self.requested.value,
            "selected": self.selected.value,
            "recommendation": self.recommendation.value,
            "fallback": self.fallback,
            "reason_codes": list(self.reason_codes),
            "decision_owner": self.decision_owner,
            "execution_owner": self.execution_owner,
            "generation": self.generation,
            "telemetry_digest": self.telemetry_digest,
            "pressure": dict(self.pressure),
            "policy": None if self.policy is None else dict(self.policy),
            "guardrail": dict(self.guardrail),
            "cache": dict(self.cache),
            "execution_plan": {
                "strategy": self.selected.value,
                "owner": self.execution_owner,
            },
            "contract_receipt": (
                None
                if self.contract_receipt is None
                else dict(self.contract_receipt)
            ),
        }


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Typed result returned by :class:`PolicyEngine`."""

    requested: SpeculationConfiguration
    selected: SpeculationStrategy
    recommendation: Recommendation
    reason_codes: tuple[str, ...]
    cache_hit: bool
    receipt: PolicyReceipt
    policy_result: SpeculationResult | None = None

    @property
    def strategy(self) -> SpeculationStrategy:
        """Alias for callers that treat the result as an execution plan."""

        return self.selected

    @property
    def fallback(self) -> bool:
        return self.receipt.fallback

    def to_dict(self) -> dict[str, Any]:
        return self.receipt.to_dict()


def _bounded_reasons(
    reasons: tuple[str, ...] | list[str], max_items: int
) -> tuple[str, ...]:
    unique = list(dict.fromkeys(reasons))
    if len(unique) <= max_items:
        return tuple(unique)
    if max_items == 1:
        return ("RECEIPT_REASON_CODES_TRUNCATED",)
    return (*unique[: max_items - 1], "RECEIPT_REASON_CODES_TRUNCATED")


def _compact_cache_receipt(
    receipt: Mapping[str, Any], max_items: int
) -> dict[str, Any]:
    """Keep cache evidence bounded even when a generation invalidates many keys."""

    compact: dict[str, Any] = {}
    for name, value in receipt.items():
        if name in {"invalidated_key_digests", "evicted_key_digests"}:
            if not isinstance(value, list):
                continue
            compact[name] = value[:max_items]
            if len(value) > max_items:
                compact[f"{name}_omitted"] = len(value) - max_items
        else:
            compact[name] = value
    return compact


def _guardrail_unavailable() -> GuardrailResult:
    return GuardrailResult(False, "profile_unavailable", None, None)


class PolicyEngine:
    """Compose existing Fast policy authorities without executing Local work."""

    def __init__(
        self,
        policy: SpeculationPolicy | str | SpeculationConfiguration = "auto",
        *,
        cache: DecisionCache | None = None,
        config: PolicyEngineConfig | None = None,
    ) -> None:
        if isinstance(policy, SpeculationPolicy):
            self.policy = policy
        else:
            self.policy = SpeculationPolicy(policy)
        self.cache = cache if cache is not None else DecisionCache()
        self.config = config or PolicyEngineConfig()

    def decide(
        self,
        capabilities_or_inputs: SpeculationCapabilities | PolicyInputs,
        pressure_inputs: PressureInputs | None = None,
        *,
        profile: ProfilingReceipt | None = None,
        cache_key: DecisionCacheKey | None = None,
        telemetry_snapshot: Mapping[str, Any] | None = None,
        acceptance_rate: float | None = None,
        observed: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        """Return a deterministic plan and receipt, failing closed to baseline.

        A :class:`PolicyInputs` bundle is accepted for callers that already
        have a request object.  The expanded form keeps the common call site
        small.  Cache reuse is attempted only after current pressure and the
        profiler guardrail permit speculation.
        """

        if isinstance(capabilities_or_inputs, PolicyInputs):
            if pressure_inputs is not None or any(
                value is not None
                for value in (
                    profile,
                    cache_key,
                    telemetry_snapshot,
                    acceptance_rate,
                    observed,
                )
            ):
                raise PolicyEngineError("POLICY_INPUTS_ARGUMENTS_DUPLICATED")
            request = capabilities_or_inputs
        else:
            if pressure_inputs is None:
                raise PolicyEngineError("PRESSURE_INPUTS_MISSING")
            request = PolicyInputs(
                capabilities_or_inputs,
                pressure_inputs,
                profile=profile,
                cache_key=cache_key,
                telemetry_snapshot=telemetry_snapshot,
                acceptance_rate=acceptance_rate,
                observed=observed,
            )
        self._validate_inputs(request)
        normalized_snapshot = self._validate_snapshot(request.telemetry_snapshot)
        telemetry_digest = self._telemetry_digest(normalized_snapshot)
        generation, generation_mismatch = self._generation_state(
            request, normalized_snapshot
        )
        profile_acceptance = (
            None
            if request.profile is None
            else request.profile.metrics.acceptance_rate
        )
        if (
            request.acceptance_rate is not None
            and profile_acceptance is not None
            and request.acceptance_rate != profile_acceptance
        ):
            acceptance = profile_acceptance
            acceptance_contradiction = True
        else:
            acceptance = (
                profile_acceptance
                if profile_acceptance is not None
                else request.acceptance_rate
            )
            acceptance_contradiction = False

        pressure = score_pressure(
            request.pressure,
            acceptance_rate=acceptance,
            min_metrics=self.config.min_metrics,
            min_confidence=self.config.min_confidence,
        )
        guardrail = (
            _guardrail_unavailable()
            if request.profile is None
            else regression_guardrail(
                request.profile.metrics,
                max_throughput_regression=self.config.max_throughput_regression,
                max_memory_increase=self.config.max_memory_increase,
            )
        )
        reasons = list(pressure.reason_codes)
        if request.profile is None:
            reasons.append("PROFILE_UNAVAILABLE")
        if acceptance_contradiction:
            reasons.append("ACCEPTANCE_CONTRADICTORY")
        if generation_mismatch:
            reasons.append("GENERATION_MISMATCH")
        if not guardrail.enabled:
            reasons.append(f"GUARDRAIL_{guardrail.reason.upper()}")

        cache_receipt: dict[str, Any] = {
            "enabled": request.cache_key is not None,
            "hit": False,
        }
        if request.cache_key is None:
            cache_receipt["reason"] = "cache_key_unavailable"
        elif generation_mismatch:
            cache_receipt["reason"] = "generation_mismatch"
        else:
            activation = self.cache.activate_generation(request.cache_key.generation)
            cache_receipt["activation"] = _compact_cache_receipt(
                activation, self.config.max_receipt_items
            )

        allowed_to_speculate = (
            pressure.recommendation is Recommendation.SPECULATIVE
            and guardrail.enabled
            and request.profile is not None
            and not acceptance_contradiction
            and not generation_mismatch
        )

        if not allowed_to_speculate:
            if request.cache_key is not None and not generation_mismatch:
                self._invalidate_disallowed(
                    request.cache_key,
                    guardrail,
                    pressure,
                    cache_receipt,
                )
            return self._finish(
                request,
                normalized_snapshot,
                telemetry_digest,
                generation,
                pressure,
                guardrail,
                SpeculationStrategy.BASELINE,
                reasons,
                cache_receipt,
                policy_result=None,
                cache_hit=False,
            )

        if request.cache_key is not None:
            lookup = self.cache.lookup(
                request.cache_key,
                observed=self._observed(request.observed, pressure, guardrail),
            )
            cache_receipt["lookup"] = _compact_cache_receipt(
                lookup, self.config.max_receipt_items
            )
            if lookup["outcome"] == "hit":
                cached_strategy = self._cached_strategy(
                    lookup.get("decision"),
                    request.capabilities,
                )
                if cached_strategy is not None:
                    cache_receipt["hit"] = True
                    reasons.append("CACHE_HIT")
                    return self._finish(
                        request,
                        normalized_snapshot,
                        telemetry_digest,
                        generation,
                        pressure,
                        guardrail,
                        cached_strategy,
                        reasons,
                        cache_receipt,
                        policy_result=None,
                        cache_hit=True,
                    )
                self.cache.invalidate(
                    request.cache_key, reason="cached_decision_invalid"
                )
                reasons.append("CACHE_DECISION_INVALID")
            elif lookup["outcome"] == "quarantined":
                reasons.append("CACHE_QUARANTINED")
            else:
                reasons.append("CACHE_MISS")

        policy_result = self.policy.decide(request.capabilities)
        selected = policy_result.selected
        reasons.append(f"POLICY_{policy_result.reason.upper()}")
        if selected is SpeculationStrategy.BASELINE:
            if request.cache_key is not None:
                self.cache.invalidate(request.cache_key, reason="policy_baseline")
            return self._finish(
                request,
                normalized_snapshot,
                telemetry_digest,
                generation,
                pressure,
                guardrail,
                selected,
                reasons,
                cache_receipt,
                policy_result=policy_result,
                cache_hit=False,
            )

        if request.cache_key is not None:
            stored = self.cache.put(
                request.cache_key,
                {
                    "requested": self.policy.configuration.value,
                    "strategy": selected.value,
                },
                expected={
                    "pressure_recommendation": Recommendation.SPECULATIVE.value,
                    "guardrail": "enabled",
                },
            )
            cache_receipt["store"] = _compact_cache_receipt(
                stored, self.config.max_receipt_items
            )
        return self._finish(
            request,
            normalized_snapshot,
            telemetry_digest,
            generation,
            pressure,
            guardrail,
            selected,
            reasons,
            cache_receipt,
            policy_result=policy_result,
            cache_hit=False,
        )

    def _validate_inputs(self, request: PolicyInputs) -> None:
        if not isinstance(request.capabilities, SpeculationCapabilities):
            raise PolicyEngineError("CAPABILITIES_INVALID")
        if not isinstance(request.pressure, PressureInputs):
            raise PolicyEngineError("PRESSURE_INPUTS_INVALID")
        if request.profile is not None and not isinstance(
            request.profile, ProfilingReceipt
        ):
            raise PolicyEngineError("PROFILE_INVALID")
        if request.cache_key is not None and not isinstance(
            request.cache_key, DecisionCacheKey
        ):
            raise PolicyEngineError("CACHE_KEY_INVALID")
        if request.telemetry_snapshot is not None and not isinstance(
            request.telemetry_snapshot, Mapping
        ):
            raise PolicyEngineError("TELEMETRY_SNAPSHOT_INVALID")
        if request.observed is not None and not isinstance(request.observed, Mapping):
            raise PolicyEngineError("OBSERVED_TELEMETRY_INVALID")

    def _validate_snapshot(
        self, snapshot: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return validate_telemetry_snapshot(snapshot)

    @staticmethod
    def _telemetry_digest(snapshot: Mapping[str, Any] | None) -> str | None:
        if snapshot is None:
            return None
        return str(snapshot["payload"]["telemetry_digest"])

    @staticmethod
    def _generation_state(
        request: PolicyInputs, snapshot: Mapping[str, Any] | None
    ) -> tuple[str | None, bool]:
        values: list[str] = []
        if request.cache_key is not None:
            values.append(request.cache_key.generation)
        if request.profile is not None:
            values.append(request.profile.key.generation)
        if snapshot is not None:
            values.append(str(snapshot["generation"]["generation_id"]))
        if not values:
            return None, False
        return values[0], len(set(values)) > 1

    @staticmethod
    def _observed(
        observed: Mapping[str, Any] | None,
        pressure: PressureScore,
        guardrail: GuardrailResult,
    ) -> dict[str, Any]:
        result = dict(observed or {})
        result.setdefault("pressure_recommendation", pressure.recommendation.value)
        result.setdefault("guardrail", "enabled" if guardrail.enabled else "disabled")
        return result

    def _cached_strategy(
        self,
        value: object,
        capabilities: SpeculationCapabilities,
    ) -> SpeculationStrategy | None:
        if not isinstance(value, Mapping):
            return None
        if value.get("requested") != self.policy.configuration.value:
            return None
        strategy = value.get("strategy")
        try:
            parsed = SpeculationStrategy(strategy)
        except (TypeError, ValueError):
            return None
        if parsed is SpeculationStrategy.BASELINE:
            return None
        return parsed if capabilities.for_strategy(parsed).is_usable() else None

    def _invalidate_disallowed(
        self,
        key: DecisionCacheKey,
        guardrail: GuardrailResult,
        pressure: PressureScore,
        cache_receipt: dict[str, Any],
    ) -> None:
        if guardrail.reason in {
            "throughput_regression",
            "memory_regression",
            "fallback_observed",
        }:
            invalidated = self.cache.disable_for_regression(
                key, reason=guardrail.reason
            )
        elif pressure.recommendation is Recommendation.BASELINE:
            invalidated = self.cache.invalidate(key, reason="pressure_guardrail")
        else:
            return
        cache_receipt["invalidation"] = _compact_cache_receipt(
            invalidated, self.config.max_receipt_items
        )

    def _finish(
        self,
        request: PolicyInputs,
        snapshot: Mapping[str, Any] | None,
        telemetry_digest: str | None,
        generation: str | None,
        pressure: PressureScore,
        guardrail: GuardrailResult,
        selected: SpeculationStrategy,
        reasons: list[str],
        cache_receipt: Mapping[str, Any],
        *,
        policy_result: SpeculationResult | None,
        cache_hit: bool,
    ) -> PolicyDecision:
        recommendation = (
            Recommendation.SPECULATIVE
            if selected is not SpeculationStrategy.BASELINE
            and pressure.recommendation is Recommendation.SPECULATIVE
            and guardrail.enabled
            else Recommendation.BASELINE
        )
        fallback = recommendation is Recommendation.BASELINE
        selected_reasons = list(reasons)
        if fallback:
            selected_reasons.append("BASELINE_FALLBACK")
        bounded_reasons = _bounded_reasons(
            selected_reasons, self.config.max_receipt_items
        )
        policy_payload = None
        if policy_result is not None:
            policy_payload = policy_result.to_dict()
        contract_receipt = self._contract_receipt(
            request,
            snapshot,
            pressure,
            guardrail,
            selected,
            bounded_reasons,
            cache_hit,
        )
        receipt = PolicyReceipt(
            requested=self.policy.configuration,
            selected=selected,
            recommendation=recommendation,
            fallback=fallback,
            reason_codes=bounded_reasons,
            pressure=pressure.as_dict(),
            policy=policy_payload,
            guardrail=guardrail.to_dict(),
            cache=dict(cache_receipt),
            generation=generation,
            telemetry_digest=telemetry_digest,
            contract_receipt=contract_receipt,
        )
        result = PolicyDecision(
            requested=self.policy.configuration,
            selected=selected,
            recommendation=recommendation,
            reason_codes=bounded_reasons,
            cache_hit=cache_hit,
            receipt=receipt,
            policy_result=policy_result,
        )
        encoded = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self.config.max_receipt_bytes:
            raise PolicyEngineError("RECEIPT_SIZE_EXCEEDED")
        return result

    def _contract_receipt(
        self,
        request: PolicyInputs,
        snapshot: Mapping[str, Any] | None,
        pressure: PressureScore,
        guardrail: GuardrailResult,
        selected: SpeculationStrategy,
        reasons: tuple[str, ...],
        cache_hit: bool,
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        placements = rank_placements(request.pressure)
        if placements:
            placement_target = placements[0].candidate.name
            placement_reason = placements[0].reason_codes[0]
        else:
            placement_target = "baseline"
            placement_reason = "placement_unavailable"
        strategy = "disabled" if selected is SpeculationStrategy.BASELINE else (
            "tree" if selected is SpeculationStrategy.MTP else "draft_verify"
        )
        generation = snapshot["generation"]
        source_digest = snapshot["payload"]["telemetry_digest"]
        unsigned: dict[str, Any] = {
            "schema": CONTRACT_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "message_type": "decision_receipt",
            "generation": generation,
            "payload": {
                "decision_id": "decision-" + digest_for(
                    {
                        "generation": generation["generation_id"],
                        "selected": selected.value,
                        "pressure": pressure.as_dict(),
                        "cache_hit": cache_hit,
                    }
                )[7:23],
                "source_telemetry_digest": source_digest,
                "speculation_policy": {
                    "enabled": strategy != "disabled",
                    "strategy": strategy,
                },
                "placement_recommendation": {
                    "target": placement_target,
                    "reason_code": placement_reason,
                },
                "context_batch_policy": {
                    "batch_size": 1,
                    "ranking": "cache_locality" if cache_hit else "balanced",
                },
                "confidence": pressure.confidence if guardrail.enabled else 0.0,
                "reason_codes": sorted(reasons) or ["policy_decision"],
                "invalidation_triggers": list(INVALIDATION_TRIGGERS),
                "unavailable": {},
            },
        }
        unsigned["payload"]["decision_digest"] = digest_for(unsigned)
        return validate_decision_receipt(unsigned)


__all__ = [
    "DEFAULT_MAX_RECEIPT_BYTES",
    "DEFAULT_MAX_RECEIPT_ITEMS",
    "POLICY_ENGINE_SCHEMA",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyEngineConfig",
    "PolicyEngineError",
    "PolicyInputs",
    "PolicyReceipt",
]
