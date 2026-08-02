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


def _validate_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{field}_invalid")


__all__ = ["PROJECTION_TYPES", "ProjectionEnvelope", "ProjectionError", "SCHEMA"]
