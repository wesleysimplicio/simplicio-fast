"""Command-line entrypoint for the cross-repository conformance gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cross_repo import (
    CrossRepoError,
    load_stack_lock,
    receipt_json,
    validate_stack_lock,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simplicio-fast-cross-repo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate a pinned stack-lock JSON")
    validate.add_argument("--file", required=True, type=Path)
    validate.add_argument("--profile", choices=("loop-standalone", "runtime-backed"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "validate":
        return 2
    try:
        receipt = validate_stack_lock(
            load_stack_lock(args.file.read_bytes()), profile=args.profile
        )
    except (OSError, CrossRepoError) as error:
        reason = error.reason_code if isinstance(error, CrossRepoError) else "manifest_read_failed"
        print(json.dumps({"schema": "simplicio.fast.cross-repo-receipt/v1", "status": "blocked", "reason_code": reason}, sort_keys=True))
        return 2
    print(receipt_json(receipt), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

