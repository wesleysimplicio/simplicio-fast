from __future__ import annotations

import copy
import unittest

from scripts.perf_gate import evaluate


def receipt(*, hot: float = 100.0, speedup: float = 1.5, tokens: float = 75.0, repetitions: int = 10) -> dict:
    return {
        "schema": "simplicio.fast.e2e-benchmark/v1",
        "environment": {"python": "3.14", "platform": "test"},
        "workload": {"files": 50, "functions_per_file": 20, "term": "task_7"},
        "totals": {
            "fast_python_alteration_wall_ms": hot,
            "alteration_speedup_hot": speedup,
            "estimated_token_savings_percent": tokens,
        },
        "scenarios": {
            "without_fast_alteration": {"repetitions": repetitions},
            "fast_python_alteration": {"repetitions": repetitions},
            "fast_python_alteration_refresh": {"repetitions": repetitions},
            "full_standalone": {"status": "blocked", "reason": "runtime_missing"},
            "loop_standalone": {"status": "blocked", "reason": "runtime_missing"},
        },
    }


class PerfGateTest(unittest.TestCase):
    def test_improvement_is_reported_without_treating_blocked_cells_as_pass(self) -> None:
        result = evaluate(receipt(), receipt(hot=90.0, speedup=1.6, tokens=80.0))
        self.assertEqual("inconclusive", result["status"])
        self.assertEqual(2, len(result["blocked_scenarios"]))

    def test_regression_exceeding_budget_is_reported(self) -> None:
        result = evaluate(receipt(), receipt(hot=120.0, speedup=1.0, tokens=60.0))
        self.assertEqual("regressed", result["status"])
        self.assertTrue(any(check["status"] == "fail" for check in result["checks"]))

    def test_missing_or_insufficient_repetitions_is_inconclusive(self) -> None:
        candidate = receipt(repetitions=9)
        result = evaluate(receipt(), candidate)
        self.assertEqual("inconclusive", result["status"])
        self.assertEqual("minimum_repetitions_not_met", result["reason"])

    def test_missing_metric_is_not_coerced_to_zero(self) -> None:
        candidate = copy.deepcopy(receipt())
        del candidate["totals"]["alteration_speedup_hot"]
        result = evaluate(receipt(), candidate)
        self.assertEqual("inconclusive", result["status"])
        speedup = next(check for check in result["checks"] if check["metric"] == "alteration_speedup_hot")
        self.assertEqual("inconclusive", speedup["status"])


if __name__ == "__main__":
    unittest.main()
