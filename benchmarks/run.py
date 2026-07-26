"""Reproducible baseline/Fast benchmark for 1k, 10k and 100k symbols."""

import argparse
import ast
import json
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows Python can omit resource.
    resource = None

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from simplicio_fast.snapshot import Snapshot, build_snapshot, source_files


def generate_project(root: Path, symbols: int) -> None:
    files = max(1, (symbols + 99) // 100)
    package = root / "app"
    package.mkdir(parents=True)
    for file_index in range(files):
        start = file_index * 100
        functions = []
        for offset in range(min(100, symbols - start)):
            index = start + offset
            functions.append(
                f"def update_user_{index}(user_id: int) -> int:\n    return user_id + {index}\n"
            )
        (package / f"service_{file_index:04d}.py").write_text("\n".join(functions), encoding="utf-8")


def baseline_query(root: Path, term: str) -> int:
    matches = 0
    for path in source_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        matches += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and term.casefold() in node.name.casefold()
        )
    return matches


def measure(operation, repetitions: int) -> dict[str, float | int]:
    wall: list[float] = []
    cpu: list[float] = []
    for _ in range(repetitions):
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        operation()
        wall.append((time.perf_counter() - wall_start) * 1000)
        cpu.append((time.process_time() - cpu_start) * 1000)
    return {
        "repetitions": repetitions,
        "wall_median_ms": statistics.median(wall),
        "wall_p95_ms": sorted(wall)[max(0, int(repetitions * 0.95) - 1)],
        "cpu_median_ms": statistics.median(cpu),
    }


def process_usage() -> dict[str, int | None]:
    if resource is None:
        return {"peak_rss_kib": None, "minor_page_faults": None, "major_page_faults": None}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "peak_rss_kib": int(usage.ru_maxrss),
        "minor_page_faults": int(getattr(usage, "ru_minflt", 0)),
        "major_page_faults": int(getattr(usage, "ru_majflt", 0)),
    }


def run_size(size: int, repetitions: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "project"
        generate_project(root, size)
        snapshot_path = root / ".simplicio-fast/project.sfast"
        baseline = measure(lambda: baseline_query(root, "update_user"), repetitions)
        cold = build_snapshot(root, snapshot_path)
        with Snapshot(snapshot_path) as snapshot:
            mapped = measure(lambda: snapshot.find("update_user"), repetitions)
        warm_build = build_snapshot(root, snapshot_path)
        changed_file = root / "app/service_0000.py"
        changed_file.write_text(
            changed_file.read_text(encoding="utf-8")
            + "\ndef deactivate_user(user_id: int) -> bool:\n    return user_id >= 0\n",
            encoding="utf-8",
        )
        incremental = build_snapshot(root, snapshot_path)
        with Snapshot(snapshot_path) as snapshot:
            changed_visible = len(snapshot.find("deactivate_user")) == 1
        return {
            "symbols_requested": size,
            "files": cold.files,
            "repetitions": repetitions,
            "baseline_ast_query": baseline,
            "snapshot_cold_build": asdict(cold),
            "snapshot_mmap_query": mapped,
            "snapshot_no_change_build": asdict(warm_build),
            "snapshot_one_file_change": asdict(incremental),
            "changed_symbol_visible": changed_visible,
            "query_speedup": baseline["wall_median_ms"] / mapped["wall_median_ms"],
            "query_cpu_reduction_percent": (1 - mapped["cpu_median_ms"] / baseline["cpu_median_ms"]) * 100,
            "usage": process_usage(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,10000,100000", help="comma-separated symbol counts")
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    if args.repetitions < 10:
        parser.error("--repetitions must be at least 10")
    sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
    if not sizes or any(size < 1 for size in sizes):
        parser.error("--sizes must contain positive integers")
    result = {
        "schema": "simplicio.fast.benchmark/v1",
        "environment": {"python": sys.version.split()[0], "usage": process_usage()},
        "workload": {"sizes": sizes, "repetitions": args.repetitions, "query": "update_user"},
        "runs": [run_size(size, args.repetitions) for size in sizes],
    }
    output = Path("benchmarks/results/latest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
