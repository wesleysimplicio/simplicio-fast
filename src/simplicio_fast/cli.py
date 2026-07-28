import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .processor import ProjectProcessor, load_changeset
from .rollout import RolloutController
from .snapshot import (
    DEFAULT_BUILD_TIMEOUT_SECONDS,
    Snapshot,
    SnapshotBuildTimeout,
    StaleSnapshotError,
    build_snapshot,
)
from .adapters import capability_report
from .workspace import MANIFEST_SCHEMA, OVERLAY_SCHEMA, WorkspaceStore
from .engine import EngineSelection, EngineSelectionError, select_engine
from .delivery import DeliveryEngine
from .query_planner import plan_query
from .navigation import DIRECTIONS, RELATIONS, NavigationBudget, NavigationIndex
from .semantic_scoring import (
    SemanticBudgets,
    SemanticScorer,
    SourceDocument,
    semantic_capabilities,
)
from .users.http import serve
from .users.repository import JsonUserRepository
from .users.service import UserService

DEFAULT_STATE_DIR = ".simplicio/fast"
DEFAULT_SNAPSHOT = f"{DEFAULT_STATE_DIR}/project.sfast"


def emit(value: object) -> None:
    # JSON is a machine-readable CLI contract.  Escape non-ASCII characters so
    # Windows consoles using a legacy code page cannot fail while emitting a
    # valid receipt containing source text or Unicode symbols.
    print(json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True))


def source_commit(root: Path) -> tuple[str | None, str | None]:
    """Return the checked-out commit, or a reason when root is outside Git."""
    if not (root / ".git").exists():
        return None, "not_a_git_checkout"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=False,
            close_fds=True,
        )
    except OSError:
        return None, "git_unavailable"
    commit = result.stdout.strip()
    if result.returncode or not commit:
        return None, "not_a_git_checkout"
    return commit, None


def _rust_bridge(selection: EngineSelection, args: argparse.Namespace) -> dict[str, object] | None:
    """Dispatch read-only snapshot commands to a proven Rust executable.

    Rust is deliberately limited to operations it owns today.  Snapshot
    construction and mutations stay on the Python/Dev CLI paths until their
    contracts are implemented by the native engine.
    """
    if selection.selected != "rust" or args.command not in {"stats", "query", "context"}:
        return None
    executable = selection.executable
    if not executable:
        raise EngineSelectionError(
            {
                "schema": "simplicio.fast.engine-selection/v1",
                "requested": selection.requested,
                "selected": "unavailable",
                "reason": "rust_executable_missing_for_bridge",
                "executable": None,
                "manifest": selection.manifest,
            }
        )
    if args.command == "stats":
        command = [executable, "--stats", str(Path(args.snapshot)), "--json"]
        expected_schema = "simplicio.fast.stats/v1"
    elif args.command == "query":
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        command = [
            executable,
            "--query",
            str(Path(args.snapshot)),
            args.term,
            "--limit",
            str(args.limit),
            "--json",
        ]
        expected_schema = "simplicio.fast.query/v1"
    else:
        if min(args.max_results, args.max_lines, args.max_bytes, args.max_tokens) < 1:
            raise ValueError("context limits must be positive")
        command = [
            executable,
            "--context",
            str(Path(args.snapshot)),
            str(Path(args.root).resolve()),
            args.term,
            "--limit",
            str(args.max_results),
            "--max-lines",
            str(args.max_lines),
            "--max-bytes",
            str(args.max_bytes),
            "--max-tokens",
            str(args.max_tokens),
            "--json",
        ]
        expected_schema = "simplicio.fast.context/v1"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"rust_bridge_failed: {type(error).__name__}") from error
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"rust_bridge_invalid_json: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("rust_bridge_output_not_object")
    if payload.get("schema") != expected_schema:
        reason = payload.get("reason", "schema_mismatch")
        raise RuntimeError(f"rust_bridge_contract_error: {reason}")
    if completed.returncode != 0:
        raise RuntimeError("rust_bridge_command_failed")
    return payload


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
            "Simplicio Fast is semantic project memory and guarded change coordination for AI coding tools.\n\n"
            "It ingests a repository into an incremental binary/mmap snapshot, returns bounded\n"
            "hash-verified context, compiles PlanDAGs, and validates changesets without replacing\n"
            "the source files. Mapper owns canonical extraction; Dev CLI owns mechanical edits;\n"
            "Loop owns convergence; Runtime owns policy and effects."
        ),
        epilog=(
            "Typical flow: build/ingest -> context or understand -> plan -> apply (dry-run first)\n"
            "-> refresh and validate. Use --help on a subcommand for its JSON contract.\n"
            "Never read .sfast offsets directly: use versioned Fast or Mapper handles."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--fast-engine",
        choices=("auto", "rust", "python", "off"),
        default="auto",
        help="select the Fast engine: Rust only after a healthy probe, or Python fallback (default: auto)",
    )
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
        command.add_argument(
            "--timeout",
            type=float,
            default=DEFAULT_BUILD_TIMEOUT_SECONDS,
            help=f"maximum build time in seconds before failing without publishing (default: {DEFAULT_BUILD_TIMEOUT_SECONDS:g})",
        )

        command.add_argument(
            "--max-file-bytes",
            type=int,
            default=8 * 1024 * 1024,
            help="reject a source file larger than this before parsing (default: 8388608)",
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

    navigate_command = commands.add_parser(
        "navigate",
        help="navigate one bounded structural hop from a canonical symbol handle",
        description="Python reference-engine navigation; use --fast-engine python until Rust parity exists.",
    )
    navigate_command.add_argument("handle", help="canonical snapshot symbol ID")
    navigate_command.add_argument("relation", choices=sorted(RELATIONS))
    navigate_command.add_argument("direction", choices=sorted(DIRECTIONS))
    snapshot_argument(navigate_command)
    navigate_command.add_argument("--max-nodes", type=int, default=20)
    navigate_command.add_argument("--max-bytes", type=int, default=8192)
    navigate_command.add_argument("--max-depth", type=int, default=1)
    navigate_command.add_argument("--cursor")
    navigate_command.add_argument("--generation")
    json_option(navigate_command)

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

    query_plan = commands.add_parser(
        "query-plan",
        help="explain the deterministic index and budget plan for a query",
        description="Return QueryIR-style planning evidence without materializing source spans.",
    )
    query_plan.add_argument("term")
    snapshot_argument(query_plan)
    query_plan.add_argument("--operation", choices=("query", "search", "context", "impact"), default="query")
    query_plan.add_argument("--prefix", action="store_true")
    query_plan.add_argument("--path")
    query_plan.add_argument("--kind", choices=("class", "function", "async_function"))
    query_plan.add_argument("--max-results", type=int, default=50)
    query_plan.add_argument("--max-bytes", type=int, default=32_000)
    query_plan.add_argument("--max-tokens", type=int, default=8_000)
    json_option(query_plan)

    segments = commands.add_parser(
        "segments",
        help="publish, validate or map immutable snapshot sections",
        description="Expose the bounded segmented-storage contract without exposing raw snapshot offsets.",
    )
    segments.add_argument("action", choices=("publish", "validate", "map"))
    segments.add_argument("--directory", required=True, help="segmented storage directory")
    segments.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="source SFAST snapshot for publish")
    segments.add_argument("--name", help="segment name for map")
    json_option(segments)

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

    delivery = commands.add_parser(
        "delivery",
        help="prepare or execute guarded delivery and emit a cache/provenance receipt",
    )
    delivery.add_argument("task", help="task or issue text")
    delivery.add_argument("--root", default=".", help="repository root (default: .)")
    snapshot_argument(delivery)
    delivery.add_argument("--cache", default=None, help="delivery cache directory")
    delivery.add_argument("--profile", choices=("full", "loop-standalone"), default="loop-standalone")
    delivery.add_argument("--max-bytes", type=int, default=32_000)
    delivery.add_argument("--changeset", default=None, help="optional simplicio.fast.changeset/v2 JSON")
    delivery.add_argument("--write", action="store_true", help="apply a validated changeset; dry-run is the default")
    delivery.add_argument("--idempotency-key", default=None, help="stable delivery replay key")
    delivery.add_argument(
        "--runtime-transaction",
        default=None,
        help="coordinator-issued simplicio.effect-transaction/v1 JSON for Full writes",
    )

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
    doctor.add_argument("--installation", action="store_true", help="report local Python/Rust artifacts without downloading")
    doctor.add_argument("--smoke", action="store_true", help="run a disposable installed Python CLI smoke flow")
    json_option(doctor)

    rollout = commands.add_parser(
        "rollout",
        help="record an atomic shadow/canary/integrated rollout receipt",
    )
    rollout.add_argument(
        "mode",
        choices=("shadow", "canary", "integrated", "fallback", "rollback"),
    )
    rollout.add_argument("--state", default=f"{DEFAULT_STATE_DIR}/rollout.json")
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

    semantic = commands.add_parser(
        "semantic-score",
        help="rank bounded candidates with optional Runtime inference and deterministic fallback",
        description=(
            "Read canonical candidate handles/text from JSON and emit semantic-score/v1 rows. "
            "The CLI offline path never downloads a model and remains fully deterministic."
        ),
    )
    semantic.add_argument("query")
    semantic.add_argument("--generation", required=True)
    semantic.add_argument("--candidates", required=True, help="JSON list of canonical_id/text records")
    semantic.add_argument("--max-candidates", type=int, default=128)
    semantic.add_argument("--max-results", type=int, default=10)
    semantic.add_argument("--max-request-bytes", type=int, default=256_000)
    semantic.add_argument("--max-tokens", type=int, default=8_000)
    json_option(semantic)

    commands.add_parser("capabilities", help="report parser capability negotiation")

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

    # Accept the selector both before and after the subcommand.  Suppressing
    # the subparser default preserves an explicit top-level value.
    for command in commands.choices.values():
        command.add_argument(
            "--fast-engine",
            dest="fast_engine",
            choices=("auto", "rust", "python", "off"),
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        selection = select_engine(args.fast_engine)
        bridged = _rust_bridge(selection, args)
        if bridged is not None:
            emit(bridged)
            return
        if args.command in {"build", "refresh", "ingest"}:
            processor = ProjectProcessor(Path(args.root), Path(args.output))
            if args.command == "ingest":
                emit(
                    processor.ingest(
                        timeout_seconds=args.timeout,
                        max_file_bytes=args.max_file_bytes,
                    )
                )
                return
            emit(
                {
                    "schema": "simplicio.fast.build/v1",
                    "version": __version__,
                    "snapshot": str(Path(args.output)),
                    "metrics": asdict(
                        build_snapshot(
                            Path(args.root),
                            Path(args.output),
                            timeout_seconds=args.timeout,
                            max_file_bytes=args.max_file_bytes,
                        )
                    ),
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
        elif args.command == "delivery":
            delivery_engine = DeliveryEngine(
                Path(args.root),
                Path(args.snapshot),
                Path(args.cache) if args.cache else None,
            )
            if args.changeset:
                runtime_transaction = None
                if args.runtime_transaction:
                    runtime_transaction = json.loads(
                        Path(args.runtime_transaction).read_text(encoding="utf-8")
                    )
                    if not isinstance(runtime_transaction, dict):
                        raise ValueError("--runtime-transaction must contain a JSON object")
                emit(
                    delivery_engine.deliver(
                        load_changeset(Path(args.changeset)),
                        profile=args.profile,
                        engine_receipt=selection.receipt(),
                        write=args.write,
                        idempotency_key=args.idempotency_key,
                        runtime_transaction=runtime_transaction,
                    )
                )
            else:
                emit(
                    delivery_engine.prepare(
                        args.task, profile=args.profile, engine_receipt=selection.receipt()
                    )
                )
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
        elif args.command == "navigate":
            if args.fast_engine != "python":
                raise RuntimeError("navigate_requires_explicit_python_engine")
            budget = NavigationBudget(
                max_nodes=args.max_nodes, max_bytes=args.max_bytes, max_depth=args.max_depth
            )
            with Snapshot(Path(args.snapshot)) as snapshot:
                page = NavigationIndex(snapshot).navigate(
                    args.handle,
                    args.relation,
                    args.direction,
                    budget,
                    cursor=args.cursor,
                    generation=args.generation,
                )
                emit(page.to_dict())
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
        elif args.command == "query-plan":
            with Snapshot(Path(args.snapshot)) as snapshot:
                emit(
                    plan_query(
                        snapshot,
                        args.term,
                        operation=args.operation,
                        prefix=args.prefix,
                        path=args.path,
                        kind=args.kind,
                        max_results=args.max_results,
                        max_bytes=args.max_bytes,
                        max_tokens=args.max_tokens,
                    ).to_dict()
                )
        elif args.command == "segments":
            from .segments import SegmentStore

            store = SegmentStore(Path(args.directory))
            if args.action == "publish":
                emit(store.publish(Path(args.snapshot)))
            elif args.action == "validate":
                emit(store.validate())
            else:
                if not args.name:
                    raise ValueError("--name is required for segments map")
                with store.map(args.name) as mapped:
                    emit(
                        {
                            "schema": "simplicio.fast.segment-map/v1",
                            "name": args.name,
                            "bytes": len(mapped),
                            "sha256": hashlib.sha256(bytes(mapped)).hexdigest(),
                        }
                    )
        elif args.command == "doctor":
            if args.installation:
                from .installation import python_smoke, report

                payload = report()
                if args.smoke:
                    payload["python_smoke"] = python_smoke()
                emit(payload)
                return
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
                            "detail": {
                                "error": type(error).__name__,
                                "message": str(error),
                                "recovery_code": "snapshot_corrupt_rebuild",
                                "remediation": (
                                    "run simplicio-fast refresh . --json; source files remain "
                                    "authoritative and the replacement is validated before publication"
                                ),
                                "snapshot": str(path),
                            },
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
        elif args.command == "semantic-score":
            raw_candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
            if not isinstance(raw_candidates, list):
                raise ValueError("--candidates must contain a JSON list")
            candidates = []
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    raise ValueError("each semantic candidate must be an object")
                text = raw.get("text")
                canonical_id = raw.get("canonical_id")
                if not isinstance(text, str) or not isinstance(canonical_id, str):
                    raise ValueError("semantic candidates require canonical_id and text")
                if raw.get("source_sha256") is None:
                    candidates.append(
                        SourceDocument.create(
                            canonical_id,
                            text,
                            structural_score=float(raw.get("structural_score", 0.0)),
                        )
                    )
                else:
                    candidates.append(
                        SourceDocument(
                            canonical_id,
                            text,
                            raw["source_sha256"],
                            float(raw.get("structural_score", 0.0)),
                        )
                    )
            budgets = SemanticBudgets(
                max_candidates=args.max_candidates,
                max_selected=args.max_results,
                max_request_bytes=args.max_request_bytes,
                max_selected_tokens=args.max_tokens,
            )
            emit(
                SemanticScorer(budgets=budgets).score(
                    generation=args.generation,
                    query=args.query,
                    candidates=tuple(candidates),
                )
            )
        elif args.command == "capabilities":
            emit({
                "schema": "simplicio.fast.capabilities/v1",
                "engine": selection.receipt(),
                "engine_manifest": selection.manifest,
                "capabilities": [asdict(item) for item in capability_report()],
                "semantic_scoring": semantic_capabilities(),
            })
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
    except EngineSelectionError as error:
        emit(error.receipt)
        raise SystemExit(2) from error
    except (FileNotFoundError, RuntimeError, ValueError, SnapshotBuildTimeout, StaleSnapshotError) as error:
        payload = {
            "schema": "simplicio.fast.error/v1",
            "error": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, SnapshotBuildTimeout):
            payload.update(
                {
                    "recovery_code": error.code,
                    "recovery": error.recovery,
                    "progress": error.progress,
                }
            )
        emit(payload)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
