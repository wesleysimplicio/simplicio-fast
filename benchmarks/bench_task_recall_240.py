"""Measured DeliveryEngine recall on a frozen source-task corpus for #240."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from simplicio_fast.delivery import DeliveryEngine
from simplicio_fast.engine import select_engine
from simplicio_fast.snapshot import build_snapshot


SCHEMA = "simplicio.fast.delivery-task-recall-receipt/v1"
CORPUS = Path(__file__).parents[1] / "fixtures" / "delivery" / "v1" / "issue240-task-recall.json"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _task_result(
    task: dict[str, Any],
    engine: DeliveryEngine,
    engine_receipt: dict[str, Any],
    repetitions: int,
) -> dict[str, Any]:
    cold: list[float] = []
    warm: list[float] = []
    selected: list[str] = []
    recall: list[float] = []
    precision: list[float] = []
    expected = set(task["expected_files"])
    for repetition in range(repetitions):
        started = time.perf_counter_ns()
        engine.prepare(
            task["text"],
            profile="loop-standalone",
            mode="bootstrap",
            engine_receipt=engine_receipt,
            selection_mode="semantic",
            tokenizer_id="whitespace-v1",
            tokenizer=lambda text: len(text.split()),
        )
        cold.append((time.perf_counter_ns() - started) / 1_000_000)
        started = time.perf_counter_ns()
        result = engine.prepare(
            task["text"],
            profile="loop-standalone",
            mode="bootstrap",
            engine_receipt=engine_receipt,
            selection_mode="semantic",
            tokenizer_id="whitespace-v1",
            tokenizer=lambda text: len(text.split()),
        )
        warm.append((time.perf_counter_ns() - started) / 1_000_000)
        unique = list(dict.fromkeys(item["file"] for item in result["context"]["selected"]))
        hit_count = len(expected.intersection(unique))
        selected = unique
        recall.append(hit_count / len(expected) if expected else 1.0)
        precision.append(hit_count / len(unique) if unique else 0.0)
    return {
        "id": task["id"],
        "text": task["text"],
        "expected_files": sorted(expected),
        "selected_files": selected,
        "recall": statistics.median(recall),
        "precision": statistics.median(precision),
        "cold_median_ms": statistics.median(cold),
        "cold_p95_ms": _percentile(cold, 0.95),
        "warm_median_ms": statistics.median(warm),
        "warm_p95_ms": _percentile(warm, 0.95),
        "repetitions": repetitions,
        "raw": {
            "cold_ms": cold,
            "warm_ms": warm,
            "recall": recall,
            "precision": precision,
        },
    }


def run(root: Path | None = None, *, repetitions: int = 10) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    if corpus.get("schema") != "simplicio.fast.delivery-task-corpus/v1":
        raise ValueError("task corpus schema mismatch")
    source_root = (root or Path.cwd()).resolve()
    with tempfile.TemporaryDirectory(prefix="simplicio-fast-240-recall-") as directory:
        snapshot = Path(directory) / "source.sfast"
        build_snapshot(source_root, snapshot)
        engine_receipt = select_engine("python").receipt()
        results: list[dict[str, Any]] = []
        for task in corpus["tasks"]:
            results.append(
                _task_result(
                    task,
                    DeliveryEngine(source_root, snapshot, Path(directory) / task["id"]),
                    engine_receipt,
                    repetitions,
                )
            )
    return {
        "schema": SCHEMA,
        "status": "partial",
        "scope": corpus["scope"],
        "dataset_id": corpus["dataset_id"],
        "dataset_sha256": _digest(corpus),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "results": results,
        "aggregate": {
            "recall_median": statistics.median(item["recall"] for item in results),
            "precision_median": statistics.median(item["precision"] for item in results),
            "warm_p95_max_ms": max(item["warm_p95_ms"] for item in results),
            "downstream_success": None,
            "downstream_success_reason": "consumer_not_present_in_frozen_source_fixture",
        },
        "unverified": ["real_historical_task_recall", "downstream_consumer_success", "installed_cross_platform"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.root, repetitions=args.repetitions)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
