"""Deterministic delivery-engine preparation and cache receipts.

This layer coordinates bounded semantic preparation. It does not make cognitive
decisions or write source files: Dev CLI owns mutation and Runtime owns Full
effects. The receipt is the handoff contract for Loop standalone and Full.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import __version__
from .processor import ProjectProcessor
from .snapshot import Snapshot, build_snapshot


SCHEMA = "simplicio.fast.delivery-engine/v1"
PROFILE_NAMES = {"full": "Full", "loop-standalone": "Loop standalone"}


def _source_commit(root: Path) -> tuple[str | None, str | None]:
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
    return (commit, None) if result.returncode == 0 and commit else (None, "not_a_git_checkout")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _terms(task: str) -> list[str]:
    values = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task)
    return list(dict.fromkeys(values))[:8]


class DeliveryEngine:
    def __init__(self, root: Path, snapshot: Path, cache: Path | None = None) -> None:
        self.root = root.resolve()
        self.snapshot = snapshot.resolve()
        self.cache = (cache or self.root / ".simplicio-fast" / "delivery-cache").resolve()

    def prepare(self, task: str, *, profile: str, engine_receipt: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter_ns()
        if profile not in PROFILE_NAMES:
            raise ValueError(f"unsupported delivery profile: {profile}")
        if not self.snapshot.is_file():
            build_snapshot(self.root, self.snapshot)
        commit, commit_reason = _source_commit(self.root)
        with Snapshot(self.snapshot) as snapshot:
            key_material = {
                "task": task,
                "profile": PROFILE_NAMES[profile],
                "engine": engine_receipt,
                "source_commit": commit,
                "snapshot_generation": snapshot.generation,
            }
            cache_key = hashlib.sha256(
                json.dumps(key_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            cache_path = self.cache / f"{cache_key}.json"
            if cache_path.is_file():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached["cache"] = {"L0_attempt": "hit", "hits": 1, "misses": 0, "key": cache_key}
                cached["timings"]["prepare_wall_ms"] = (time.perf_counter_ns() - started) / 1_000_000
                return cached

            terms = _terms(task)
            spans = []
            for term in terms:
                spans.extend(snapshot.context(self.root, term, max_results=2, max_bytes=4_000))
                if len(spans) >= 8:
                    break
            context_bytes = sum(len(span.content.encode("utf-8")) for span in spans)
            context_tokens = sum(max(1, len(span.content.split())) for span in spans) if spans else 0
            receipt: dict[str, Any] = {
                "schema": SCHEMA,
                "status": "ready",
                "task": {"text": task, "sha256": hashlib.sha256(task.encode("utf-8")).hexdigest()},
                "profile": PROFILE_NAMES[profile],
                "engine": engine_receipt,
                "engine_version": __version__,
                "repository": str(self.root),
                "source_commit": commit,
                "source_commit_reason": commit_reason,
                "base_generation": snapshot.generation,
                "overlay_generation": None,
                "mapper": {"schema": "simplicio.mapper-context/v1", "handle": snapshot.generation},
                "budgets": {"context_bytes": 32_000, "context_tokens": 8_000},
                "context": {
                    "terms": terms,
                    "spans": len(spans),
                    "bytes": context_bytes,
                    "estimated_tokens": context_tokens,
                    "digest": hashlib.sha256("\n".join(span.content for span in spans).encode("utf-8")).hexdigest(),
                },
                "cache": {"L0_attempt": "miss", "hits": 0, "misses": 1, "key": cache_key},
                "ownership": {
                    "source_writer": "simplicio-dev-cli",
                    "full_effect_authority": "simplicio-runtime" if profile == "full" else "local-guard",
                    "mutation_applied": False,
                },
                "reason_codes": ["prepared_context_only"],
                "timings": {"prepare_wall_ms": (time.perf_counter_ns() - started) / 1_000_000},
            }
        _atomic_json(cache_path, receipt)
        return receipt

    def deliver(
        self,
        changeset: dict[str, Any],
        *,
        profile: str,
        engine_receipt: dict[str, Any],
        write: bool = False,
        idempotency_key: str | None = None,
        runtime_authorized: bool = False,
    ) -> dict[str, Any]:
        """Execute one guarded changeset and persist a replay-safe delivery receipt.

        Loop standalone may use the local guarded executor. Full write effects are
        fail-closed until a Runtime authorization receipt is supplied. Dry-runs
        remain available in both profiles for inspection and planning.
        """
        started = time.perf_counter_ns()
        if profile not in PROFILE_NAMES:
            raise ValueError(f"unsupported delivery profile: {profile}")
        if changeset.get("schema") != "simplicio.fast.changeset/v2":
            raise ValueError("unsupported changeset schema")
        if not isinstance(changeset.get("changes"), list) or not changeset["changes"]:
            raise ValueError("changeset must contain at least one change")
        if not self.snapshot.is_file():
            build_snapshot(self.root, self.snapshot)
        canonical = json.dumps(changeset, sort_keys=True, separators=(",", ":"))
        change_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        request_key = idempotency_key or hashlib.sha256(
            json.dumps(
                {"changeset": change_digest, "profile": profile, "write": write},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result_path = self.cache / "delivery" / f"{request_key}.json"
        if result_path.is_file():
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            cached["idempotency"] = {
                **cached.get("idempotency", {}),
                "key": request_key,
                "hit": True,
                "replayed": True,
            }
            cached["cache"] = {"L0_delivery": "hit", "key": request_key}
            return cached

        with Snapshot(self.snapshot) as snapshot:
            before_generation = snapshot.generation
        if profile == "full" and write and not runtime_authorized:
            receipt = {
                "schema": SCHEMA,
                "status": "blocked",
                "profile": PROFILE_NAMES[profile],
                "engine": engine_receipt,
                "engine_version": __version__,
                "changeset": {"schema": changeset["schema"], "sha256": change_digest},
                "base_generation": before_generation,
                "idempotency": {"key": request_key, "hit": False, "replayed": False},
                "cache": {"L0_delivery": "miss", "key": request_key},
                "ownership": {
                    "source_writer": "simplicio-dev-cli",
                    "full_effect_authority": "simplicio-runtime",
                    "mutation_applied": False,
                },
                "reason_codes": ["runtime_authorization_required"],
                "timings": {"delivery_wall_ms": (time.perf_counter_ns() - started) / 1_000_000},
            }
            _atomic_json(result_path, receipt)
            return receipt

        applied_receipt = ProjectProcessor(self.root, self.snapshot).apply_changeset(
            changeset, write=write
        )
        applied = bool(applied_receipt.get("applied"))
        after_generation = before_generation
        refresh = {"attempted": False, "status": "not-needed"}
        if write and applied:
            refresh["attempted"] = True
            build_snapshot(self.root, self.snapshot)
            with Snapshot(self.snapshot) as snapshot:
                after_generation = snapshot.generation
            refresh["status"] = "refreshed"
        outcome = "applied" if applied else "dry_run" if not write else "refused"
        reason_codes = []
        if applied_receipt.get("reason_code"):
            reason_codes.append(str(applied_receipt["reason_code"]))
        if not applied:
            reason_codes.append("changeset_not_applied")
        receipt = {
            "schema": SCHEMA,
            "status": outcome,
            "profile": PROFILE_NAMES[profile],
            "engine": engine_receipt,
            "engine_version": __version__,
            "changeset": {
                "schema": changeset["schema"],
                "sha256": change_digest,
                "files": [change["path"] for change in changeset["changes"]],
            },
            "apply": applied_receipt,
            "base_generation": before_generation,
            "result_generation": after_generation,
            "idempotency": {"key": request_key, "hit": False, "replayed": False},
            "cache": {"L0_delivery": "miss", "key": request_key},
            "ownership": {
                "source_writer": "simplicio-dev-cli",
                "full_effect_authority": "simplicio-runtime" if profile == "full" else "local-guard",
                "mutation_applied": applied,
            },
            "refresh": refresh,
            "reason_codes": reason_codes,
            "timings": {"delivery_wall_ms": (time.perf_counter_ns() - started) / 1_000_000},
        }
        _atomic_json(result_path, receipt)
        return receipt
