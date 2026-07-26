import ast
import json
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from simplicio_fast.snapshot import Snapshot, build_snapshot, source_files


def _unavailable(reason: str) -> dict[str, int | str | None]:
    return {"value": None, "reason": reason}


def _posix_peak_rss_kib(platform: str) -> dict[str, int | str | None]:
    try:
        import resource
    except ImportError:
        return _unavailable("posix_resource_module_unavailable")

    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError, ValueError):
        return _unavailable("posix_resource_getrusage_unavailable")

    # Linux and the BSDs report KiB; macOS reports bytes.
    if platform == "darwin":
        value //= 1024
    return {"value": value, "reason": None}


def _windows_peak_rss_kib() -> dict[str, int | str | None]:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_memory_info.restype = wintypes.BOOL
        if not get_memory_info(process, ctypes.byref(counters), counters.cb):
            return _unavailable("windows_get_process_memory_info_failed")
        return {"value": int(counters.PeakWorkingSetSize // 1024), "reason": None}
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return _unavailable("windows_process_memory_api_unavailable")


def peak_rss_metric(platform: str | None = None) -> dict[str, int | str | None]:
    """Return a deterministic peak-RSS value and an unavailable reason, if any."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return _windows_peak_rss_kib()
    return _posix_peak_rss_kib(platform)


def peak_rss_kib() -> int | None:
    """Return peak RSS in KiB for callers that only need the numeric value."""
    return peak_rss_metric()["value"]  # type: ignore[return-value]


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

        peak_rss = peak_rss_metric()
        status = "complete" if peak_rss["reason"] is None else "partial"
        result = {
            "schema": "simplicio.fast.benchmark/v1",
            "status": status,
            "environment": {
                "python": sys.version.split()[0],
                "generated_files": 500,
                "peak_rss_kib": peak_rss["value"],
                "peak_rss_reason": peak_rss["reason"],
                "metrics_status": status,
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
