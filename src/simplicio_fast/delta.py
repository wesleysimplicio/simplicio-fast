"""Changed-path delta handoff bound to a canonical Fast snapshot."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable

from .adapters import parse_path
from .snapshot import Snapshot, source_files
from .workspace import (
    MANIFEST_SCHEMA,
    OVERLAY_SCHEMA,
    EffectiveSnapshot,
    GenerationId,
    Overlay,
    WorkspaceStore,
    _atomic_json,
    _canonical_json,
    _hash_source,
)

DELTA_SCHEMA = "simplicio.fast.delta/v1"
HANDOFF_SCHEMA = "simplicio.fast.handoff/v1"


class DeltaError(ValueError):
    """A delta cannot be safely composed with its canonical base."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True, slots=True)
class Delta:
    schema: str
    delta_generation: str
    base_generation: str
    base_commit: str
    base_config_fingerprint: str
    base_schema: str
    base_snapshot_sha256: str
    worktree_id: str
    changed: dict[str, dict[str, object]]
    created_at: str
    delta_sha256: str

    def __post_init__(self) -> None:
        try:
            GenerationId(self.delta_generation)
            GenerationId(self.base_generation)
        except ValueError as error:
            raise DeltaError("delta_generation_invalid") from error
        if self.schema != DELTA_SCHEMA:
            raise DeltaError("delta_schema_mismatch")
        if self.base_schema != MANIFEST_SCHEMA:
            raise DeltaError("base_schema_mismatch")
        for field in (self.base_snapshot_sha256, self.delta_sha256):
            if not _is_digest(field):
                raise DeltaError("delta_digest_invalid")
        for path, record in self.changed.items():
            _validate_record(path, record)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Delta":
        if not isinstance(value, dict):
            raise DeltaError("delta_shape_invalid")
        if value.get("schema") != DELTA_SCHEMA:
            raise DeltaError("delta_schema_mismatch")
        required = (
            "delta_generation", "base_generation", "base_commit",
            "base_config_fingerprint", "base_schema", "base_snapshot_sha256",
            "worktree_id", "created_at", "delta_sha256",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise DeltaError("delta_field_missing", missing[0])
        if any(not isinstance(value[key], str) for key in required):
            raise DeltaError("delta_field_invalid")
        changed = value.get("changed")
        if not isinstance(changed, dict):
            raise DeltaError("delta_shape_invalid")
        normalized_changed: dict[str, dict[str, object]] = {}
        for path, record in changed.items():
            if not isinstance(path, str) or not isinstance(record, dict):
                raise DeltaError("delta_record_invalid")
            normalized = _normal_path(path)
            if normalized != path:
                raise DeltaError("delta_record_invalid")
            _validate_record(path, record)
            normalized_changed[path] = record
        return cls(
            schema=str(value["schema"]),
            delta_generation=str(value["delta_generation"]),
            base_generation=str(value["base_generation"]),
            base_commit=str(value["base_commit"]),
            base_config_fingerprint=str(value["base_config_fingerprint"]),
            base_schema=str(value["base_schema"]),
            base_snapshot_sha256=str(value["base_snapshot_sha256"]),
            worktree_id=str(value["worktree_id"]),
            changed=normalized_changed,
            created_at=str(value["created_at"]),
            delta_sha256=str(value["delta_sha256"]),
        )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _validate_record(path: str, record: object) -> None:
    if not isinstance(record, dict):
        raise DeltaError("delta_record_invalid", path)
    required = {"sha256", "tombstone", "symbols"}
    if not required.issubset(record):
        raise DeltaError("delta_record_invalid", path)
    tombstone = record["tombstone"]
    symbols = record["symbols"]
    digest = record["sha256"]
    if not isinstance(tombstone, bool) or not isinstance(symbols, list):
        raise DeltaError("delta_record_invalid", path)
    if not all(isinstance(symbol, dict) for symbol in symbols):
        raise DeltaError("delta_record_invalid", path)
    if tombstone:
        if digest is not None or symbols:
            raise DeltaError("delta_record_invalid", path)
    elif not _is_digest(digest):
        raise DeltaError("delta_record_invalid", path)


def _normal_path(value: str) -> str:
    candidate = str(value).replace("\\", "/")
    if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
        raise DeltaError("delta_path_invalid", f"invalid changed path: {value}")
    candidate = candidate.strip("/")
    if not candidate or candidate in {".", ".."} or candidate.startswith("../") or "/../" in candidate:
        raise DeltaError("delta_path_invalid", f"invalid changed path: {value}")
    if candidate.startswith(".simplicio/") or candidate == ".simplicio":
        raise DeltaError("delta_path_derived", f"derived path is not a source delta: {value}")
    return candidate


def _base_snapshot(store: WorkspaceStore, base_generation: str) -> tuple[object, Path, str]:
    base = store.manifest(base_generation)
    if base.schema != MANIFEST_SCHEMA:
        raise DeltaError("base_schema_mismatch")
    if not base.root or Path(base.root).resolve() != store.root:
        raise DeltaError("base_root_mismatch")
    if not _is_digest(base.snapshot_sha256) or not _is_digest(base.source_tree_sha256):
        raise DeltaError("base_artifact_digest_missing")
    if _digest(base.source_hashes) != base.source_tree_sha256:
        raise DeltaError("base_source_tree_digest_mismatch")
    directory = (store.base_dir / base_generation).resolve()
    relative_snapshot = Path(base.snapshot)
    if relative_snapshot.is_absolute() or ".." in relative_snapshot.parts:
        raise DeltaError("base_snapshot_path_invalid")
    snapshot_path = (directory / relative_snapshot).resolve()
    if not snapshot_path.is_relative_to(directory):
        raise DeltaError("base_snapshot_path_invalid")
    if not snapshot_path.is_file():
        raise DeltaError("base_artifact_missing")
    actual = _hash_source(snapshot_path)
    if actual != base.snapshot_sha256:
        raise DeltaError("base_artifact_digest_mismatch")
    return base, snapshot_path, actual


def _delta_identity(delta: Delta) -> dict[str, object]:
    return {
        "schema": DELTA_SCHEMA,
        "base_generation": delta.base_generation,
        "base_commit": delta.base_commit,
        "base_config_fingerprint": delta.base_config_fingerprint,
        "base_schema": delta.base_schema,
        "base_snapshot_sha256": delta.base_snapshot_sha256,
        "worktree_id": delta.worktree_id,
        "changed": delta.changed,
    }


def _verify_delta(delta: Delta) -> None:
    digest = _digest(_delta_identity(delta))
    if digest != delta.delta_generation or digest != delta.delta_sha256:
        raise DeltaError("delta_digest_mismatch")


def create_delta(
    store: WorkspaceStore,
    base_generation: str,
    worktree_id: str,
    changed_paths: Iterable[str] | None = None,
    *,
    config_fingerprint: str | None = None,
) -> Delta:
    store._worktree_id(worktree_id)
    base, _, snapshot_sha256 = _base_snapshot(store, base_generation)
    if config_fingerprint is not None and config_fingerprint != base.config_fingerprint:
        raise DeltaError("config_fingerprint_mismatch")
    current_paths = {
        path.relative_to(store.root).as_posix(): path for path in source_files(store.root)
    }
    requested = None if changed_paths is None else sorted({_normal_path(path) for path in changed_paths})
    if requested is not None:
        missing = [path for path in requested if path not in current_paths and path not in base.source_hashes]
        if missing:
            raise DeltaError("delta_path_missing", missing[0])
    candidates = sorted(set(current_paths) | set(base.source_hashes) if requested is None else set(requested))
    changed: dict[str, dict[str, object]] = {}
    for relative in candidates:
        path = current_paths.get(relative)
        if path is None:
            if relative in base.source_hashes:
                changed[relative] = {"sha256": None, "tombstone": True, "symbols": []}
            continue
        digest = _hash_source(path)
        if base.source_hashes.get(relative) == digest:
            continue
        changed[relative] = {
            "sha256": digest,
            "tombstone": False,
            "symbols": [asdict(symbol) for symbol in parse_path(path, relative)],
        }
    identity = {
        "schema": DELTA_SCHEMA,
        "base_generation": base_generation,
        "base_commit": base.commit,
        "base_config_fingerprint": base.config_fingerprint,
        "base_schema": base.schema,
        "base_snapshot_sha256": snapshot_sha256,
        "worktree_id": worktree_id,
        "changed": changed,
    }
    generation = _digest(identity)
    delta = Delta(
        DELTA_SCHEMA, generation, base_generation, base.commit, base.config_fingerprint,
        base.schema, snapshot_sha256, worktree_id, changed,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), generation,
    )
    _atomic_json(store.delta_dir / worktree_id / f"{generation}.json", delta.to_dict())
    store._receipt("delta", {
        "base_generation": base_generation, "delta_generation": generation,
        "worktree_id": worktree_id, "changed_files": sorted(changed),
    })
    return delta


def load_delta(store: WorkspaceStore, worktree_id: str, generation: str) -> Delta:
    store._worktree_id(worktree_id)
    GenerationId(generation)
    path = store.delta_dir / worktree_id / f"{generation}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeltaError("delta_missing") from error
    delta = Delta.from_dict(value)
    if delta.delta_generation != generation or delta.worktree_id != worktree_id:
        raise DeltaError("delta_identity_mismatch")
    _verify_delta(delta)
    return delta


def compose_delta(
    store: WorkspaceStore,
    base_generation: str,
    worktree_id: str,
    delta_generation: str,
    *,
    config_fingerprint: str | None = None,
) -> EffectiveSnapshot:
    base, snapshot_path, snapshot_sha256 = _base_snapshot(store, base_generation)
    delta = load_delta(store, worktree_id, delta_generation)
    if delta.base_generation != base_generation:
        raise DeltaError("base_generation_mismatch")
    if delta.base_commit != base.commit:
        raise DeltaError("base_commit_mismatch")
    if delta.base_config_fingerprint != base.config_fingerprint:
        raise DeltaError("config_fingerprint_mismatch")
    if config_fingerprint is not None and config_fingerprint != base.config_fingerprint:
        raise DeltaError("config_fingerprint_mismatch")
    if delta.base_schema != base.schema or delta.base_snapshot_sha256 != snapshot_sha256:
        raise DeltaError("base_artifact_digest_mismatch")
    current_hashes = {
        path.relative_to(store.root).as_posix(): _hash_source(path) for path in source_files(store.root)
    }
    expected_hashes = _composed_source_hashes(store, base, delta)
    for relative in sorted(set(current_hashes) | set(expected_hashes)):
        if current_hashes.get(relative) == expected_hashes.get(relative):
            continue
        if relative not in delta.changed:
            raise DeltaError("delta_source_unlisted", relative)
        raise DeltaError("delta_source_stale", relative)
    overlay = Overlay(
        OVERLAY_SCHEMA, delta.delta_generation, base_generation, worktree_id,
        delta.changed, delta.created_at,
    )
    return EffectiveSnapshot(store.root, snapshot_path, base, overlay)


def _composed_source_hashes(store: WorkspaceStore, base, delta: Delta) -> dict[str, str]:
    hashes = dict(base.source_hashes)
    for relative, record in delta.changed.items():
        if bool(record.get("tombstone")):
            hashes.pop(relative, None)
        else:
            hashes[relative] = str(record["sha256"])
    return hashes


def handoff(
    store: WorkspaceStore,
    base_generation: str,
    worktree_id: str,
    changed_paths: Iterable[str] | None = None,
    *,
    delta_generation: str | None = None,
    config_fingerprint: str | None = None,
    parity_snapshot: Path | None = None,
) -> dict[str, object]:
    cold_start = perf_counter()
    base, snapshot_path, snapshot_sha256 = _base_snapshot(store, base_generation)
    with Snapshot(snapshot_path) as canonical:
        canonical_files = canonical.files()
    cold_ms = (perf_counter() - cold_start) * 1000
    incremental_start = perf_counter()
    delta = load_delta(store, worktree_id, delta_generation) if delta_generation else create_delta(
        store, base_generation, worktree_id, changed_paths, config_fingerprint=config_fingerprint,
    )
    incremental_ms = (perf_counter() - incremental_start) * 1000
    warm_start = perf_counter()
    with compose_delta(store, base_generation, worktree_id, delta.delta_generation, config_fingerprint=config_fingerprint) as composed:
        composed_symbols = len(composed.symbols())
    warm_ms = (perf_counter() - warm_start) * 1000
    merged = _composed_source_hashes(store, base, delta)
    current = {
        path.relative_to(store.root).as_posix(): _hash_source(path) for path in source_files(store.root)
    }
    target = current
    parity_snapshot_hash = None
    if parity_snapshot is not None:
        with Snapshot(Path(parity_snapshot)) as candidate:
            target = {path: digest.hex() for path, digest in candidate.files()}
            parity_snapshot_hash = candidate.sha256
    parity = merged == target
    return {
        "schema": HANDOFF_SCHEMA,
        "status": "pass" if parity else "parity_mismatch",
        "base_generation": base_generation,
        "delta_generation": delta.delta_generation,
        "base_commit": base.commit,
        "config_fingerprint": base.config_fingerprint,
        "snapshot_hash": snapshot_sha256,
        "snapshot_sha256": snapshot_sha256,
        "delta_sha256": delta.delta_sha256,
        "parity_snapshot_hash": parity_snapshot_hash,
        "parity": parity,
        "parity_result": {
            "source_tree_sha256": _digest(merged), "target_tree_sha256": _digest(target),
            "canonical_file_count": len(canonical_files), "composed_symbol_count": composed_symbols,
        },
        "changed_paths": sorted(delta.changed),
        "files_parsed": sum(1 for record in delta.changed.values() if not bool(record.get("tombstone"))),
        "cache_reuse": len(set(base.source_hashes) - set(delta.changed)),
        "timings_ms": {"cold_ms": round(cold_ms, 3), "warm_ms": round(warm_ms, 3), "incremental_ms": round(incremental_ms, 3)},
    }