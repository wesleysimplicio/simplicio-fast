from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from simplicio_fast.contract_surface import validate_decision_receipt
from simplicio_fast.decision_cache import DecisionCache, DecisionCacheKey
from simplicio_fast.policy_engine import (
    PolicyEngine,
    PolicyEngineConfig,
    PolicyEngineError,
)
from simplicio_fast.pressure_inputs import (
    BandwidthPressure,
    CachePressure,
    ConcurrencyPressure,
    Headroom,
    KVPressure,
    PressureInputs,
    PressureMetric,
    ThroughputCost,
    TransferPressure,
)
from simplicio_fast.speculation_policy import (
    SpeculationCapabilities,
    SpeculationPolicy,
    SpeculationStrategy,
    StrategyCapability,
)
from simplicio_fast.speculation_profiler import (
    TuningKey,
    deterministic_synthetic_profile,
)

ROOT = Path(__file__).parents[1]
EXAMPLES = json.loads(
    (ROOT / "contracts/fast-local/v1/examples.json").read_text(encoding="utf-8")
)


def metric(value, capability: str, confidence: float = 1.0):
    return PressureMetric.available(
        value,
        capability=capability,
        confidence=confidence,
    )


def pressure_inputs(*, pressure: float = 0.20, confidence: float = 1.0):
    return PressureInputs(
        bandwidth=metric(BandwidthPressure(pressure), "memory.bandwidth", confidence),
        cache=metric(CachePressure(pressure), "cache.llc", confidence),
        transfer=metric(
            TransferPressure(pressure, bytes=1024, milliseconds=1.0),
            "transfer.cost",
            confidence,
        ),
        headroom=metric(
            Headroom(ram=1.0 - pressure, vram=1.0 - pressure),
            "memory.headroom",
            confidence,
        ),
        kv=metric(
            KVPressure(pressure, context_tokens=100, capacity_tokens=1000),
            "kv.pressure",
            confidence,
        ),
        concurrency=metric(
            ConcurrencyPressure(pressure, active_sessions=1, capacity_sessions=8),
            "concurrency.pressure",
            confidence,
        ),
        throughput=metric(
            ThroughputCost(
                pressure,
                target_tokens_per_second=100,
                draft_tokens_per_second=120,
            ),
            "telemetry.throughput",
            confidence,
        ),
    )


def profile(generation: str, *, throughput_gain: float = 0.12):
    return deterministic_synthetic_profile(
        TuningKey(
            generation=generation,
            model="model-a",
            backend="cuda",
            hardware="gpu-a",
            quantization="q4",
        ),
        seed=494,
        throughput_gain=throughput_gain,
    )


def cache_key(generation: str) -> DecisionCacheKey:
    return DecisionCacheKey(
        model_digest="sha256:model",
        artifact_digest="sha256:artifact",
        quant_digest="sha256:q4",
        tokenizer_template_identity="chat-template-v1",
        backend_version="cuda-v1",
        hardware_topology_fingerprint="gpu-a",
        device_placement_class="same-device",
        context_kv_pressure_bucket="low",
        workload_class="interactive",
        concurrency_bucket="c1",
        fast_policy_version="policy-engine-v1",
        generation=generation,
    )


class PolicyEngine494Test(unittest.TestCase):
    def capabilities(self):
        return SpeculationCapabilities(draft=StrategyCapability(True, 1.4))

    def test_good_decision_uses_all_pressure_dimensions_and_reuses_cache(self):
        engine = PolicyEngine(cache=DecisionCache(max_entries=4))
        inputs = pressure_inputs()
        first = engine.decide(
            self.capabilities(),
            inputs,
            profile=profile("generation-1"),
            cache_key=cache_key("generation-1"),
        )

        self.assertEqual(SpeculationStrategy.DRAFT, first.selected)
        self.assertFalse(first.cache_hit)
        self.assertEqual(
            (
                "bandwidth",
                "cache",
                "transfer",
                "headroom",
                "kv",
                "concurrency",
                "throughput",
            ),
            tuple(first.receipt.pressure["used_metrics"]),
        )

        second = engine.decide(
            self.capabilities(),
            inputs,
            profile=profile("generation-1"),
            cache_key=cache_key("generation-1"),
        )
        self.assertEqual(SpeculationStrategy.DRAFT, second.selected)
        self.assertTrue(second.cache_hit)
        self.assertTrue(second.receipt.cache["hit"])
        self.assertIsNone(second.policy_result)
        self.assertEqual(first.to_dict(), first.receipt.to_dict())

    def test_missing_profile_and_high_pressure_fall_back_to_baseline(self):
        engine = PolicyEngine()
        missing_profile = engine.decide(
            self.capabilities(),
            pressure_inputs(),
            acceptance_rate=0.95,
        )
        self.assertEqual(SpeculationStrategy.BASELINE, missing_profile.selected)
        self.assertTrue(missing_profile.fallback)
        self.assertIn("PROFILE_UNAVAILABLE", missing_profile.reason_codes)

        high_pressure = engine.decide(
            self.capabilities(),
            pressure_inputs(pressure=0.95),
            profile=profile("generation-high"),
        )
        self.assertEqual(SpeculationStrategy.BASELINE, high_pressure.selected)
        self.assertIn("BANDWIDTH_PRESSURE_HIGH", high_pressure.reason_codes)
        self.assertIn("TRANSFER_PRESSURE_HIGH", high_pressure.reason_codes)

    def test_cache_reuse_respects_current_policy_and_injected_cache(self):
        cache = DecisionCache(max_entries=4)
        auto = PolicyEngine(cache=cache)
        key = cache_key("generation-policy")
        auto.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-policy"),
            cache_key=key,
        )

        disabled = PolicyEngine(SpeculationPolicy("off"), cache=cache).decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-policy"),
            cache_key=key,
        )
        self.assertIs(auto.cache, cache)
        self.assertEqual(SpeculationStrategy.BASELINE, disabled.selected)
        self.assertTrue(disabled.fallback)

    def test_low_confidence_and_regression_guardrail_are_fail_closed(self):
        engine = PolicyEngine(cache=DecisionCache(max_entries=4))
        low_confidence = engine.decide(
            self.capabilities(),
            pressure_inputs(confidence=0.40),
            profile=profile("generation-low-confidence"),
            cache_key=cache_key("generation-low-confidence"),
        )
        self.assertEqual(SpeculationStrategy.BASELINE, low_confidence.selected)
        self.assertIn("TELEMETRY_LOW_CONFIDENCE", low_confidence.reason_codes)

        key = cache_key("generation-regression")
        engine.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-regression"),
            cache_key=key,
        )
        regression = engine.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-regression", throughput_gain=-0.20),
            cache_key=key,
        )
        self.assertEqual(SpeculationStrategy.BASELINE, regression.selected)
        self.assertIn("GUARDRAIL_THROUGHPUT_REGRESSION", regression.reason_codes)
        self.assertEqual(
            "disabled",
            engine.cache.lookup(key)["outcome"],
        )

    def test_generation_change_invalidates_previous_cache_entries(self):
        engine = PolicyEngine(cache=DecisionCache(max_entries=4))
        engine.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-old"),
            cache_key=cache_key("generation-old"),
        )
        new_key = cache_key("generation-new")
        result = engine.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-new"),
            cache_key=new_key,
        )

        self.assertEqual(SpeculationStrategy.DRAFT, result.selected)
        self.assertFalse(result.cache_hit)
        activation = result.receipt.cache["activation"]
        self.assertEqual("generation_advanced", activation["reason"])
        self.assertEqual(1, len(activation["invalidated_key_digests"]))

    def test_contract_snapshot_is_validated_and_decision_receipt_is_contract_safe(self):
        snapshot = copy.deepcopy(EXAMPLES["telemetry_snapshot"])
        result = PolicyEngine().decide(
            self.capabilities(),
            pressure_inputs(),
            telemetry_snapshot=snapshot,
            acceptance_rate=0.90,
        )

        self.assertIsNotNone(result.receipt.contract_receipt)
        validated = validate_decision_receipt(result.receipt.contract_receipt)
        self.assertEqual("decision_receipt", validated["message_type"])
        self.assertEqual(
            snapshot["generation"]["generation_id"],
            validated["generation"]["generation_id"],
        )

    def test_receipt_limits_are_explicit_and_enforced(self):
        with self.assertRaises(PolicyEngineError):
            PolicyEngineConfig(max_receipt_bytes=255)

        engine = PolicyEngine(
            config=PolicyEngineConfig(max_receipt_items=2),
        )
        result = engine.decide(
            self.capabilities(),
            pressure_inputs(),
            profile=profile("generation-bounded"),
        )
        self.assertLessEqual(len(result.receipt.reason_codes), 2)
        self.assertLessEqual(
            len(json.dumps(result.to_dict(), sort_keys=True).encode("utf-8")),
            8_192,
        )


if __name__ == "__main__":
    unittest.main()
