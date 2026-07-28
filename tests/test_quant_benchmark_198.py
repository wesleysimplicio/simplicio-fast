from __future__ import annotations

from simplicio_fast.quant_benchmark import run_quant_benchmark


def test_quant_lanes_run_deterministically():
    vectors = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.7, 0.7, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ]
    queries = [[0.9, 0.1, 0.0, 0.0], [0.1, 0.9, 0.0, 0.0]]
    first = run_quant_benchmark(vectors, queries, top_k=2)
    second = run_quant_benchmark(vectors, queries, top_k=2)
    assert first["schema"].startswith("simplicio.fast.quant-benchmark")
    assert set(first["lanes"]) == {"Q0_full", "Q1_8bit", "Q2_turboquant_4bit"}
    assert first["lanes"] == second["lanes"]
    assert first["cpu_ms"] is None
    assert first["cpu_ms_null_reason"] == "NOT_MEASURED"
