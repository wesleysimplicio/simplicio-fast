"""Python reference for generation-scoped bitemporal semantic facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any


SCHEMA = "simplicio.fast.bitemporal-fact/v1"
AS_OF_SCHEMA = "simplicio.fast.as-of-query/v1"
STATES = {"active", "superseded", "tombstoned", "held"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TemporalInvariantError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class BitemporalFact:
    canonical_id: str
    repository: str
    base_generation: str
    overlay_generation: str | None
    source_commit: str
    source_sha256: str
    artifact_digest: str
    valid_from: int
    valid_to: int | None
    observed_at: int
    invalidated_at: int | None
    state: str
    reason_code: str | None
    predecessor: str | None
    successor: str | None
    dependencies: tuple[str, ...]
    provenance: dict[str, Any]

    @property
    def digest(self) -> str:
        payload = self._unsigned_record()
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _unsigned_record(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "canonical_id": self.canonical_id,
            "repository": self.repository,
            "base_generation": self.base_generation,
            "overlay_generation": self.overlay_generation,
            "source_commit": self.source_commit,
            "source_sha256": self.source_sha256,
            "artifact_digest": self.artifact_digest,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "observed_at": self.observed_at,
            "invalidated_at": self.invalidated_at,
            "state": self.state,
            "reason_code": self.reason_code,
            "predecessor": self.predecessor,
            "dependencies": list(self.dependencies),
            "provenance": self.provenance,
        }

    def as_record(self) -> dict[str, Any]:
        return {**self._unsigned_record(), "digest": self.digest}


class BitemporalOverlay:
    """Append-only world/system-time facts with deterministic as-of queries."""

    def __init__(self, repository: str, *, base_generation: str, overlay_generation: str | None = None) -> None:
        if not repository or not base_generation:
            raise ValueError("repository and base_generation are required")
        self.repository = repository
        self.base_generation = base_generation
        self.overlay_generation = overlay_generation
        self._facts: dict[str, list[BitemporalFact]] = {}
        self._world_sequence = 0
        self._observed_sequence = 0

    @staticmethod
    def _validate_digest(value: str, field: str) -> None:
        if _SHA256.fullmatch(value) is None:
            raise ValueError(f"{field} must be a lowercase SHA-256 digest")

    def _current(self, canonical_id: str) -> BitemporalFact | None:
        versions = self._facts.get(canonical_id, [])
        return versions[-1] if versions and versions[-1].valid_to is None else None

    def append(
        self,
        canonical_id: str,
        *,
        source_commit: str,
        source_sha256: str,
        artifact_digest: str,
        valid_from: int | None = None,
        state: str = "active",
        reason_code: str | None = None,
        predecessor: str | None = None,
        _allow_same_world: bool = False,
        dependencies: tuple[str, ...] = (),
        provenance: dict[str, Any] | None = None,
    ) -> BitemporalFact:
        self._validate_digest(canonical_id, "canonical_id")
        self._validate_digest(source_sha256, "source_sha256")
        self._validate_digest(artifact_digest, "artifact_digest")
        if not source_commit:
            raise ValueError("source_commit is required")
        if state not in STATES:
            raise ValueError(f"unsupported temporal state: {state}")
        previous = self._current(canonical_id)
        world = self._world_sequence + 1 if valid_from is None else valid_from
        if world < self._world_sequence or (
            world == self._world_sequence and not _allow_same_world
        ):
            raise TemporalInvariantError("world_time_out_of_order", "valid_from must advance monotonically")
        self._world_sequence = world
        self._observed_sequence += 1
        if previous is not None:
            closed = replace(
                previous,
                valid_to=world,
                invalidated_at=self._observed_sequence,
                state="superseded" if state == "active" else previous.state,
                reason_code="superseded" if state == "active" else previous.reason_code,
            )
            self._facts[canonical_id][-1] = closed
            predecessor = closed.digest
        fact = BitemporalFact(
            canonical_id=canonical_id,
            repository=self.repository,
            base_generation=self.base_generation,
            overlay_generation=self.overlay_generation,
            source_commit=source_commit,
            source_sha256=source_sha256,
            artifact_digest=artifact_digest,
            valid_from=world,
            valid_to=None,
            observed_at=self._observed_sequence,
            invalidated_at=None,
            state=state,
            reason_code=reason_code,
            predecessor=predecessor,
            successor=None,
            dependencies=tuple(dependencies),
            provenance=dict(provenance or {}),
        )
        if previous is not None:
            self._facts[canonical_id][-1] = replace(self._facts[canonical_id][-1], successor=fact.digest)
        self._facts.setdefault(canonical_id, []).append(fact)
        return fact

    def tombstone(
        self,
        canonical_id: str,
        *,
        source_commit: str,
        source_sha256: str,
        reason_code: str,
        successor: str | None = None,
        valid_from: int | None = None,
        _allow_same_world: bool = False,
    ) -> BitemporalFact:
        current = self._current(canonical_id)
        if current is None or current.state != "active":
            raise TemporalInvariantError("fact_not_active", "only an active fact can be tombstoned")
        result = self.append(
            canonical_id,
            source_commit=source_commit,
            source_sha256=source_sha256,
            artifact_digest=current.artifact_digest,
            state="tombstoned",
            reason_code=reason_code,
            valid_from=valid_from,
            _allow_same_world=_allow_same_world,
            provenance={"successor": successor} if successor else {},
        )
        return result

    def rename(
        self,
        old_id: str,
        new_id: str,
        *,
        source_commit: str,
        source_sha256: str,
        artifact_digest: str,
    ) -> BitemporalFact:
        current = self._current(old_id)
        if current is None or current.state != "active":
            raise TemporalInvariantError("fact_not_active", "only an active fact can be renamed")
        replacement = self.append(
            new_id,
            source_commit=source_commit,
            source_sha256=source_sha256,
            artifact_digest=artifact_digest,
            predecessor=current.digest,
        )
        rename_world = self._world_sequence
        self.tombstone(
            old_id,
            source_commit=source_commit,
            source_sha256=source_sha256,
            reason_code="renamed",
            successor=replacement.digest,
            valid_from=rename_world,
            _allow_same_world=True,
        )
        return replacement

    def as_of(
        self,
        world_sequence: int,
        *,
        generation: str | None = None,
        include_tombstones: bool = False,
    ) -> list[BitemporalFact]:
        if world_sequence < 1:
            raise TemporalInvariantError("invalid_as_of", "as_of sequence must be positive")
        if generation is not None and generation not in {self.base_generation, self.overlay_generation}:
            raise TemporalInvariantError("stale_generation", "as_of generation is outside this overlay")
        result: list[BitemporalFact] = []
        for versions in self._facts.values():
            candidates = [
                fact
                for fact in versions
                if fact.valid_from <= world_sequence
                and (fact.valid_to is None or world_sequence < fact.valid_to)
                and (include_tombstones or fact.state != "tombstoned")
            ]
            if candidates:
                result.append(candidates[-1])
        return sorted(result, key=lambda fact: fact.canonical_id)

    def verify(self) -> dict[str, Any]:
        invalid: list[str] = []
        for canonical_id, versions in self._facts.items():
            previous_to = None
            for index, fact in enumerate(versions):
                if fact.canonical_id != canonical_id or fact.repository != self.repository:
                    invalid.append(fact.digest)
                if fact.valid_to is not None and fact.valid_to <= fact.valid_from:
                    invalid.append(fact.digest)
                if previous_to is not None and previous_to != fact.valid_from:
                    invalid.append(fact.digest)
                if index and fact.predecessor != versions[index - 1].digest:
                    invalid.append(fact.digest)
                if fact.digest != hashlib.sha256(
                    json.dumps(fact._unsigned_record(), sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest():
                    invalid.append(fact.digest)
                previous_to = fact.valid_to
        return {
            "schema": SCHEMA,
            "status": "valid" if not invalid else "invalid",
            "repository": self.repository,
            "base_generation": self.base_generation,
            "overlay_generation": self.overlay_generation,
            "facts": sum(len(versions) for versions in self._facts.values()),
            "active": sum(
                1
                for versions in self._facts.values()
                if self._current(versions[-1].canonical_id) is not None
                and self._current(versions[-1].canonical_id).state == "active"
            ),
            "invalid_digests": sorted(set(invalid)),
        }

    def receipt(self, world_sequence: int | None = None) -> dict[str, Any]:
        sequence = self._world_sequence if world_sequence is None else world_sequence
        return {
            "schema": AS_OF_SCHEMA,
            "repository": self.repository,
            "base_generation": self.base_generation,
            "overlay_generation": self.overlay_generation,
            "as_of": sequence,
            "facts": [fact.as_record() for fact in self.as_of(sequence, include_tombstones=True)],
            "verification": self.verify(),
        }
