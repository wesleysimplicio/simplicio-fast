"""Reproducible end-to-end baseline versus Python Fast measurement.

This benchmark measures the same bounded-context task two ways: a direct source
scan on every repetition and a single Fast snapshot followed by repeated mmap
queries. Token values are explicitly deterministic estimates unless a caller
provides an observable tokenizer; they are never presented as provider usage.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import platform
import statistics
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from simplicio_fast.snapshot import Snapshot, build_snapshot


SCHEMA = "simplicio.fast.e2e-benchmark/v1"


def estimate_tokens(text: str) -> int:
    return max(1, len(text.split())) if text else 0


def make_workload(root: Path, *, files: int, functions: int) -> str:
    for index in range(files):
        lines = [f"class Service{index}:\n"]
        for function in range(functions):
            lines.extend(
                [
                    f"    def task_{function}(self, value):\n",
                    "        return value\n",
                ]
            )
        (root / f"service_{index}.py").write_text("".join(lines), encoding="utf-8")
    return "task_7"


def direct_context(root: Path, term: str, *, max_lines: int = 8) -> str:
    snippets: list[str] = []
    for path in sorted(root.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if term.casefold() in line.casefold():
                start = max(0, index - 2)
                end = min(len(lines), index + max_lines - 2)
                snippets.append(f"# {path.name}\n" + "\n".join(lines[start:end]))
    return "\n".join(snippets)


def ast_context(root: Path, term: str, *, max_lines: int = 8) -> str:
    snippets: list[str] = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lines = text.splitlines()
        for node in ast.walk(tree):
            name = getattr(node, "name", "")
            if term.casefold() not in name.casefold():
                continue
            line = max(1, getattr(node, "lineno", 1))
            start = max(0, line - 3)
            end = min(len(lines), start + max_lines)
            snippets.append(f"# {path.name}\n" + "\n".join(lines[start:end]))
    return "\n".join(snippets)


def timed(call: Callable[[], str], repetitions: int) -> dict[str, Any]:
    durations: list[float] = []
    bytes_seen = 0
    tokens = 0
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        context = call()
        durations.append((time.perf_counter_ns() - start) / 1_000_000)
        bytes_seen += len(context.encode("utf-8"))
        tokens += estimate_tokens(context)
    return {
        "repetitions": repetitions,
        "wall_ms": {
            "median": statistics.median(durations),
            "p95": sorted(durations)[max(0, math.ceil(repetitions * 0.95) - 1)],
            "samples": durations,
        },
        "context_bytes": bytes_seen,
        "estimated_input_tokens": tokens,
        "token_measurement": "whitespace-v1-estimate",
    }


def run(*, files: int, functions: int, repetitions: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="simplicio-fast-bench-") as directory:
        root = Path(directory)
        term = make_workload(root, files=files, functions=functions)
        source_bytes = sum(path.stat().st_size for path in root.glob("*.py"))
        baseline_scan = timed(lambda: direct_context(root, term), repetitions)
        baseline_ast = timed(lambda: ast_context(root, term), repetitions)

        snapshot = root / "project.sfast"
        build_start = time.perf_counter_ns()
        build = build_snapshot(root, snapshot)
        build_wall_ms = (time.perf_counter_ns() - build_start) / 1_000_000
        with Snapshot(snapshot) as opened:
            fast = timed(
                lambda: "\n".join(
                    span.content
                    for span in opened.context(root, term, max_results=files, max_bytes=64_000)
                ),
                repetitions,
            )

        baseline_scan_total = sum(baseline_scan["wall_ms"]["samples"])
        baseline_ast_total = sum(baseline_ast["wall_ms"]["samples"])
        fast_total = build_wall_ms + sum(fast["wall_ms"]["samples"])
        token_saved = baseline_ast["estimated_input_tokens"] - fast["estimated_input_tokens"]
        return {
            "schema": SCHEMA,
            "status": "complete",
            "workload": {"files": files, "functions_per_file": functions, "term": term},
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "source_bytes": source_bytes,
            "build": {"wall_ms": build_wall_ms, "metrics": asdict(build)},
            "scenarios": {
                "without_fast_scan": baseline_scan,
                "without_fast_ast_reparse": baseline_ast,
                "fast_python": fast,
            },
            "totals": {
                "without_fast_scan_wall_ms": baseline_scan_total,
                "without_fast_ast_reparse_wall_ms": baseline_ast_total,
                "fast_python_wall_ms": fast_total,
                "speedup_vs_scan": baseline_scan_total / fast_total if fast_total else None,
                "speedup_vs_ast_reparse": baseline_ast_total / fast_total if fast_total else None,
                "estimated_tokens_without_fast": baseline_ast["estimated_input_tokens"],
                "estimated_tokens_fast": fast["estimated_input_tokens"],
                "estimated_tokens_saved": token_saved,
                "estimated_token_savings_percent": (
                    token_saved / baseline_ast["estimated_input_tokens"] * 100
                    if baseline_ast["estimated_input_tokens"]
                    else None
                ),
            },
            "limitations": [
                "Fast Python is measured; Rust and Full/Loop ecosystem cells remain pending until their engines/integrations exist.",
                "Token counts use whitespace-v1 estimates, not provider billing telemetry.",
            ],
        }


def markdown_report(result: dict[str, Any]) -> str:
    totals = result["totals"]
    return "\n".join(
        [
            "# Fast versus baseline benchmark",
            "",
            f"- Workload: {result['workload']['files']} files × {result['workload']['functions_per_file']} functions",
            f"- Repetitions: {result['scenarios']['without_fast_scan']['repetitions']}",
            f"- Build wall time: {result['build']['wall_ms']:.3f} ms",
            f"- Baseline scan total wall time: {totals['without_fast_scan_wall_ms']:.3f} ms",
            f"- Baseline AST-reparse total wall time: {totals['without_fast_ast_reparse_wall_ms']:.3f} ms",
            f"- Fast Python total wall time: {totals['fast_python_wall_ms']:.3f} ms",
            f"- Speedup versus scan: {totals['speedup_vs_scan']:.3f}x",
            f"- Speedup versus AST reparse: {totals['speedup_vs_ast_reparse']:.3f}x",
            f"- Estimated input tokens without Fast: {totals['estimated_tokens_without_fast']}",
            f"- Estimated input tokens with Fast: {totals['estimated_tokens_fast']}",
            f"- Estimated tokens saved: {totals['estimated_tokens_saved']} ({totals['estimated_token_savings_percent']:.2f}%)",
            "",
            "Token values use `whitespace-v1-estimate`; they are not provider billing telemetry.",
            "Rust and Full/Loop cells remain pending until those engines/integrations are implemented.",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=50)
    parser.add_argument("--functions", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--json-out")
    parser.add_argument("--markdown-out")
    args = parser.parse_args()
    if min(args.files, args.functions, args.repetitions) < 1:
        parser.error("files, functions and repetitions must be positive")
    result = run(files=args.files, functions=args.functions, repetitions=args.repetitions)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown_report(result), encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
