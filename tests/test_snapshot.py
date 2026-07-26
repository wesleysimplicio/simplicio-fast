import tempfile
import unittest
from pathlib import Path

from simplicio_fast.snapshot import Snapshot, build_snapshot


class SnapshotTest(unittest.TestCase):
    def test_binary_snapshot_query_and_incremental_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("class User:\n    def save(self):\n        return True\n")
            output = root / ".index/project.sfast"

            cold = build_snapshot(root, output)
            self.assertEqual(1, cold.parsed_files)
            with Snapshot(output) as snapshot:
                matches = snapshot.find("save")
                self.assertEqual("User.save", matches[0].qualified_name)

            warm = build_snapshot(root, output)
            self.assertEqual(0, warm.parsed_files)
            self.assertEqual(1, warm.reused_files)

            source.write_text(source.read_text() + "\ndef deactivate():\n    return False\n")
            changed = build_snapshot(root, output)
            self.assertEqual(1, changed.parsed_files)
            with Snapshot(output) as snapshot:
                self.assertEqual(1, len(snapshot.find("deactivate")))


if __name__ == "__main__":
    unittest.main()
