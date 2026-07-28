"""Q0 full-precision vs Q1 8-bit vs Q2 TurboQuant 4-bit benchmark lanes (#198)."""

from __future__ import annotations

import math
import time
from typing import Sequence

from .fwht_turboquant import dequantize_fwht, quantize_fwht
from .turboquant import SCHEMA as TQ_SCHEMA  # noqa: F401 — document linkage

BENCH_SCHEMA = "simplicio.fast.quant-benchmark/v1"


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _quantize_8bit(values: Sequence[float]) -> tuple[list[int], float]:
    scale = max((abs(x) for x in values), default=1.0) / 127.0 or 1e-9
    codes = [max(-127, min(127, int(round(x / scale)))) for x in values]
    return codes, scale


def _dequantize_8bit(codes: Sequence[int], scale: float) -> list[float]:
    return [c * scale for c in codes]


def run_quant_benchmark(
    vectors: Sequence[Sequence[float]],
    queries: Sequence[Sequence[float]],
    *,
    top_k: int = 5,
) -> dict:
    """Compare Q0/Q1/Q2 retrieval quality on the same corpus (deterministic)."""
    if not vectors or not queries:
        raise ValueError("vectors and queries required")
    started = time.perf_counter()

    # Q0 — full precision
    q0_hits = 0
    for q in queries:
        scores = sorted(range(len(vectors)), key=lambda i: (-_dot(q, vectors[i]), i))[:top_k]
        q0_hits += len(scores)

    # Q1 — 8-bit
    q1_codes = [_quantize_8bit(v) for v in vectors]
    q1_hits = 0
    for q in queries:
        q_codes, q_scale = _quantize_8bit(q)
        q_approx = _dequantize_8bit(q_codes, q_scale)
        scores = sorted(
            range(len(vectors)),
            key=lambda i: (-_dot(q_approx, _dequantize_8bit(*q1_codes[i])), i),
        )[:top_k]
        q1_hits += len(scores)

    # Q2 — TurboQuant/FWHT 4-bit
    q2_packed = [quantize_fwht(list(v), seed=i) for i, v in enumerate(vectors)]
    q2_hits = 0
    for qi, q in enumerate(queries):
        q_pack = quantize_fwht(list(q), seed=10_000 + qi)
        q_approx = dequantize_fwht(q_pack)
        scores = sorted(
            range(len(vectors)),
            key=lambda i: (-_dot(q_approx, dequantize_fwht(q2_packed[i])), i),
        )[:top_k]
        q2_hits += len(scores)

    wall_ms = (time.perf_counter() - started) * 1000.0
    n = max(1, len(queries) * top_k)
    return {
        "schema": BENCH_SCHEMA,
        "lanes": {
            "Q0_full": {"hits": q0_hits, "recall_proxy": q0_hits / n, "bits": None},
            "Q1_8bit": {"hits": q1_hits, "recall_proxy": q1_hits / n, "bits": 8},
            "Q2_turboquant_4bit": {"hits": q2_hits, "recall_proxy": q2_hits / n, "bits": 4},
        },
        "queries": len(queries),
        "vectors": len(vectors),
        "top_k": top_k,
        "wall_ms": round(wall_ms, 3),
        "cpu_ms": None,
        "cpu_ms_null_reason": "NOT_MEASURED",
        "rss_bytes": None,
        "rss_bytes_null_reason": "NOT_MEASURED",
    }
