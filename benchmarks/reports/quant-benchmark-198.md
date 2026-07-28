# Quant benchmark Q0/Q1/Q2 — issue #198

- Classification: `MEASURED`
- Source commit: `5ec1284af830991d4ee03c915a707dc0c9b81e3e`
- Source tree: `318dd6b718b2f5d85136d592229eca7edbc4dcc0`
- Reproducible clean tree: `True`
- Config hash: `5c8c6711592e4d4487c634318040f0af47e27eb725db99539ccb158896fb1944`
- Generation: `634e7db3c342dabaff4f5d6fc7b2c3df9fedc61eda878ea21a512b6731aacdc9`
- Reproduction: `PYTHONPATH=src python benchmarks/quant_benchmark_198.py --repetitions 10 --sizes 10000,100000,1000000 --max-vectors 10000 --dimension 16 --candidate-k 80 --result-k 20 --seed 198`
- Rust parity: `null` (`RUNTIME_FAST_QUANT_CAPABILITY_UNAVAILABLE`); no Rust compilation was attempted.

![Measured size, latency and quality trade-off](quant-benchmark-198.svg)

Measured lanes use identical corpus, queries, judgments, embeddings and configuration.
Q2a is 4-bit without reranking; Q2b reranks its candidates with integral vectors.

## 10,000 vectors

Dataset: `62e2e7a1315727c2aae27db671e84e355d5c8bd2282a6eceb9dff51ecc585802`

Corpus: `8f95e8baeb10573468a7b148a6c4fc267c833ff62253b0412046d5175d47df4e`

Queries: `d861029d45904f180de799ddebe177ae51c51bda8a390bf128bfe83afaae6526`

Embeddings: `31c6cbcec7046af37ad7110e28de4f5bcfefbab507cbd7f94b240e46ef136ad3`

| Lane | Index bytes | Total bytes | Gate memory bytes | Reduction | Query p50 ms | Query p95 ms | Recall@10 | nDCG@10 | Rerank p50 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q0 | 1,410,016 | 1,410,016 | 1,410,016 | 0.00% | 42.181 | 56.382 | 1.0000 | 1.0000 | 0.000 |
| Q1 | 330,016 | 1,610,016 | 1,610,016 | 76.59% | 33.112 | 43.200 | 0.9812 | 0.9871 | 0.000 |
| Q2a | 250,024 | 1,530,024 | 1,530,024 | 82.27% | 37.522 | 45.506 | 0.0688 | 0.0618 | 0.000 |
| Q2b | 250,024 | 1,530,024 | 1,530,024 | 82.27% | 40.162 | 54.965 | 0.3438 | 0.4817 | 0.388 |

Promotion gate: **REJECT** (fail-closed).

```json
{
  "checks": {
    "latency": true,
    "measured_only": true,
    "memory_policy": false,
    "quality_ndcg": false,
    "quality_recall": false,
    "rerank_present": true
  },
  "decision": "REJECT",
  "fail_closed": true,
  "observed": {
    "index_reduction": 0.8226800263259424,
    "ndcg_at_10_regression": 0.518311222064248,
    "policy_memory_reduction": -0.08511109093797509,
    "promotion_memory_bytes": {
      "q0": 1410016,
      "q2b": 1530024
    },
    "query_p95_latency_ratio": 0.9748618159154268,
    "recall_at_10_regression": 0.65625,
    "storage_policy": "dedicated-end-to-end",
    "total_storage_reduction": -0.08511109093797509
  },
  "schema": "simplicio.fast.quant-promotion-gate/v1",
  "thresholds": {
    "max_ndcg_regression": 0.02,
    "max_recall_regression": 0.02,
    "maximum_latency_ratio": 1.5,
    "minimum_index_reduction": 0.5
  }
}
```

## Unavailable sizes

Unexecuted sizes are classified `BLOCKED`, have `null` values and stable reasons; no projection is substituted for a measurement.

| Vectors | Value | Reason |
|---:|---:|---|
| 100,000 | null | `CAPACITY_LIMIT_CONFIGURED` |
| 1,000,000 | null | `CAPACITY_LIMIT_CONFIGURED` |

## Classification boundary

- `measured`: raw samples from this machine, at least ten repetitions per lane.
- `simulated`: `null`; simulations were not run or mixed into rankings.
- Claims about speed or memory are restricted to measured samples in the JSON.
- Current-RSS values may be `null` with a reason on hosts without `/proc`; index bytes, page faults, I/O blocks and peak RSS remain independently reported.
