import ast
import json
import resource
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from simplicio_fast.snapshot import Snapshot, build_snapshot, source_files


def generate_project(root: Path, files: int = 500) -> None:
    package = root / "app"
    package.mkdir(parents=True)
    for index in range(files):
        (package / f"service_{index:04d}.py").write_text(
            f'''class UserService{index}:
    def create_user(self, name: str) -> dict:
        return {{"id": {index}, "name": name}}

    def update_user(self, user_id: int, name: str) -> dict:
        return {{"id": user_id, "name": name}}
'''
        )


def baseline_query(root: Path, term: str) -> int:
    matches = 0
    for path in source_files(root):
        tree = ast.parse(path.read_text(), filename=str(path))
        matches += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and term.casefold() in node.name.casefold()
        )
    return matches


def measure(operation, repetitions: int) -> dict[str, float]:
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


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "project"
        generate_project(root)
        snapshot_path = root / ".simplicio-fast/project.sfast"

        baseline = measure(lambda: baseline_query(root, "update_user"), 10)
        cold = build_snapshot(root, snapshot_path)
        with Snapshot(snapshot_path) as snapshot:
            mapped = measure(lambda: snapshot.find("update_user"), 100)
        warm_build = build_snapshot(root, snapshot_path)

        changed_file = root / "app/service_0000.py"
        changed_file.write_text(
            changed_file.read_text()
            + "\n\ndef deactivate_user(user_id: int) -> bool:\n    return user_id >= 0\n"
        )
        incremental = build_snapshot(root, snapshot_path)
        with Snapshot(snapshot_path) as snapshot:
            changed_visible = len(snapshot.find("deactivate_user")) == 1

        result = {
            "environment": {
                "python": sys.version.split()[0],
                "generated_files": 500,
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            },
            "baseline_ast_query": baseline,
            "snapshot_cold_build": asdict(cold),
            "snapshot_mmap_query": mapped,
            "snapshot_no_change_build": asdict(warm_build),
            "snapshot_one_file_change": asdict(incremental),
            "changed_symbol_visible": changed_visible,
            "query_speedup": baseline["wall_median_ms"] / mapped["wall_median_ms"],
            "query_cpu_reduction_percent": (
                1 - mapped["cpu_median_ms"] / baseline["cpu_median_ms"]
            )
            * 100,
        }
        output = Path("benchmarks/results/latest.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
