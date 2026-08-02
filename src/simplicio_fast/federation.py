"""Bounded, deterministic federation of pinned projection members."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Sequence


MANIFEST_SCHEMA = "simplicio.fast.federation-manifest/v1"
EDGE_SCHEMA = "simplicio.fast.federated-edge/v1"
GENERATION_SCHEMA = "simplicio.fast.federated-generation/v1"
MAX_MEMBERS = 256
MAX_EDGES = 100_000
MAX_BYTES = 8 * 1024 * 1024


class FederationError(ValueError):
    """Raised when a federation cannot be safely compiled or queried."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FederationError("federation_not_json") from error
    if len(encoded) > MAX_BYTES:
        raise FederationError("federation_size_limit")
    return encoded


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FederationError(reason)
    return value


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class FederationMember:
    repository: str
    commit: str
    generation: str
    schema: str
    digest: str
    scope: str = "*"
    tombstone: bool = False

    def __post_init__(self) -> None:
        for value, reason in (
            (self.repository, "member_repository_invalid"),
            (self.commit, "member_commit_invalid"),
            (self.generation, "member_generation_invalid"),
            (self.schema, "member_schema_invalid"),
            (self.digest, "member_digest_invalid"),
            (self.scope, "member_scope_invalid"),
        ):
            _text(value, reason)
        if not self.digest.startswith("sha256:"):
            raise FederationError("member_digest_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "commit": self.commit,
            "generation": self.generation,
            "schema": self.schema,
            "digest": self.digest,
            "scope": self.scope,
            "tombstone": self.tombstone,
        }


@dataclass(frozen=True, slots=True)
class FederatedEdge:
    source_handle: str
    target_handle: str
    relation_type: str
    confidence: float
    evidence: tuple[str, ...] = ()
    derived: bool = False

    def __post_init__(self) -> None:
        for value, reason in (
            (self.source_handle, "edge_source_invalid"),
            (self.target_handle, "edge_target_invalid"),
            (self.relation_type, "edge_relation_invalid"),
        ):
            _text(value, reason)
        if not 0.0 <= self.confidence <= 1.0:
            raise FederationError("edge_confidence_invalid")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise FederationError("edge_evidence_invalid")
        if self.derived and not self.evidence:
            raise FederationError("derived_edge_evidence_missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EDGE_SCHEMA,
            "source_handle": self.source_handle,
            "target_handle": self.target_handle,
            "relation_type": self.relation_type,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "derived": self.derived,
        }


class Federation:
    """Immutable member/edge view with bounded read-only indexes."""

    def __init__(self, members: Sequence[FederationMember], edges: Sequence[FederatedEdge] = ()) -> None:
        if not members or len(members) > MAX_MEMBERS:
            raise FederationError("member_count_limit")
        if len(edges) > MAX_EDGES:
            raise FederationError("edge_count_limit")
        repositories = [member.repository.casefold() for member in members]
        if len(repositories) != len(set(repositories)):
            raise FederationError("duplicate_member_repository")
        if any(member.tombstone for member in members):
            raise FederationError("member_tombstone_present")
        self.members = tuple(sorted(members, key=lambda item: (item.repository, item.generation)))
        self.edges = tuple(sorted(edges, key=lambda item: (item.source_handle, item.target_handle, item.relation_type)))
        self._consumers: dict[str, tuple[FederatedEdge, ...]] = {}
        self._dependencies: dict[str, tuple[FederatedEdge, ...]] = {}
        for edge in self.edges:
            self._consumers[edge.target_handle] = self._consumers.get(edge.target_handle, ()) + (edge,)
            self._dependencies[edge.source_handle] = self._dependencies.get(edge.source_handle, ()) + (edge,)

    @property
    def generation(self) -> str:
        return _digest({"members": [item.to_dict() for item in self.members], "edges": [item.to_dict() for item in self.edges]})

    def manifest(self) -> dict[str, Any]:
        body = {
            "schema": MANIFEST_SCHEMA,
            "members": [item.to_dict() for item in self.members],
            "edges": [item.to_dict() for item in self.edges],
            "generation": self.generation,
        }
        return {"schema": GENERATION_SCHEMA, "body": body, "digest": _digest(body)}

    def encode(self) -> bytes:
        return _canonical(self.manifest()) + b"\n"

    def apply_delta(
        self,
        changed_members: Sequence[FederationMember] = (),
        *,
        removed_repositories: Sequence[str] = (),
        added_edges: Sequence[FederatedEdge] = (),
        removed_edges: Sequence[FederatedEdge] = (),
    ) -> tuple["Federation", dict[str, Any]]:
        """Build a new generation from changed members without mutating this one."""
        removed = {repository.casefold() for repository in removed_repositories}
        active_changes: dict[str, FederationMember] = {}
        tombstones = set(removed)
        for member in changed_members:
            key = member.repository.casefold()
            if member.tombstone:
                tombstones.add(key)
            else:
                active_changes[key] = member
        members = [
            member
            for member in self.members
            if member.repository.casefold() not in tombstones
            and member.repository.casefold() not in active_changes
        ]
        members.extend(active_changes.values())
        removed_edge_set = set(removed_edges)
        def belongs_to_removed(edge: FederatedEdge) -> bool:
            return any(
                edge.source_handle.casefold().startswith(repository + ":")
                or edge.target_handle.casefold().startswith(repository + ":")
                for repository in tombstones
            )
        edges = [
            edge
            for edge in self.edges
            if edge not in removed_edge_set and not belongs_to_removed(edge)
        ]
        edges.extend(added_edges)
        next_generation = Federation(members, edges)
        closure = sorted(
            {
                handle
                for edge in (*added_edges, *removed_edges)
                for handle in (edge.source_handle, edge.target_handle)
            }
        )
        receipt = {
            "schema": "simplicio.fast.federated-delta/v1",
            "from_generation": self.generation,
            "to_generation": next_generation.generation,
            "changed_repositories": sorted(active_changes),
            "tombstones": sorted(tombstones),
            "closure_handles": closure,
            "reused_members": len(members) - len(active_changes),
            "complete": True,
        }
        return next_generation, receipt

    def consumers(self, target_handle: str, *, max_edges: int = 1000) -> list[dict[str, Any]]:
        _text(target_handle, "target_handle_invalid")
        if max_edges < 0 or max_edges > MAX_EDGES:
            raise FederationError("edge_budget_invalid")
        return [edge.to_dict() for edge in self._consumers.get(target_handle, ())[:max_edges]]

    def dependencies(self, source_handle: str, *, max_edges: int = 1000) -> list[dict[str, Any]]:
        _text(source_handle, "source_handle_invalid")
        if max_edges < 0 or max_edges > MAX_EDGES:
            raise FederationError("edge_budget_invalid")
        return [edge.to_dict() for edge in self._dependencies.get(source_handle, ())[:max_edges]]

    def traverse(self, start_handle: str, *, max_depth: int = 8, max_nodes: int = 1000) -> dict[str, Any]:
        _text(start_handle, "source_handle_invalid")
        if max_depth < 0 or max_nodes <= 0 or max_nodes > MAX_EDGES:
            raise FederationError("traversal_budget_invalid")
        queue: list[tuple[str, int]] = [(start_handle, 0)]
        visited: set[str] = set()
        paths: dict[str, list[str]] = {start_handle: [start_handle]}
        edges: list[dict[str, Any]] = []
        while queue and len(visited) < max_nodes:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for edge in self._dependencies.get(current, ()):
                if len(edges) >= MAX_EDGES:
                    raise FederationError("edge_budget_exceeded")
                edges.append(edge.to_dict())
                if edge.target_handle not in visited and edge.target_handle not in paths:
                    paths[edge.target_handle] = paths[current] + [edge.target_handle]
                    queue.append((edge.target_handle, depth + 1))
        return {"start_handle": start_handle, "nodes": sorted(visited), "edges": edges, "paths": paths, "complete": not queue}


def compile_federation(members: Iterable[FederationMember], edges: Iterable[FederatedEdge] = ()) -> Federation:
    """Compile a pinned set; no implicit latest/member discovery is allowed."""
    return Federation(tuple(members), tuple(edges))


__all__ = [
    "EDGE_SCHEMA", "FederatedEdge", "Federation", "FederationError",
    "FederationMember", "GENERATION_SCHEMA", "MANIFEST_SCHEMA", "compile_federation",
]
