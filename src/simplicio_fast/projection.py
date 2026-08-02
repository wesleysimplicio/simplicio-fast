"""Deterministic, read-only projection envelopes for Semantic Compute.

Projection payloads are derived views.  Producers remain authoritative and
the envelope carries enough provenance for a consumer to reject stale or
cross-repository data without exposing mmap implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


SCHEMA = "simplicio.fast.projection/v1"
PROJECTION_TYPES = frozenset({"code", "knowledge", "operations"})
_FORBIDDEN_KEYS = frozenset({"offset", "mmap_offset", "address", "pointer"})


class ProjectionError(ValueError):
    """Raised when a projection envelope is malformed or untrusted."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProjectionError("payload_not_json") from error


def _reject_private_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        if _FORBIDDEN_KEYS.intersection(value):
            raise ProjectionError("projection_exposes_offset")
        for child in value.values():
            _reject_private_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_private_fields(child)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectionEnvelope:
    """A typed, content-addressed read model with opaque stable provenance."""

    projection_type: str
    producer: str
    producer_schema: str
    generation: str
    stable_handle: str
    payload: Mapping[str, Any]
    payload_sha256: str

    @classmethod
    def create(
        cls,
        projection_type: str,
        *,
        producer: str,
        producer_schema: str,
        generation: str,
        stable_handle: str,
        payload: Mapping[str, Any],
    ) -> "ProjectionEnvelope":
        _validate_text(projection_type, "projection_type")
        if projection_type not in PROJECTION_TYPES:
            raise ProjectionError("projection_type_unsupported")
        for value, name in (
            (producer, "producer"),
            (producer_schema, "producer_schema"),
            (generation, "generation"),
            (stable_handle, "stable_handle"),
        ):
            _validate_text(value, name)
        if not isinstance(payload, Mapping):
            raise ProjectionError("payload_invalid")
        normalized = dict(payload)
        _reject_private_fields(normalized)
        return cls(
            projection_type=projection_type,
            producer=producer,
            producer_schema=producer_schema,
            generation=generation,
            stable_handle=stable_handle,
            payload=normalized,
            payload_sha256=_digest(normalized),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "projection_type": self.projection_type,
            "producer": self.producer,
            "producer_schema": self.producer_schema,
            "generation": self.generation,
            "stable_handle": self.stable_handle,
            "payload": dict(self.payload),
            "payload_sha256": self.payload_sha256,
        }

    def encode(self) -> bytes:
        return _canonical(self.to_dict()) + b"\n"

    @classmethod
    def decode(cls, raw: bytes) -> "ProjectionEnvelope":
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectionError("projection_invalid_json") from error
        if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
            raise ProjectionError("projection_schema_unsupported")
        payload = value.get("payload")
        expected = value.get("payload_sha256")
        if not isinstance(expected, str):
            raise ProjectionError("payload_digest_missing")
        result = cls.create(
            value.get("projection_type", ""),
            producer=value.get("producer", ""),
            producer_schema=value.get("producer_schema", ""),
            generation=value.get("generation", ""),
            stable_handle=value.get("stable_handle", ""),
            payload=payload if isinstance(payload, Mapping) else {},
        )
        if payload is not None and not isinstance(payload, Mapping):
            raise ProjectionError("payload_invalid")
        if result.payload_sha256 != expected:
            raise ProjectionError("payload_digest_mismatch")
        return result


class ProjectionStore:
    """In-memory derived projection index with explicit incremental receipts.

    The store is intentionally not a source database: callers supply complete
    envelopes from an authoritative producer, and the store only keeps the
    latest read model for one repository scope.
    """

    def __init__(self, repository: str) -> None:
        _validate_text(repository, "repository")
        self.repository = repository
        self._records: dict[str, ProjectionEnvelope] = {}
        self._generation: str | None = None

    @property
    def generation(self) -> str | None:
        return self._generation

    def publish(self, envelope: ProjectionEnvelope) -> None:
        declared_repository = envelope.payload.get("repository")
        if declared_repository is not None and declared_repository != self.repository:
            raise ProjectionError("projection_repository_mismatch")
        if self._generation is not None and envelope.generation != self._generation:
            raise ProjectionError("projection_generation_stale")
        previous = self._records.get(envelope.stable_handle)
        if previous is not None and previous.payload_sha256 != envelope.payload_sha256:
            raise ProjectionError("projection_handle_conflict")
        self._generation = envelope.generation
        self._records[envelope.stable_handle] = envelope

    def apply_delta(
        self,
        generation: str,
        *,
        changed: tuple[ProjectionEnvelope, ...] = (),
        deleted_handles: tuple[str, ...] = (),
        closure_handles: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        _validate_text(generation, "generation")
        if self._generation is not None and generation != self._generation:
            raise ProjectionError("projection_generation_stale")
        changed_handles = sorted({item.stable_handle for item in changed})
        deleted = sorted(set(deleted_handles))
        if set(changed_handles).intersection(deleted):
            raise ProjectionError("projection_delta_conflict")
        for item in changed:
            if item.generation != generation:
                raise ProjectionError("projection_generation_stale")
            self.publish(item)
        for handle in deleted:
            self._records.pop(handle, None)
        self._generation = generation
        closure = sorted(set(closure_handles).union(changed_handles, deleted))
        return {
            "schema": "simplicio.fast.projection-delta/v1",
            "repository": self.repository,
            "generation": generation,
            "changed_handles": changed_handles,
            "deleted_handles": deleted,
            "closure_handles": closure,
            "projection_digest": _digest(self.snapshot()),
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            self._records[key].to_dict() for key in sorted(self._records)
        ]


def _validate_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{field}_invalid")


__all__ = [
    "PROJECTION_TYPES",
    "ProjectionEnvelope",
    "ProjectionError",
    "ProjectionStore",
    "SCHEMA",
]
