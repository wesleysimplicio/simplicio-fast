"""Canonical base generations and isolated worktree overlays.

This module is a small, dependency-free coordination layer around the existing
read-only mmap snapshot.  It stores only versioned manifests and semantic delta
records; consumers never need to interpret `.sfast` offsets.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from .delta import Delta

from .adapters import capability_report, parse_path
from .snapshot import (
    ContextSpan,
    Snapshot,
    StaleSnapshotError,
    Symbol,
    build_snapshot,
    source_files,
)

MANIFEST_SCHEMA = "simplicio.fast.manifest/v1"
OVERLAY_SCHEMA = "simplicio.fast.overlay/v1"
LEASE_SCHEMA = "simplicio.fast.lease/v1"
RECEIPT_SCHEMA = "simplicio.fast.receipt/v1"
DELTA_STORAGE = "deltas"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


@dataclass(frozen=True, slots=True)
class GenerationId:
    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 64 or any(
            char not in "0123456789abcdef" for char in self.value
        ):
            raise ValueError("GenerationId must be a lowercase SHA-256 digest")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Manifest:
    schema: str
    generation_id: str
    kind: str
    commit: str
    root: str
    config_fingerprint: str
    parser_versions: dict[str, str]
    source_hashes: dict[str, str]
    snapshot: str
    created_at: str
    snapshot_sha256: str = ""
    source_tree_sha256: str = ""

    def __post_init__(self) -> None:
        GenerationId(self.generation_id)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Manifest":
        if value.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("unsupported manifest schema")
        return cls(
            schema=str(value["schema"]),
            generation_id=str(value["generation_id"]),
            kind=str(value["kind"]),
            commit=str(value["commit"]),
            root=str(value["root"]),
            config_fingerprint=str(value["config_fingerprint"]),
            parser_versions={
                str(k): str(v) for k, v in dict(value["parser_versions"]).items()
            },
            source_hashes={
                str(k): str(v) for k, v in dict(value["source_hashes"]).items()
            },
            snapshot=str(value["snapshot"]),
            created_at=str(value["created_at"]),
            snapshot_sha256=str(value.get("snapshot_sha256", "")),
            source_tree_sha256=str(value.get("source_tree_sha256", "")),
        )


@dataclass(frozen=True, slots=True)
class Lease:
    schema: str
    lease_id: str
    generation_id: str
    owner: str
    expires_at: float
    created_at: float


@dataclass(frozen=True, slots=True)
class Overlay:
    schema: str
    overlay_generation: str
    base_generation: str
    worktree_id: str
    changed: dict[str, dict[str, object]]
    created_at: str

    def __post_init__(self) -> None:
        GenerationId(self.overlay_generation)
        GenerationId(self.base_generation)


def _hash_source(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    """Publish JSON atomically, including under concurrent Windows writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.{os.getpid()}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(_canonical_json(value))
            temporary.flush()
            os.fsync(temporary.fileno())

        temporary_path = Path(temporary_name)
        # Windows can transiently deny replacement while virus scanners or
        # another reader close a handle. Retry only that recoverable condition.
        for attempt in range(8):
            try:
                os.replace(temporary_path, path)
                temporary_name = None
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (2**attempt))
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _git_status(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class WorkspaceStore:
    def __init__(self, root: Path, storage: Path | None = None) -> None:
        self.root = root.resolve()
        self.storage = (storage or self.root / ".simplicio" / "fast").resolve()
        self.base_dir = self.storage / "base"
        self.overlay_dir = self.storage / "overlays"
        self.lease_dir = self.storage / "leases"
        self.receipt_dir = self.storage / "receipts"
        self.delta_dir = self.storage / DELTA_STORAGE
        self._validated_base_cache: dict[
            tuple[str, str, int, int, int, int], tuple[Manifest, str]
        ] = {}
        self._delta_cache: dict[
            tuple[str, str, str, int, int, int, int], object
        ] = {}
        self._manifest_cache: dict[
            tuple[str, str, int, int, int, int], Manifest
        ] = {}

    def cached_validated_base(
        self, generation: str, base: Manifest, snapshot: Path
    ) -> str | None:
        """Return a digest previously verified for an unchanged artifact identity."""
        try:
            stat = snapshot.stat()
        except OSError:
            return None
        key = (
            generation,
            str(snapshot),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(getattr(stat, "st_ino", 0)),
        )
        cached = self._validated_base_cache.get(key)
        if cached is None or cached[0] != base:
            return None
        return cached[1]

    def remember_validated_base(
        self, generation: str, base: Manifest, snapshot: Path, digest: str
    ) -> None:
        try:
            stat = snapshot.stat()
        except OSError:
            return
        key = (
            generation,
            str(snapshot),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(getattr(stat, "st_ino", 0)),
        )
        self._validated_base_cache[key] = (base, digest)
        while len(self._validated_base_cache) > 8:
            self._validated_base_cache.pop(next(iter(self._validated_base_cache)))

    def cached_delta(self, worktree_id: str, generation: str, path: Path) -> object | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (
            worktree_id,
            generation,
            str(path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(getattr(stat, "st_ino", 0)),
        )
        return self._delta_cache.get(key)

    def remember_delta(
        self, worktree_id: str, generation: str, path: Path, delta: object
    ) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        key = (
            worktree_id,
            generation,
            str(path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(getattr(stat, "st_ino", 0)),
        )
        self._delta_cache[key] = delta
        while len(self._delta_cache) > 16:
            self._delta_cache.pop(next(iter(self._delta_cache)))

    def _manifest_path(self, generation: str) -> Path:
        return self.base_dir / generation / "manifest.json"

    @staticmethod
    def _worktree_id(value: str) -> str:
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ValueError("worktree_id must be a single safe path component")
        return value

    def manifest(self, generation: str) -> Manifest:
        GenerationId(generation)
        path = self._manifest_path(generation)
        try:
            stat = path.stat()
        except OSError:
            stat = None
        key = (
            generation,
            str(path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
            int(getattr(stat, "st_ino", 0)),
        ) if stat is not None else None
        if key is not None:
            cached = self._manifest_cache.get(key)
            if cached is not None:
                return cached
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = Manifest.from_dict(data)
        if manifest.generation_id != generation:
            raise ValueError("manifest generation mismatch")
        if key is not None:
            for cached_key in tuple(self._manifest_cache):
                if cached_key[:2] == key[:2] and cached_key != key:
                    del self._manifest_cache[cached_key]
            self._manifest_cache[key] = manifest
            while len(self._manifest_cache) > 8:
                self._manifest_cache.pop(next(iter(self._manifest_cache)))
        return manifest

    def build_base(self, *, config: dict[str, object] | None = None) -> Manifest:
        config = config or {}
        commit = _commit(self.root)
        if commit != "unknown" and _git_status(self.root):
            raise ValueError("canonical_base_dirty")
        files = source_files(self.root)
        source_hashes = {
            path.relative_to(self.root).as_posix(): _hash_source(path) for path in files
        }
        source_tree_sha256 = hashlib.sha256(_canonical_json(source_hashes)).hexdigest()
        parser_versions = {
            item.language: f"{item.parser}:1" for item in capability_report()
        }
        config_fingerprint = hashlib.sha256(_canonical_json(config)).hexdigest()
        identity = {
            "kind": "base",
            "commit": commit,
            "config_fingerprint": config_fingerprint,
            "parser_versions": parser_versions,
            "source_hashes": source_hashes,
            "source_tree_sha256": source_tree_sha256,
        }
        generation = hashlib.sha256(_canonical_json(identity)).hexdigest()
        directory = self.base_dir / generation
        directory.mkdir(parents=True, exist_ok=True)
        snapshot_path = directory / "project.sfast"
        manifest_path = directory / "manifest.json"
        if snapshot_path.is_file() and manifest_path.is_file():
            existing = self.manifest(generation)
            _atomic_json(self.storage / "current.json", existing.to_dict())
            return existing
        build_snapshot(self.root, snapshot_path)
        snapshot_sha256 = _hash_source(snapshot_path)
        manifest = Manifest(
            MANIFEST_SCHEMA,
            generation,
            "base",
            identity["commit"],
            str(self.root),
            config_fingerprint,
            parser_versions,
            source_hashes,
            "project.sfast",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            snapshot_sha256,
            source_tree_sha256,
        )
        _atomic_json(manifest_path, manifest.to_dict())
        _atomic_json(self.storage / "current.json", manifest.to_dict())
        self._receipt("base", {"generation_id": generation, "source_count": len(files)})
        return manifest

    def create_overlay(self, worktree_id: str, base_generation: str) -> Overlay:
        self._worktree_id(worktree_id)
        manifest = self.manifest(base_generation)
        changed: dict[str, dict[str, object]] = {}
        current_paths = {
            path.relative_to(self.root).as_posix(): path
            for path in source_files(self.root)
        }
        for relative, path in current_paths.items():
            digest = _hash_source(path)
            if manifest.source_hashes.get(relative) == digest:
                continue
            symbols = [asdict(symbol) for symbol in parse_path(path, relative)]
            changed[relative] = {
                "sha256": digest,
                "tombstone": False,
                "symbols": symbols,
            }
        for relative in sorted(set(manifest.source_hashes) - set(current_paths)):
            changed[relative] = {"sha256": None, "tombstone": True, "symbols": []}
        identity = {
            "base_generation": base_generation,
            "worktree_id": worktree_id,
            "changed": changed,
        }
        generation = hashlib.sha256(_canonical_json(identity)).hexdigest()
        overlay = Overlay(
            OVERLAY_SCHEMA,
            generation,
            base_generation,
            worktree_id,
            changed,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        _atomic_json(
            self.overlay_dir / worktree_id / f"{generation}.json", asdict(overlay)
        )
        self._receipt(
            "overlay",
            {
                "base_generation": base_generation,
                "overlay_generation": generation,
                "worktree_id": worktree_id,
                "changed_files": sorted(changed),
            },
        )
        return overlay

    def create_delta(
        self,
        base_generation: str,
        worktree_id: str,
        changed_paths: Iterable[str] | None = None,
        *,
        config_fingerprint: str | None = None,
    ) -> "Delta":
        from .delta import create_delta

        return create_delta(
            self,
            base_generation,
            worktree_id,
            changed_paths,
            config_fingerprint=config_fingerprint,
        )

    def delta(self, worktree_id: str, generation: str) -> "Delta":
        from .delta import load_delta

        return load_delta(self, worktree_id, generation)

    def compose_delta(
        self,
        base_generation: str,
        worktree_id: str,
        delta_generation: str,
        *,
        config_fingerprint: str | None = None,
    ) -> "EffectiveSnapshot":
        from .delta import compose_delta

        return compose_delta(
            self,
            base_generation,
            worktree_id,
            delta_generation,
            config_fingerprint=config_fingerprint,
        )

    def handoff(
        self,
        base_generation: str,
        worktree_id: str,
        changed_paths: Iterable[str] | None = None,
        *,
        delta_generation: str | None = None,
        config_fingerprint: str | None = None,
        parity_snapshot: Path | None = None,
    ) -> dict[str, object]:
        from .delta import handoff

        return handoff(
            self,
            base_generation,
            worktree_id,
            changed_paths,
            delta_generation=delta_generation,
            config_fingerprint=config_fingerprint,
            parity_snapshot=parity_snapshot,
        )

    def overlay(self, worktree_id: str, generation: str) -> Overlay:
        self._worktree_id(worktree_id)
        GenerationId(generation)
        value = json.loads(
            (self.overlay_dir / worktree_id / f"{generation}.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            value.get("schema") != OVERLAY_SCHEMA
            or value.get("overlay_generation") != generation
        ):
            raise ValueError("invalid overlay manifest")
        return Overlay(
            str(value["schema"]),
            generation,
            str(value["base_generation"]),
            str(value["worktree_id"]),
            dict(value["changed"]),
            str(value["created_at"]),
        )

    def open(
        self,
        base_generation: str,
        *,
        worktree_id: str | None = None,
        overlay_generation: str | None = None,
    ) -> "EffectiveSnapshot":
        manifest = self.manifest(base_generation)
        overlay = (
            self.overlay(worktree_id, overlay_generation)
            if worktree_id and overlay_generation
            else None
        )
        if overlay and overlay.base_generation != base_generation:
            raise ValueError("overlay base generation does not match requested base")
        return EffectiveSnapshot(
            self.root,
            self.base_dir / base_generation / manifest.snapshot,
            manifest,
            overlay,
        )

    def acquire_lease(
        self, generation: str, owner: str, ttl_seconds: float = 3600
    ) -> Lease:
        self.manifest(generation)
        now = time.time()
        lease = Lease(
            LEASE_SCHEMA, uuid.uuid4().hex, generation, owner, now + ttl_seconds, now
        )
        _atomic_json(self.lease_dir / f"{lease.lease_id}.json", asdict(lease))
        return lease

    def release_lease(self, lease_id: str) -> None:
        path = self.lease_dir / f"{lease_id}.json"
        if path.exists():
            path.unlink()

    def pin(self, generation: str, owner: str, ttl_seconds: float = 3600) -> Lease:
        return self.acquire_lease(generation, owner, ttl_seconds)

    @contextmanager
    def pinned(
        self, generation: str, owner: str, ttl_seconds: float = 3600
    ) -> Iterator[Lease]:
        lease = self.acquire_lease(generation, owner, ttl_seconds)
        try:
            yield lease
        finally:
            self.release_lease(lease.lease_id)

    def gc(self, *, now: float | None = None, apply: bool = False) -> dict[str, object]:
        now = now or time.time()
        protected: set[str] = set()
        for path in self.lease_dir.glob("*.json"):
            try:
                lease = json.loads(path.read_text(encoding="utf-8"))
                if float(lease.get("expires_at", 0)) > now:
                    protected.add(str(lease["generation_id"]))
                elif apply:
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                if apply:
                    path.unlink(missing_ok=True)
        candidates = [
            path.name
            for path in self.base_dir.iterdir()
            if path.is_dir() and path.name not in protected
        ]
        removed: list[str] = []
        if apply:
            for generation in candidates:
                shutil.rmtree(self.base_dir / generation, ignore_errors=False)
                removed.append(generation)
        result = {
            "schema": RECEIPT_SCHEMA,
            "protected": sorted(protected),
            "candidates": candidates,
            "removed": removed,
            "applied": apply,
        }
        self._receipt("gc", result)
        return result

    def refresh(
        self,
        worktree_id: str,
        base_generation: str,
        overlay_generation: str | None = None,
    ) -> Overlay:
        previous = None
        if overlay_generation is not None:
            previous = self.overlay(worktree_id, overlay_generation)
            if previous.base_generation != base_generation:
                raise ValueError("overlay base generation does not match refresh base")
        refreshed = self.create_overlay(worktree_id, base_generation)
        self._receipt(
            "refresh",
            {
                "base_generation": base_generation,
                "overlay_generation": refreshed.overlay_generation,
                "previous_overlay_generation": previous.overlay_generation
                if previous
                else None,
                "worktree_id": worktree_id,
                "changed_files": sorted(refreshed.changed),
            },
        )
        return refreshed

    def watch_once(
        self,
        worktree_id: str,
        base_generation: str,
        previous: dict[str, str] | None = None,
    ) -> tuple[Overlay | None, dict[str, str]]:
        hashes = {
            path.relative_to(self.root).as_posix(): _hash_source(path)
            for path in source_files(self.root)
        }
        if previous == hashes:
            return None, hashes
        return self.create_overlay(worktree_id, base_generation), hashes

    def _receipt(self, action: str, detail: dict[str, object]) -> None:
        payload = {
            "schema": RECEIPT_SCHEMA,
            "action": action,
            "created_at": time.time(),
            **detail,
        }
        _atomic_json(
            self.receipt_dir
            / f"{int(time.time() * 1000)}-{secrets.token_hex(4)}-{action}.json",
            payload,
        )


class EffectiveSnapshot:
    def __init__(
        self,
        root: Path,
        snapshot_path: Path,
        manifest: Manifest,
        overlay: Overlay | None,
    ) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self.overlay = overlay
        self.base_generation = manifest.generation_id
        self.overlay_generation = overlay.overlay_generation if overlay else None
        self._base = Snapshot(snapshot_path)

    def close(self) -> None:
        self._base.close()

    def __enter__(self) -> "EffectiveSnapshot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _overlay_symbols(self) -> list[Symbol]:
        if not self.overlay:
            return []
        return [
            Symbol(**item)
            for record in self.overlay.changed.values()
            for item in record["symbols"]
        ]

    def symbols(self) -> list[Symbol]:
        replacements = set(self.overlay.changed) if self.overlay else set()
        base = [
            replace(
                symbol,
                base_generation=self.base_generation,
                overlay_generation=self.overlay_generation,
            )
            for symbol in self._base.symbols()
            if symbol.file not in replacements
        ]
        overlay = [
            replace(
                symbol,
                base_generation=self.base_generation,
                overlay_generation=self.overlay_generation,
            )
            for symbol in self._overlay_symbols()
        ]
        return sorted(
            [*base, *overlay],
            key=lambda item: (item.name, item.qualified_name, item.file, item.line),
        )

    def find(self, query: str) -> list[Symbol]:
        needle = query.casefold()
        return [
            item
            for item in self.symbols()
            if needle in item.name.casefold()
            or needle in item.qualified_name.casefold()
        ]

    def context(
        self,
        query: str,
        *,
        max_results: int = 10,
        max_lines: int = 120,
        max_bytes: int = 32_000,
    ) -> list[ContextSpan]:
        if max_results < 1 or max_lines < 1 or max_bytes < 1:
            raise ValueError("context limits must be positive")
        spans: list[ContextSpan] = []
        consumed = 0
        for symbol in self.find(query)[:max_results]:
            path = (self.root / symbol.file).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise ValueError(
                    f"snapshot path escapes root: {symbol.file}"
                ) from error
            actual = _hash_source(path)
            expected = (
                self.overlay.changed.get(symbol.file, {}).get("sha256")
                if self.overlay
                else None
            ) or self.manifest.source_hashes.get(symbol.file)
            if actual != expected:
                raise StaleSnapshotError(
                    f"source changed after generation: {symbol.file}; run refresh"
                )
            lines = path.read_text(encoding="utf-8").splitlines()
            end = min(symbol.end_line, symbol.line + max_lines - 1)
            content = "\n".join(lines[symbol.line - 1 : end])
            remaining = max_bytes - consumed
            if remaining <= 0:
                break
            content = content.encode()[:remaining].decode("utf-8", errors="ignore")
            consumed += len(content.encode())
            spans.append(
                ContextSpan(
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.file,
                    symbol.line,
                    end,
                    actual,
                    content,
                    self.base_generation,
                    self.overlay_generation,
                )
            )
        return spans
