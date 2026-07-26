"""Run the bounded Python/Rust SFAST v2 differential conformance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "simplicio.fast.conformance/v1"


def _json_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "reason": "engine_command_failed",
                    "command": command,
                    "returncode": completed.returncode,
                    "stderr": completed.stderr.strip(),
                },
                sort_keys=True,
            )
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"engine_output_invalid_json: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("engine_output_not_object")
    return value


def _python_stats(snapshot: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "simplicio_fast.cli",
        "stats",
        "--snapshot",
        str(snapshot),
        "--fast-engine",
        "python",
    ]
    payload = _json_command(command)
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        raise RuntimeError("python_stats_missing")
    return stats


def _rust_stats(rust: Path, snapshot: Path) -> dict[str, Any]:
    payload = _json_command([str(rust), "--stats", str(snapshot), "--json"])
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        raise RuntimeError("rust_stats_missing")
    return stats


def _python_query(snapshot: Path, term: str) -> list[dict[str, Any]]:
    payload = _json_command([
        sys.executable,
        "-m",
        "simplicio_fast.cli",
        "query",
        term,
        "--snapshot",
        str(snapshot),
        "--limit",
        "50",
        "--fast-engine",
        "python",
    ])
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise RuntimeError("python_query_missing")
    return matches


def _rust_query(rust: Path, snapshot: Path, term: str) -> list[dict[str, Any]]:
    payload = _json_command([str(rust), "--query", str(snapshot), term, "--limit", "50", "--json"])
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise RuntimeError("rust_query_missing")
    return matches


def normalize(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": stats.get("format_version", stats.get("version")),
        "bytes": stats.get("bytes"),
        "files": stats.get("files"),
        "symbols": stats.get("symbols"),
        "relations": stats.get("relations"),
        "sections": sorted(stats.get("sections", [])),
        "generation": stats.get("generation"),
    }


def normalize_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("name", "qualified_name", "kind", "file", "line", "end_line", "symbol_id", "signature")
    return [{field: symbol.get(field) for field in fields} for symbol in symbols]


def run(snapshot: Path, rust: Path, term: str | None = None) -> dict[str, Any]:
    snapshot_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    python = _python_stats(snapshot)
    rust_stats = _rust_stats(rust, snapshot)
    python_normalized = normalize(python)
    rust_normalized = normalize(rust_stats)
    mismatches = {
        key: {"python": python_normalized[key], "rust": rust_normalized[key]}
        for key in python_normalized
        if python_normalized[key] != rust_normalized[key]
    }
    queries: dict[str, Any] = {}
    if term:
        python_symbols = normalize_symbols(_python_query(snapshot, term))
        rust_symbols = normalize_symbols(_rust_query(rust, snapshot, term))
        queries = {
            "term": term,
            "python": python_symbols,
            "rust": rust_symbols,
            "match": python_symbols == rust_symbols,
        }
    query_mismatch = bool(queries and not queries["match"])
    return {
        "schema": SCHEMA,
        "status": "pass" if not mismatches and not query_mismatch else "fail",
        "snapshot": str(snapshot.resolve()),
        "snapshot_sha256": snapshot_digest,
        "engines": {"python": python_normalized, "rust": rust_normalized},
        "mismatches": mismatches,
        "queries": queries,
        "query_mismatch": query_mismatch,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--term", help="also compare a public symbol query")
    args = parser.parse_args()
    try:
        receipt = run(args.snapshot, args.rust, args.term)
    except (OSError, RuntimeError) as error:
        receipt = {
            "schema": SCHEMA,
            "status": "error",
            "snapshot": str(args.snapshot.resolve()),
            "reason": str(error),
        }
    output = json.dumps(receipt, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if receipt["status"] == "pass" and not receipt.get("query_mismatch") else 2


if __name__ == "__main__":
    raise SystemExit(main())
