"""Issue #246 size-matrix runner with raw, fail-closed receipts.

The runner delegates each workload to the existing end-to-end comparator. It
does not turn unavailable Runtime/Loop/native cells into measurements.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

from benchmarks.compare_fast import run as run_comparison

SCHEMA = "simplicio.fast.issue246-benchmark/v1"
DEFAULT_SIZES = (10_000, 100_000, 1_000_000)


def _shape(symbols: int) -> tuple[int, int]:
    if symbols < 1:
        raise ValueError("symbols must be positive")
    files = max(1, math.ceil(math.sqrt(symbols)))
    functions = math.ceil(symbols / files)
    return files, functions


def _parse_sizes(values: Iterable[int]) -> tuple[int, ...]:
    sizes = tuple(int(value) for value in values)
    if not sizes or any(value < 1 for value in sizes):
        raise ValueError("sizes must contain positive values")
    return sizes


def run_matrix(
    *,
    sizes: Iterable[int] = DEFAULT_SIZES,
    repetitions: int = 10,
    rust_executable: Path | None = None,
) -> dict[str, Any]:
    if repetitions < 10:
        raise ValueError("repetitions must be at least 10")
    requested_sizes = _parse_sizes(sizes)
    raw: list[dict[str, Any]] = []
    for symbols in requested_sizes:
        files, functions = _shape(symbols)
        try:
            receipt = run_comparison(
                files=files,
                functions=functions,
                repetitions=repetitions,
                rust_executable=rust_executable,
                compact_symbols=symbols >= 1_000_000,
            )
        except Exception as error:  # noqa: BLE001 - preserve a fail-closed receipt
            receipt = {
                "status": "blocked",
                "reason": "comparison_failed",
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
                "claims": {
                    "speed": "unavailable",
                    "tokens": "unavailable",
                },
            }
        raw.append(
            {
                "requested_symbols": symbols,
                "workload_shape": {
                    "files": files,
                    "functions_per_file": functions,
                    "generated_symbols": files * functions,
                },
                "receipt": receipt,
            }
        )
    status = (
        "complete"
        if all(item["receipt"].get("status") == "complete" for item in raw)
        else "partial"
    )
    return {
        "schema": SCHEMA,
        "status": status,
        "requested_sizes": list(requested_sizes),
        "repetitions": repetitions,
        "rust_executable": str(rust_executable) if rust_executable else None,
        "raw_runs": raw,
        "claims": {
            "speed": "unavailable until every compared scenario is complete and parity is verified",
            "tokens": "unavailable unless exact tokenizer/provider counters are present",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--rust-executable", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    receipt = run_matrix(
        sizes=(int(value) for value in args.sizes.split(",")),
        repetitions=args.repetitions,
        rust_executable=args.rust_executable,
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
