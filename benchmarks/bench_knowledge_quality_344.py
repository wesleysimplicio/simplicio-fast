"""Measured quality receipt for the bounded Knowledge projection (#344)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
from pathlib import Path
from typing import Any

from simplicio_fast.knowledge_projection import KnowledgeFact, KnowledgeProjection


SCHEMA = "simplicio.fast.knowledge-quality-receipt/v1"
CORPUS = Path(__file__).parents[1] / "fixtures" / "knowledge" / "v1" / "issue344-quality.json"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ndcg(relevances: list[int], ideal: list[int]) -> float:
    def dcg(values: list[int]) -> float:
        return sum(value / math.log2(index + 2) for index, value in enumerate(values))

    ideal_score = dcg(ideal)
    return dcg(relevances) / ideal_score if ideal_score else 1.0


def run() -> dict[str, Any]:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    if corpus.get("schema") != "simplicio.fast.knowledge-quality-corpus/v1":
        raise ValueError("knowledge quality corpus schema mismatch")
    projection = KnowledgeProjection(
        corpus["repository"], corpus["scope"], corpus["generation"]
    )
    delta = projection.apply_delta(
        KnowledgeFact(**item) for item in corpus["facts"]
    )
    results: list[dict[str, Any]] = []
    for query in corpus["queries"]:
        expected = set(query["expected_handles"])
        response = projection.query(query["text"], max_results=10)
        handles = response["handles"]
        hits = len(expected.intersection(handles))
        relevances = [1 if handle in expected else 0 for handle in handles]
        ideal = [1] * min(len(expected), len(handles))
        results.append(
            {
                "id": query["id"],
                "text": query["text"],
                "expected_handles": sorted(expected),
                "handles": handles,
                "precision": hits / len(handles) if handles else 0.0,
                "recall": hits / len(expected) if expected else 1.0,
                "ndcg": _ndcg(relevances, ideal),
                "truncated": response["truncated"],
                "explain": [item["explain"] for item in response["results"]],
            }
        )
    excluded = {
        "knowledge:parser-revoked",
        "knowledge:parser-expired",
        "knowledge:conflict",
    }
    returned = {handle for item in results for handle in item["handles"]}
    return {
        "schema": SCHEMA,
        "status": "partial",
        "scope": "frozen-knowledge-fixture",
        "dataset_id": corpus["dataset_id"],
        "dataset_sha256": _digest(corpus),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "delta": delta,
        "results": results,
        "aggregate": {
            "precision": statistics.median(item["precision"] for item in results),
            "recall": statistics.median(item["recall"] for item in results),
            "ndcg": statistics.median(item["ndcg"] for item in results),
            "excluded_inactive_or_conflicted_returned": sorted(excluded.intersection(returned)),
            "authoritative_source": "mapper",
        },
        "unverified": ["real_corpus_quality", "vector_ranking_quality", "rust_parity", "installed_consumer_e2e"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    receipt = run()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
