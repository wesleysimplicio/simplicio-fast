import hashlib
import tempfile
import unittest
from pathlib import Path

from simplicio_fast.processor import ProjectProcessor


class ProjectProcessorTest(unittest.TestCase):
    def test_ingest_understand_plan_and_guarded_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "users.py"
            source.write_text(
                "class UserService:\n"
                "    def create_user(self, name: str) -> str:\n"
                "        return name\n"
            )
            processor = ProjectProcessor(root, root / ".simplicio-fast/project.sfast")

            ingest = processor.ingest()
            self.assertEqual("simplicio.fast.ingest/v2", ingest["schema"])
            understanding = processor.understand("change UserService")
            self.assertIn("users.py", understanding.files)

            plan = processor.plan("change UserService")
            self.assertEqual("simplicio.fast.plandag/v2", plan["schema"])
            self.assertEqual(["orient", "modify", "validate", "refresh"], [
                node["id"] for node in plan["nodes"]
            ])

            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            changeset = {
                "schema": "simplicio.fast.changeset/v2",
                "changes": [
                    {
                        "path": "users.py",
                        "expected_sha256": expected,
                        "replacements": [
                            {
                                "start_line": 3,
                                "end_line": 3,
                                "content": "        return name.strip()",
                            }
                        ],
                    }
                ],
            }
            dry_run = processor.apply_changeset(changeset, write=False)
            self.assertEqual("dry-run", dry_run["mode"])
            self.assertNotIn("strip", source.read_text())

            written = processor.apply_changeset(changeset, write=True)
            self.assertEqual("write", written["mode"])
            self.assertIn("return name.strip()", source.read_text())

    def test_rejects_stale_changeset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("value = 1\n")
            processor = ProjectProcessor(root, root / "project.sfast")
            changeset = {
                "schema": "simplicio.fast.changeset/v2",
                "changes": [
                    {
                        "path": "app.py",
                        "expected_sha256": "0" * 64,
                        "replacements": [
                            {"start_line": 1, "end_line": 1, "content": "value = 2"}
                        ],
                    }
                ],
            }
            with self.assertRaisesRegex(ValueError, "stale source hash"):
                processor.apply_changeset(changeset, write=True)


if __name__ == "__main__":
    unittest.main()
