"""Atomic immutable section storage derived from a validated SFAST snapshot."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from .snapshot import Snapshot


MANIFEST_SCHEMA = "simplicio.fast.segments/v1"
MANIFEST_NAME = "manifest.json"
MANIFEST_BACKUP_NAME = "manifest.previous.json"


class SegmentStoreError(ValueError):
    """A segmented cache is missing, corrupt, or incompatible."""


@dataclass(frozen=True, slots=True)
class Segment:
    name: str
    file: str
    bytes: int
    sha256: str


class SegmentStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.manifest_path = self.directory / MANIFEST_NAME
        self.previous_manifest_path = self.directory / MANIFEST_BACKUP_NAME

    def publish(self, snapshot_path: Path) -> dict[str, Any]:
        """Publish all sections, then atomically swap the manifest pointer."""

        snapshot_path = snapshot_path.resolve()
        with Snapshot(snapshot_path) as snapshot:
            source_digest = snapshot.sha256
            segments: list[Segment] = []
            staging = Path(tempfile.mkdtemp(prefix=".segments-", dir=self.directory.parent if self.directory.parent.exists() else None))
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                # Move staging under the destination parent only; files are
                # content-addressed, so old manifests remain readable while a
                # new generation is being prepared.
                if staging.parent != self.directory:
                    destination_staging = self.directory / staging.name
                    shutil.move(str(staging), str(destination_staging))
                    staging = destination_staging
                for name in sorted(snapshot._sections):
                    data = snapshot._section_bytes(name)
                    digest = hashlib.sha256(data).hexdigest()
                    filename = f"{name}-{digest}.seg"
                    temporary = staging / filename
                    temporary.write_bytes(data)
                    with temporary.open("r+b") as handle:
                        handle.flush()
                        os.fsync(handle.fileno())
                    final = self.directory / filename
                    if not final.exists():
                        os.replace(temporary, final)
                    segments.append(Segment(name, filename, len(data), digest))
                payload = {
                    "schema": MANIFEST_SCHEMA,
                    "generation": snapshot.generation,
                    "source_snapshot_sha256": source_digest,
                    "segments": [segment.__dict__ if hasattr(segment, "__dict__") else {
                        "name": segment.name,
                        "file": segment.file,
                        "bytes": segment.bytes,
                        "sha256": segment.sha256,
                    } for segment in segments],
                }
                self.directory.mkdir(parents=True, exist_ok=True)
                manifest_tmp = self.directory / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
                manifest_tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                with manifest_tmp.open("r+b") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                if self.manifest_path.exists():
                    backup_tmp = self.directory / f".{MANIFEST_BACKUP_NAME}.{os.getpid()}.tmp"
                    shutil.copyfile(self.manifest_path, backup_tmp)
                    with backup_tmp.open("r+b") as handle:
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(backup_tmp, self.previous_manifest_path)
                os.replace(manifest_tmp, self.manifest_path)
                return payload
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def _read_manifest_file(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SegmentStoreError(f"manifest_unreadable:{error}") from error
        if payload.get("schema") != MANIFEST_SCHEMA or not isinstance(payload.get("segments"), list):
            raise SegmentStoreError("manifest_schema_mismatch")
        return payload

    def read_manifest(self) -> dict[str, Any]:
        return self._read_manifest_file(self.manifest_path)

    def recover_previous(self) -> dict[str, Any]:
        """Restore the last verified manifest after an interrupted pointer swap."""
        payload = self._read_manifest_file(self.previous_manifest_path)
        for item in payload["segments"]:
            self._validate_entry(item)
        manifest_tmp = self.directory / f".{MANIFEST_NAME}.{os.getpid()}.recovery.tmp"
        manifest_tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with manifest_tmp.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(manifest_tmp, self.manifest_path)
        return payload

    def validate(self) -> dict[str, Any]:
        payload = self.read_manifest()
        checked = 0
        for item in payload["segments"]:
            self._validate_entry(item)
            checked += 1
        return {"schema": "simplicio.fast.segments-validation/v1", "status": "valid", "segments": checked, "generation": payload["generation"]}

    def read(self, name: str) -> bytes:
        with self.map(name) as mapped:
            return bytes(mapped)

    @contextmanager
    def map(self, name: str) -> Iterator[mmap.mmap | bytes]:
        """Validate and map one segment, loading only its requested bytes."""

        payload = self.read_manifest()
        item = next((entry for entry in payload["segments"] if entry.get("name") == name), None)
        if item is None:
            raise SegmentStoreError(f"segment_not_found:{name}")
        self._validate_entry(item)
        path = self.directory / item["file"]
        if item["bytes"] == 0:
            yield b""
            return
        with path.open("rb") as handle:
            mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                yield mapped
            finally:
                mapped.close()

    def _validate_entry(self, item: Any) -> None:
        if not isinstance(item, dict) or not all(key in item for key in ("name", "file", "bytes", "sha256")):
            raise SegmentStoreError("segment_entry_invalid")
        if not isinstance(item["file"], str) or Path(item["file"]).name != item["file"]:
            raise SegmentStoreError("segment_path_invalid")
        path = self.directory / item["file"]
        try:
            size = path.stat().st_size
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as error:
            raise SegmentStoreError(f"segment_missing:{item['name']}") from error
        if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise SegmentStoreError(f"segment_checksum_mismatch:{item['name']}")


def migrate_snapshot(snapshot_path: Path, directory: Path) -> dict[str, Any]:
    """Idempotent monolith-to-segments migration entry point."""

    return SegmentStore(directory).publish(snapshot_path)
