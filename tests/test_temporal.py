from __future__ import annotations

import unittest

from simplicio_fast.temporal import BitemporalOverlay, TemporalInvariantError


class BitemporalOverlayTest(unittest.TestCase):
    def test_as_of_preserves_previous_version_after_update(self) -> None:
        overlay = BitemporalOverlay("repo", base_generation="base", overlay_generation="slot-1")
        entity = "a" * 64
        first = overlay.append(
            entity,
            source_commit="c1",
            source_sha256="b" * 64,
            artifact_digest="d" * 64,
        )
        second = overlay.append(
            entity,
            source_commit="c2",
            source_sha256="e" * 64,
            artifact_digest="f" * 64,
        )
        self.assertEqual(["c1"], [fact.source_commit for fact in overlay.as_of(1)])
        self.assertEqual([second.digest], [fact.digest for fact in overlay.as_of(2)])
        self.assertEqual("superseded", overlay.as_of(1, include_tombstones=True)[0].state)
        self.assertEqual("valid", overlay.verify()["status"])

    def test_rename_and_tombstone_are_auditable(self) -> None:
        overlay = BitemporalOverlay("repo", base_generation="base")
        old_id, new_id = "1" * 64, "2" * 64
        overlay.append(old_id, source_commit="c1", source_sha256="3" * 64, artifact_digest="4" * 64)
        replacement = overlay.rename(
            old_id,
            new_id,
            source_commit="c2",
            source_sha256="5" * 64,
            artifact_digest="6" * 64,
        )
        current = overlay.as_of(2)
        self.assertEqual([new_id], [fact.canonical_id for fact in current])
        history = overlay.as_of(2, include_tombstones=True)
        self.assertEqual({old_id, new_id}, {fact.canonical_id for fact in history})
        self.assertEqual("renamed", next(fact for fact in history if fact.canonical_id == old_id).reason_code)
        self.assertEqual("valid", overlay.receipt()["verification"]["status"])
        self.assertIsNotNone(replacement.digest)

    def test_generation_and_order_guards_fail_closed(self) -> None:
        overlay = BitemporalOverlay("repo", base_generation="base", overlay_generation="slot")
        overlay.append("a" * 64, source_commit="c", source_sha256="b" * 64, artifact_digest="c" * 64)
        with self.assertRaises(TemporalInvariantError) as error:
            overlay.as_of(1, generation="other")
        self.assertEqual("stale_generation", error.exception.reason_code)
        with self.assertRaises(TemporalInvariantError) as error:
            overlay.append("a" * 64, source_commit="c", source_sha256="b" * 64, artifact_digest="c" * 64, valid_from=1)
        self.assertEqual("world_time_out_of_order", error.exception.reason_code)
