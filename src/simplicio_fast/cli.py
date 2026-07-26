import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .processor import ProjectProcessor, load_changeset
from .rollout import RolloutController
from .snapshot import Snapshot, StaleSnapshotError, build_snapshot
from .adapters import capability_report
from .workspace import MANIFEST_SCHEMA, OVERLAY_SCHEMA, WorkspaceStore
from .users.http import serve
from .users.repository import JsonUserRepository
from .users.service import UserService

DEFAULT_SNAPSHOT = ".simplicio-fast/project.sfast"


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def source_commit(root: Path) -> tuple[str | None, str | None]:
    """Return the checked-out commit, or a reason when root is outside Git."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None, "git_unavailable"
    commit = result.stdout.strip()
    if result.returncode or not commit:
        return None, "not_a_git_checkout"
    return commit, None


def json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON (the default)")


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

    for name in ("build", "refresh", "ingest"):
        command = commands.add_parser(
            name,
            help=(
                "build a snapshot, reusing unchanged files"
                if name == "build"
                else (
                    "incrementally refresh the snapshot after source changes"
                    if name == "refresh"
                    else "absorb a project into the binary semantic processor"
                )
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
        json_option(command)

    query = commands.add_parser(
        "query",
        help="find classes and functions in a snapshot",
        description="Return matching semantic symbols as deterministic JSON.",
    )
    query.add_argument("term", help="case-insensitive symbol or qualified-name substring")
    snapshot_argument(query)
    query.add_argument("--limit", type=int, default=50, help="maximum matches (default: 50)")
    json_option(query)

    search = commands.add_parser(
        "search",
        help="search direct indexes by name, path or kind",
        description="Resolve symbols from direct indexes without deserializing the full symbol table.",
    )
    search.add_argument("term", help="case-insensitive name or qualified-name substring")
    snapshot_argument(search)
    search.add_argument("--limit", type=int, default=50, help="maximum matches (default: 50)")
    search.add_argument("--prefix", action="store_true", help="match names beginning with term")
    search.add_argument("--path", help="restrict matches to a relative source path")
    search.add_argument("--kind", choices=("class", "function", "async_function"))
    json_option(search)

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
    context.add_argument("--max-tokens", type=int, default=8_000)
    json_option(context)

    impact = commands.add_parser(
        "impact",
        help="return typed imports, references, calls and test relations",
        description="Return bounded deterministic impact relationships for a symbol or term.",
    )
    impact.add_argument("term", help="symbol, path or relation term")
    snapshot_argument(impact)
    impact.add_argument("--limit", type=int, default=100)
    json_option(impact)

    stats = commands.add_parser("stats", help="show snapshot generation and section statistics")
    snapshot_argument(stats)
    json_option(stats)

    for name in ("understand", "plan"):
        command = commands.add_parser(
            name,
            help=(
                "understand a task using bounded project context"
                if name == "understand"
                else "compile a task and semantic context into a PlanDAG"
            ),
        )
        command.add_argument("task", help="task or goal in natural language")
        command.add_argument("--root", default=".", help="repository root (default: .)")
        snapshot_argument(command)
        command.add_argument("--max-bytes", type=int, default=48_000)

    apply_command = commands.add_parser(
        "apply",
        help="validate or apply a hash-guarded structured changeset",
        description=(
            "Dry-run by default. Use --write only after inspecting the generated receipt."
        ),
    )
    apply_command.add_argument("changeset", help="path to simplicio.fast.changeset/v2 JSON")
    apply_command.add_argument("--root", default=".", help="repository root (default: .)")
    apply_command.add_argument(
        "--write", action="store_true", help="atomically replace validated source files"
    )

    doctor = commands.add_parser(
        "doctor",
        help="validate installation and snapshot integrity",
        description="Check Python, snapshot structure and query readiness; emits JSON.",
    )
    snapshot_argument(doctor)
    json_option(doctor)

    rollout = commands.add_parser(
        "rollout",
        help="record an atomic shadow/canary/integrated rollout receipt",
    )
    rollout.add_argument(
        "mode",
        choices=("shadow", "canary", "integrated", "fallback", "rollback"),
    )
    rollout.add_argument("--state", default=".simplicio-fast/rollout.json")
    rollout.add_argument("--generation")
    rollout.add_argument("--reason")

    server = commands.add_parser("serve", help="run the user CRUD proof-of-concept API")
    server.add_argument("--port", type=int, default=3000)

    base = commands.add_parser("base", help="build an immutable canonical base generation")
    base.add_argument("root", nargs="?", default=".")
    base.add_argument("--storage", default=None, help="generation storage directory")

    overlay = commands.add_parser("overlay", help="build an isolated worktree overlay")
    overlay.add_argument("root", nargs="?", default=".")
    overlay.add_argument("--storage", default=None)
    overlay.add_argument("--base-generation", required=True)
    overlay.add_argument("--worktree-id", required=True)

    merge = commands.add_parser("merge", help="query a composed base plus overlay view")
    merge.add_argument("term", nargs="?", default="")
    merge.add_argument("--root", default=".")
    merge.add_argument("--storage", default=None)
    merge.add_argument("--base-generation", required=True)
    merge.add_argument("--worktree-id")
    merge.add_argument("--overlay-generation")
    merge.add_argument("--max-results", type=int, default=50)

    capabilities = commands.add_parser("capabilities", help="report parser capability negotiation")

    pin = commands.add_parser("pin", help="acquire a lease protecting a generation from GC")
    pin.add_argument("generation")
    pin.add_argument("--root", default=".")
    pin.add_argument("--storage", default=None)
    pin.add_argument("--owner", required=True)
    pin.add_argument("--ttl", type=float, default=3600)

    release = commands.add_parser("release", help="release a generation lease")
    release.add_argument("lease_id")
    release.add_argument("--root", default=".")
    release.add_argument("--storage", default=None)

    gc = commands.add_parser("gc", help="list or remove unleased generations")
    gc.add_argument("--root", default=".")
    gc.add_argument("--storage", default=None)
    gc.add_argument("--apply", action="store_true")

    watch = commands.add_parser("watch", help="refresh an overlay once after source changes")
    watch.add_argument("root", nargs="?", default=".")
    watch.add_argument("--storage", default=None)
    watch.add_argument("--base-generation", required=True)
    watch.add_argument("--worktree-id", required=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command in {"build", "refresh", "ingest"}:
            processor = ProjectProcessor(Path(args.root), Path(args.output))
            if args.command == "ingest":
                emit(processor.ingest())
                return
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
                        "snapshot_version": snapshot.format_version,
                        "matches": [asdict(item) for item in snapshot.find(args.term)[: args.limit]],
                    }
                )
        elif args.command == "search":
            if args.limit < 1:
                parser.error("--limit must be positive")
            with Snapshot(Path(args.snapshot)) as snapshot:
                matches = snapshot.search(args.term, prefix=args.prefix, path=args.path, kind=args.kind)
                emit(
                    {
                        "schema": "simplicio.fast.search/v1",
                        "snapshot_version": snapshot.format_version,
                        "filters": {"prefix": args.prefix, "path": args.path, "kind": args.kind},
                        "matches": [asdict(item) for item in matches[: args.limit]],
                    }
                )
        elif args.command in {"understand", "plan"}:
            processor = ProjectProcessor(Path(args.root), Path(args.snapshot))
            if args.command == "understand":
                emit(asdict(processor.understand(args.task, max_bytes=args.max_bytes)))
            else:
                emit(processor.plan(args.task, max_bytes=args.max_bytes))
        elif args.command == "apply":
            processor = ProjectProcessor(Path(args.root), Path(DEFAULT_SNAPSHOT))
            emit(
                processor.apply_changeset(
                    load_changeset(Path(args.changeset)), write=args.write
                )
            )
        elif args.command == "context":
            if min(args.max_results, args.max_lines, args.max_bytes, args.max_tokens) < 1:
                parser.error("context limits must be positive")
            root = Path(args.root).resolve()
            snapshot_path = Path(args.snapshot).resolve()
            limits = {
                "max_results": args.max_results,
                "max_lines": args.max_lines,
                "max_bytes": args.max_bytes,
                "max_tokens": args.max_tokens,
            }
            with Snapshot(snapshot_path) as snapshot:
                spans = snapshot.context(
                    root,
                    args.term,
                    max_results=args.max_results,
                    max_lines=args.max_lines,
                    max_bytes=args.max_bytes,
                    max_tokens=args.max_tokens,
                )
                commit, commit_reason = source_commit(root)
                emit(
                    {
                        "schema": "simplicio.fast.context/v1",
                        "snapshot_version": snapshot.format_version,
                        "limits": limits,
                        "provenance": {
                            "schema": "simplicio.fast.provenance/v1",
                            "repository_root": str(root),
                            "source_commit": commit,
                            "source_commit_reason": commit_reason,
                            "snapshot_path": str(snapshot_path),
                            "snapshot_sha256": snapshot.sha256,
                            "snapshot_generation": snapshot.generation,
                            "span_count": len(spans),
                            "limits": {
                                "max_results": args.max_results,
                                "max_lines": args.max_lines,
                                "max_bytes": args.max_bytes,
                                "max_tokens": args.max_tokens,
                            },
                        },
                        "spans": [asdict(item) for item in spans],
                    }
                )
        elif args.command == "impact":
            if args.limit < 1:
                parser.error("--limit must be positive")
            with Snapshot(Path(args.snapshot)) as snapshot:
                emit(
                    {
                        "schema": "simplicio.fast.impact/v1",
                        "snapshot_version": snapshot.format_version,
                        "query": args.term,
                        "relations": [asdict(item) for item in snapshot.impact(args.term)[: args.limit]],
                    }
                )
        elif args.command == "stats":
            with Snapshot(Path(args.snapshot)) as snapshot:
                emit({"schema": "simplicio.fast.stats/v1", "stats": snapshot.stats()})
        elif args.command == "doctor":
            path = Path(args.snapshot)
            from .integrations import integration_status

            integration = integration_status()
            checks: list[dict[str, object]] = [
                {"name": "python", "status": "pass", "detail": sys.version.split()[0]},
                {
                    "name": "snapshot_exists",
                    "status": "pass" if path.is_file() else "fail",
                    "detail": str(path),
                },
            ]
            if path.is_file():
                try:
                    with Snapshot(path) as snapshot:
                        checks.append(
                            {
                                "name": "snapshot_integrity",
                                "status": "pass",
                                "detail": snapshot.stats(),
                            }
                        )
                except (OSError, ValueError) as error:
                    checks.append(
                        {
                            "name": "snapshot_integrity",
                            "status": "fail",
                            "detail": {"error": type(error).__name__, "message": str(error)},
                        }
                    )
            snapshot_ready = all(check["status"] == "pass" for check in checks)
            integrated_ready = snapshot_ready and bool(integration["integrated_ready"])
            emit(
                {
                    "schema": "simplicio.fast.doctor/v1",
                    "ready": integrated_ready,
                    "integrated_ready": integrated_ready,
                    "integration": integration,
                    "checks": checks,
                }
            )
            if not integrated_ready:
                raise SystemExit(1)
        elif args.command == "rollout":
            emit(
                RolloutController(Path(args.state)).transition(
                    args.mode, generation=args.generation, reason=args.reason
                )
            )
        elif args.command == "serve":
            service = UserService(JsonUserRepository(Path("data/users.json")))
            print(f"simplicio-fast listening on http://127.0.0.1:{args.port}")
            serve(service, port=args.port)
        elif args.command == "base":
            manifest = WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).build_base()
            emit({"schema": MANIFEST_SCHEMA, "manifest": manifest.to_dict()})
        elif args.command == "overlay":
            overlay_value = WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).create_overlay(
                args.worktree_id, args.base_generation
            )
            emit({"schema": OVERLAY_SCHEMA, "overlay": asdict(overlay_value)})
        elif args.command == "merge":
            if args.worktree_id and not args.overlay_generation:
                raise ValueError("--overlay-generation is required with --worktree-id")
            with WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).open(
                args.base_generation, worktree_id=args.worktree_id, overlay_generation=args.overlay_generation
            ) as view:
                matches = view.find(args.term)[: args.max_results] if args.term else view.symbols()[: args.max_results]
                emit({
                    "schema": "simplicio.fast.merge/v1",
                    "base_generation": view.base_generation,
                    "overlay_generation": view.overlay_generation,
                    "matches": [asdict(item) for item in matches],
                })
        elif args.command == "capabilities":
            emit({"schema": "simplicio.fast.capabilities/v1", "capabilities": [asdict(item) for item in capability_report()]})
        elif args.command == "pin":
            lease = WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).pin(args.generation, args.owner, args.ttl)
            emit({"schema": "simplicio.fast.lease/v1", "lease": asdict(lease)})
        elif args.command == "release":
            WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).release_lease(args.lease_id)
            emit({"schema": "simplicio.fast.lease/v1", "released": args.lease_id})
        elif args.command == "gc":
            emit(WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None).gc(apply=args.apply))
        elif args.command == "watch":
            store = WorkspaceStore(Path(args.root), Path(args.storage) if args.storage else None)
            overlay_value, _ = store.watch_once(args.worktree_id, args.base_generation)
            emit({"schema": "simplicio.fast.watch/v1", "changed": overlay_value is not None,
                  "overlay": asdict(overlay_value) if overlay_value else None})
    except (FileNotFoundError, RuntimeError, ValueError, StaleSnapshotError) as error:
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
