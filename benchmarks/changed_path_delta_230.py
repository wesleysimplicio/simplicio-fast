"""Bounded changed-path delta handoff benchmark for issue #230."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile

from pathlib import Path
from time import perf_counter

from simplicio_fast.snapshot import build_snapshot
from simplicio_fast.workspace import WorkspaceStore

SCHEMA = "simplicio.fast.changed-path-delta-benchmark/v1"


def run(*, files: int = 24, repetitions: int = 3) -> dict[str, object]:
    if files < 2 or repetitions < 3:
        raise ValueError("files must be at least two and repetitions must be at least three")
    with tempfile.TemporaryDirectory(prefix="simplicio-fast-delta-") as directory:
        root = Path(directory)
        for index in range(files):
            (root / f"module_{index:03d}.py").write_text(
                f"def value_{index}():\n    return {index}\n", encoding="utf-8"
            )
        store = WorkspaceStore(root)
        base = store.build_base(config={"benchmark": "issue-230"})
        raw = {"cold_build_ms": [], "cold_ms": [], "warm_ms": [], "incremental_ms": []}
        parity = []
        handoff_files_parsed = []
        handoff_cache_reuse = []
        for revision in range(repetitions):
            source = root / "module_000.py"
            source.write_text(
                f"def value_0():\n    return {revision + 1}\n", encoding="utf-8"
            )
            full = root / f"full-{revision}.sfast"
            cold_build_start = perf_counter()
            build_snapshot(root, full)
            raw["cold_build_ms"].append((perf_counter() - cold_build_start) * 1000)
            report = store.handoff(
                base.generation_id, "benchmark", ["module_000.py"], parity_snapshot=full
            )
            parity.append(report["parity"])
            handoff_files_parsed.append(int(report["files_parsed"]))
            handoff_cache_reuse.append(int(report["cache_reuse"]))
            for mode in ("cold_ms", "warm_ms", "incremental_ms"):
                raw[mode].append(float(report["timings_ms"][mode]))
        return {
            "schema": SCHEMA,
            "status": "pass" if all(parity) else "parity_mismatch",
            "workload": {"files": files, "changed_files": 1, "repetitions": repetitions},
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "raw": raw,
            "totals": {mode + "_median_ms": statistics.median(samples) for mode, samples in raw.items()},
            "parity": parity,
            "files_parsed": handoff_files_parsed[-1],
            "cache_reuse": handoff_cache_reuse[-1],
            "handoff": {
                "files_parsed": handoff_files_parsed,
                "cache_reuse": handoff_cache_reuse,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=24)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    receipt = run(files=args.files, repetitions=args.repetitions)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()