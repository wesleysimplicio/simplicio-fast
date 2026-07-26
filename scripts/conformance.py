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


def run(snapshot: Path, rust: Path) -> dict[str, Any]:
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
    return {
        "schema": SCHEMA,
        "status": "pass" if not mismatches else "fail",
        "snapshot": str(snapshot.resolve()),
        "snapshot_sha256": snapshot_digest,
        "engines": {"python": python_normalized, "rust": rust_normalized},
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    try:
        receipt = run(args.snapshot, args.rust)
    except (OSError, RuntimeError) as error:
        receipt = {
            "schema": SCHEMA,
            "status": "error",
            "snapshot": str(args.snapshot.resolve()),
            "reason": str(error),
        }
    output = json.dumps(receipt, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if receipt["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
