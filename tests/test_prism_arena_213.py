from __future__ import annotations

import pytest

from simplicio_fast.prism_arena import MAX_OVERLAYS_PER_SLOT, PrismArenaError, open_arena


def test_shared_base_and_ten_overlays():
    arena = open_arena("repo", "gen1", {"a.py": b"base-a", "b.py": b"base-b"})
    arena.open_slot("slot-1", prism_id="p1")
    arena.pin("slot-1", "fence-1")
    for i in range(MAX_OVERLAYS_PER_SLOT):
        arena.create_overlay(
            "slot-1",
            task_id=f"T{i}",
            attempt_id="a1",
            worktree_id=f"wt{i}",
            fence="fence-1",
        )
    with pytest.raises(PrismArenaError, match="overlay_limit"):
        arena.create_overlay(
            "slot-1",
            task_id="T10",
            attempt_id="a1",
            worktree_id="wtX",
            fence="fence-1",
        )
    arena.write_overlay("slot-1", "T0", "fence-1", "a.py", b"dirty-0")
    arena.write_overlay("slot-1", "T1", "fence-1", "a.py", b"dirty-1")
    assert arena.read("slot-1", "T0", "fence-1", "a.py") == b"dirty-0"
    assert arena.read("slot-1", "T1", "fence-1", "a.py") == b"dirty-1"
    assert arena.read("slot-1", "T2", "fence-1", "a.py") == b"base-a"
    assert arena.base_hash("a.py") is not None
    assert arena.overlay_never_mutates_base() is True
    receipt = arena.receipt("open")
    assert receipt["schema"].startswith("simplicio.fast.prism-arena")
    assert receipt["overlay_counts"]["slot-1"] == 10


def test_child_slot_reuses_base_handle():
    arena = open_arena("repo", "gen1", {"x.py": b"x"})
    arena.open_slot("parent", prism_id="p1")
    arena.pin("parent", "f1")
    child = arena.open_slot("child", prism_id="p1", parent_slot_id="parent")
    assert child.parent_slot_id == "parent"
    arena.pin("child", "f2")
    assert arena.read("child", "missing", "f2", "x.py") == b"x"
