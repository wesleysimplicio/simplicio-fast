"""PrismArena: immutable base generation + up to 10 isolated task overlays per slot.

Fast is a data plane only — no scheduling, no agent selection.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

ARENA_SCHEMA = "simplicio.fast.prism-arena/v1"
MAX_OVERLAYS_PER_SLOT = 10


class PrismArenaError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _sha(value: Any) -> str:
    if isinstance(value, bytes):
        data = value
    else:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(data).hexdigest()


def _safe_path(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise PrismArenaError("overlay_escape", path)
    return value


@dataclass
class TaskOverlay:
    task_id: str
    attempt_id: str
    worktree_id: str
    dirty: dict[str, bytes | None] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def write(self, path: str, data: bytes | None) -> None:
        self.dirty[_safe_path(path)] = data

    def read(self, path: str) -> tuple[bool, bytes | None]:
        key = _safe_path(path)
        if key in self.dirty:
            return True, self.dirty[key]
        return False, None


@dataclass
class SlotView:
    slot_id: str
    prism_id: str
    parent_slot_id: str | None
    budget_bytes: int
    overlays: dict[str, TaskOverlay] = field(default_factory=dict)
    child_slot_ids: list[str] = field(default_factory=list)


@dataclass
class GenerationLease:
    generation_id: str
    slot_id: str
    fence: str
    expires_at: float
    readers: int = 1


class PrismArena:
    """One shared immutable base + isolated overlays (≤10 per slot)."""

    def __init__(self, repo_id: str, generation_id: str, base: Mapping[str, bytes]) -> None:
        self.repo_id = repo_id
        self.generation_id = generation_id
        self.base = dict(base)
        self.base_digest = _sha({k: _sha(v) for k, v in sorted(self.base.items())})
        self.slots: dict[str, SlotView] = {}
        self.leases: dict[tuple[str, str], GenerationLease] = {}
        self.metrics = {
            "pages": 0,
            "hits": 0,
            "misses": 0,
            "overlay_writes": 0,
            "reuses": 0,
        }

    def open_slot(
        self,
        slot_id: str,
        *,
        prism_id: str,
        parent_slot_id: str | None = None,
        budget_bytes: int = 16 * 1024 * 1024,
    ) -> SlotView:
        if parent_slot_id and parent_slot_id not in self.slots:
            raise PrismArenaError("parent_slot_missing", parent_slot_id)
        if slot_id not in self.slots:
            self.slots[slot_id] = SlotView(
                slot_id=slot_id,
                prism_id=prism_id,
                parent_slot_id=parent_slot_id,
                budget_bytes=budget_bytes,
            )
            if parent_slot_id:
                parent = self.slots[parent_slot_id]
                if slot_id not in parent.child_slot_ids:
                    parent.child_slot_ids.append(slot_id)
                self.metrics["reuses"] += 1
        return self.slots[slot_id]

    def pin(self, slot_id: str, fence: str, *, ttl_s: float = 300.0) -> GenerationLease:
        if slot_id not in self.slots:
            raise PrismArenaError("slot_missing", slot_id)
        lease = GenerationLease(
            generation_id=self.generation_id,
            slot_id=slot_id,
            fence=fence,
            expires_at=time.time() + max(1.0, min(ttl_s, 3600.0)),
        )
        self.leases[(slot_id, fence)] = lease
        return lease

    def _require_lease(self, slot_id: str, fence: str) -> None:
        lease = self.leases.get((slot_id, fence))
        if lease is None or lease.expires_at < time.time():
            raise PrismArenaError("lease_stale", slot_id)

    def create_overlay(
        self,
        slot_id: str,
        *,
        task_id: str,
        attempt_id: str,
        worktree_id: str,
        fence: str,
    ) -> TaskOverlay:
        self._require_lease(slot_id, fence)
        slot = self.slots[slot_id]
        if task_id not in slot.overlays and len(slot.overlays) >= MAX_OVERLAYS_PER_SLOT:
            raise PrismArenaError("overlay_limit", f">{MAX_OVERLAYS_PER_SLOT}")
        overlay = TaskOverlay(task_id=task_id, attempt_id=attempt_id, worktree_id=worktree_id)
        slot.overlays[task_id] = overlay
        return overlay

    def write_overlay(
        self,
        slot_id: str,
        task_id: str,
        fence: str,
        path: str,
        data: bytes | None,
    ) -> None:
        self._require_lease(slot_id, fence)
        slot = self.slots[slot_id]
        overlay = slot.overlays.get(task_id)
        if overlay is None:
            raise PrismArenaError("overlay_missing", task_id)
        used = sum(len(v or b"") for v in overlay.dirty.values())
        if data is not None and used + len(data) > slot.budget_bytes:
            raise PrismArenaError("budget_exceeded", task_id)
        overlay.write(path, data)
        self.metrics["overlay_writes"] += 1

    def read(self, slot_id: str, task_id: str, fence: str, path: str) -> bytes | None:
        self._require_lease(slot_id, fence)
        path = _safe_path(path)
        self.metrics["pages"] += 1
        slot = self.slots.get(slot_id)
        if slot is None:
            raise PrismArenaError("slot_missing", slot_id)
        overlay = slot.overlays.get(task_id)
        if overlay is not None:
            hit, value = overlay.read(path)
            if hit:
                self.metrics["hits"] += 1
                return value
        self.metrics["misses"] += 1
        return self.base.get(path)

    def base_hash(self, path: str) -> str | None:
        data = self.base.get(_safe_path(path))
        return None if data is None else _sha(data)

    def overlay_never_mutates_base(self) -> bool:
        """Invariant probe for tests."""
        snapshot = {k: _sha(v) for k, v in self.base.items()}
        return snapshot == {k: _sha(v) for k, v in self.base.items()} and _sha(snapshot) == self.base_digest

    def receipt(self, kind: str = "open") -> dict[str, Any]:
        body = {
            "schema": ARENA_SCHEMA,
            "kind": kind,
            "repo_id": self.repo_id,
            "generation_id": self.generation_id,
            "base_digest": self.base_digest,
            "slot_count": len(self.slots),
            "overlay_counts": {sid: len(slot.overlays) for sid, slot in sorted(self.slots.items())},
            "metrics": dict(self.metrics),
            "max_overlays_per_slot": MAX_OVERLAYS_PER_SLOT,
        }
        body["receipt_hash"] = _sha(body)
        return body


def open_arena(repo_id: str, generation_id: str, files: Mapping[str, bytes]) -> PrismArena:
    return PrismArena(repo_id, generation_id, files)
