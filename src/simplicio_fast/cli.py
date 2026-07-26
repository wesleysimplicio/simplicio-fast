import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .snapshot import Snapshot, StaleSnapshotError, build_snapshot
from .users.http import serve
from .users.repository import JsonUserRepository
from .users.service import UserService

DEFAULT_SNAPSHOT = ".simplicio-fast/project.sfast"


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def snapshot_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-s",
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help=f"snapshot path (default: {DEFAULT_SNAPSHOT})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simplicio-fast",
        description=(
            "Build and query versioned binary semantic snapshots without replacing source files."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("build", "refresh"):
        command = commands.add_parser(
            name,
            help=(
                "build a snapshot, reusing unchanged files"
                if name == "build"
                else "incrementally refresh the snapshot after source changes"
            ),
            description="Parse changed Python files and atomically publish a complete snapshot.",
        )
        command.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
        command.add_argument(
            "-o",
            "--output",
            default=DEFAULT_SNAPSHOT,
            help=f"output snapshot path (default: {DEFAULT_SNAPSHOT})",
        )

    query = commands.add_parser(
        "query",
        help="find classes and functions in a snapshot",
        description="Return matching semantic symbols as deterministic JSON.",
    )
    query.add_argument("term", help="case-insensitive symbol or qualified-name substring")
    snapshot_argument(query)
    query.add_argument("--limit", type=int, default=50, help="maximum matches (default: 50)")

    context = commands.add_parser(
        "context",
        help="return verified source spans for an LLM or agent",
        description=(
            "Resolve symbols through mmap, verify current source hashes and emit bounded source spans."
        ),
    )
    context.add_argument("term", help="case-insensitive symbol or qualified-name substring")
    context.add_argument("--root", default=".", help="repository root (default: .)")
    snapshot_argument(context)
    context.add_argument("--max-results", type=int, default=10)
    context.add_argument("--max-lines", type=int, default=120)
    context.add_argument("--max-bytes", type=int, default=32_000)

    doctor = commands.add_parser(
        "doctor",
        help="validate installation and snapshot integrity",
        description="Check Python, snapshot structure and query readiness; emits JSON.",
    )
    snapshot_argument(doctor)

    server = commands.add_parser("serve", help="run the user CRUD proof-of-concept API")
    server.add_argument("--port", type=int, default=3000)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command in {"build", "refresh"}:
            emit(
                {
                    "schema": "simplicio.fast.build/v1",
                    "version": __version__,
                    "snapshot": str(Path(args.output)),
                    "metrics": asdict(build_snapshot(Path(args.root), Path(args.output))),
                }
            )
        elif args.command == "query":
            if args.limit < 1:
                parser.error("--limit must be positive")
            with Snapshot(Path(args.snapshot)) as snapshot:
                emit(
                    {
                        "schema": "simplicio.fast.query/v1",
                        "snapshot_version": 1,
                        "matches": [
                            asdict(item) for item in snapshot.find(args.term)[: args.limit]
                        ],
                    }
                )
        elif args.command == "context":
            with Snapshot(Path(args.snapshot)) as snapshot:
                spans = snapshot.context(
                    Path(args.root),
                    args.term,
                    max_results=args.max_results,
                    max_lines=args.max_lines,
                    max_bytes=args.max_bytes,
                )
                emit(
                    {
                        "schema": "simplicio.fast.context/v1",
                        "snapshot_version": 1,
                        "limits": {
                            "max_results": args.max_results,
                            "max_lines": args.max_lines,
                            "max_bytes": args.max_bytes,
                        },
                        "spans": [asdict(item) for item in spans],
                    }
                )
        elif args.command == "doctor":
            path = Path(args.snapshot)
            checks: list[dict[str, object]] = [
                {"name": "python", "status": "pass", "detail": sys.version.split()[0]},
                {
                    "name": "snapshot_exists",
                    "status": "pass" if path.is_file() else "fail",
                    "detail": str(path),
                },
            ]
            if path.is_file():
                with Snapshot(path) as snapshot:
                    checks.append(
                        {
                            "name": "snapshot_integrity",
                            "status": "pass",
                            "detail": {
                                "files": snapshot.file_count,
                                "symbols": snapshot.symbol_count,
                                "format": "SFAST001/v1",
                            },
                        }
                    )
            ready = all(check["status"] == "pass" for check in checks)
            emit({"schema": "simplicio.fast.doctor/v1", "ready": ready, "checks": checks})
            if not ready:
                raise SystemExit(1)
        elif args.command == "serve":
            service = UserService(JsonUserRepository(Path("data/users.json")))
            print(f"simplicio-fast listening on http://127.0.0.1:{args.port}")
            serve(service, port=args.port)
    except (FileNotFoundError, ValueError, StaleSnapshotError) as error:
        emit(
            {
                "schema": "simplicio.fast.error/v1",
                "error": type(error).__name__,
                "message": str(error),
            }
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
