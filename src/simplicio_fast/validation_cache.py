"""Content-addressed validation facts and bounded affected-test selection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


CACHE_KEY_SCHEMA = "simplicio.fast.validation-cache-key/v1"
RESULT_SCHEMA = "simplicio.fast.validation-result/v1"


class ValidationCacheError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationCacheError("cache_key_not_json") from error


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationKey:
    source_merkle: str
    lockfiles_digest: str
    toolchain: str
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    platform: str = "unknown"
    config_digest: str = ""
    fixture_digest: str = ""
    generation: str = ""
    producer_schema: str = "simplicio.fast.validation/v1"
    freshness_class: str = "normal"

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        if not self.source_merkle or not self.lockfiles_digest or not self.toolchain or not self.command:
            raise ValidationCacheError("cache_key_required_missing")
        if any(not isinstance(name, str) or not name or not isinstance(value, str) for name, value in self.environment):
            raise ValidationCacheError("cache_environment_invalid")
        return {
            "schema": CACHE_KEY_SCHEMA,
            "source_merkle": self.source_merkle,
            "lockfiles_digest": self.lockfiles_digest,
            "toolchain": self.toolchain,
            "command": list(self.command),
            "environment": [list(item) for item in sorted(self.environment)],
            "platform": self.platform,
            "config_digest": self.config_digest,
            "fixture_digest": self.fixture_digest,
            "generation": self.generation,
            "producer_schema": self.producer_schema,
            "freshness_class": self.freshness_class,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    key_digest: str
    status: str
    result_digest: str
    command: tuple[str, ...]
    fresh: bool
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "partial"}:
            raise ValidationCacheError("result_status_invalid")
        if not self.key_digest or not self.result_digest:
            raise ValidationCacheError("result_digest_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESULT_SCHEMA,
            "key_digest": self.key_digest,
            "status": self.status,
            "result_digest": self.result_digest,
            "command": list(self.command),
            "fresh": self.fresh,
            "evidence": list(self.evidence),
        }


class ValidationCache:
    """In-memory derived cache; callers retain authority over execution policy."""

    def __init__(self) -> None:
        self._entries: dict[str, ValidationResult] = {}

    def save(self, path: Path) -> dict[str, Any]:
        """Persist derived results atomically; execution authority stays external."""
        body = {
            "schema": "simplicio.fast.validation-cache/v1",
            "entries": [self._entries[key].to_dict() for key in sorted(self._entries)],
        }
        document = {"body": body, "cache_sha256": _digest(body)}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(_canonical(document) + b"\n")
        temporary.replace(path)
        return {"schema": "simplicio.fast.validation-cache-receipt/v1", "status": "saved", "entries": len(self._entries), "cache_sha256": document["cache_sha256"]}

    @classmethod
    def load(cls, path: Path) -> "ValidationCache":
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationCacheError("cache_document_invalid") from error
        if not isinstance(document, Mapping):
            raise ValidationCacheError("cache_document_invalid")
        body = document.get("body")
        if not isinstance(body, Mapping) or body.get("schema") != "simplicio.fast.validation-cache/v1":
            raise ValidationCacheError("cache_schema_unsupported")
        if document.get("cache_sha256") != _digest(body):
            raise ValidationCacheError("cache_digest_mismatch")
        entries = body.get("entries")
        if not isinstance(entries, list):
            raise ValidationCacheError("cache_entries_invalid")
        cache = cls()
        for raw in entries:
            if not isinstance(raw, Mapping):
                raise ValidationCacheError("cache_entries_invalid")
            try:
                entry = ValidationResult(
                    str(raw["key_digest"]), str(raw["status"]), str(raw["result_digest"]),
                    tuple(raw["command"]), bool(raw["fresh"]), tuple(raw.get("evidence", ())),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValidationCacheError("cache_entry_invalid") from error
            cache._entries[entry.key_digest] = entry
        return cache

    def put(self, key: ValidationKey, *, status: str, result: Any, command: Sequence[str] | None = None, fresh: bool = True, evidence: Sequence[str] = ()) -> ValidationResult:
        result_digest = _digest(result)
        entry = ValidationResult(key.digest, status, result_digest, tuple(command or key.command), fresh, tuple(evidence))
        self._entries[key.digest] = entry
        return entry

    def get(self, key: ValidationKey, *, require_fresh: bool = False) -> ValidationResult | None:
        entry = self._entries.get(key.digest)
        if entry is None or (require_fresh and not entry.fresh):
            return None
        return entry

    def affected(self, changed_handles: Sequence[str], tests: Mapping[str, Sequence[str]], *, max_tests: int = 1000) -> dict[str, Any]:
        if max_tests <= 0:
            raise ValidationCacheError("selection_budget_invalid")
        changed = set(changed_handles)
        selected = sorted({test for handle, values in tests.items() if handle in changed for test in values})
        complete = len(selected) <= max_tests
        return {
            "schema": "simplicio.fast.affected-validation/v1",
            "tests": selected[:max_tests],
            "changed_handles": sorted(changed),
            "complete": complete,
            "truncation_reasons": [] if complete else ["test_budget"],
        }


__all__ = ["CACHE_KEY_SCHEMA", "RESULT_SCHEMA", "ValidationCache", "ValidationCacheError", "ValidationKey", "ValidationResult"]
