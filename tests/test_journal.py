from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simplicio_fast.journal import ChangeJournal, ChangeJournalError, ZERO_HASH


class ChangeJournalTest(unittest.TestCase):
    def test_append_chain_is_deterministic_and_reconstructible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = ChangeJournal(Path(directory) / "changes.log")
            first = journal.append("create", "service.py", generation="g1", after_sha256="a" * 64)
            second = journal.append(
                "update", "service.py", generation="g2", before_sha256="a" * 64, after_sha256="b" * 64
            )
            events = journal.read()
            self.assertEqual(ZERO_HASH, first.prev_hash)
            self.assertEqual(first.record_hash, second.prev_hash)
            self.assertEqual([1, 2], [event.sequence for event in events])

    def test_truncated_tail_recovers_only_complete_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changes.log"
            journal = ChangeJournal(path)
            journal.append("create", "service.py", generation="g1")
            with path.open("ab") as handle:
                handle.write(b'{"sequence":2,"event_type":"update"')
            with self.assertRaisesRegex(ChangeJournalError, "truncated_tail"):
                journal.read()
            receipt = journal.recover()
            self.assertEqual("recovered", receipt["status"])
            self.assertEqual(1, len(journal.read()))

    def test_corruption_and_path_escape_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = ChangeJournal(Path(directory) / "changes.log")
            journal.append("delete", "service.py", generation="g1")
            content = journal.path.read_bytes().replace(b'"delete"', b'"update"')
            journal.path.write_bytes(content)
            with self.assertRaisesRegex(ChangeJournalError, "record_hash_mismatch"):
                journal.read()
            with self.assertRaisesRegex(ChangeJournalError, "path_outside_repository"):
                journal.append("create", "../escape.py", generation="g1")


if __name__ == "__main__":
    unittest.main()
