import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simplicio_fast.cli import main
from simplicio_fast.snapshot import build_snapshot


class ContextReceiptTest(unittest.TestCase):
    def invoke_context(self, root: Path, snapshot: Path) -> dict[str, object]:
        output = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "simplicio-fast",
                "context",
                "save",
                "--root",
                str(root),
                "--snapshot",
                str(snapshot),
            ],
        ), contextlib.redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    def make_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Simplicio Tests"], check=True)

    def test_context_includes_stable_git_and_snapshot_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("class User:\n    def save(self):\n        return True\n", encoding="utf-8")
            self.make_git_repo(root)
            subprocess.run(["git", "-C", str(root), "add", "sample.py"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            snapshot = root / "snapshot.sfast"
            build_snapshot(root, snapshot)

            first = self.invoke_context(root, snapshot)
            second = self.invoke_context(root, snapshot)
            provenance = first["provenance"]
            self.assertEqual(provenance, second["provenance"])
            self.assertEqual("simplicio.fast.provenance/v1", provenance["schema"])
            self.assertEqual(str(root.resolve()), provenance["repository_root"])
            self.assertEqual(commit, provenance["source_commit"])
            self.assertIsNone(provenance["source_commit_reason"])
            self.assertEqual(str(snapshot.resolve()), provenance["snapshot_path"])
            self.assertEqual(
                hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                provenance["snapshot_sha256"],
            )
            self.assertEqual(
                f"SFAST001:{provenance['snapshot_sha256']}",
                provenance["snapshot_generation"],
            )
            self.assertEqual(1, provenance["span_count"])

    def test_context_reports_explicit_reason_for_non_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(
                "def save():\n    return True\n", encoding="utf-8"
            )
            snapshot = root / "snapshot.sfast"
            build_snapshot(root, snapshot)

            payload = self.invoke_context(root, snapshot)
            provenance = payload["provenance"]
            self.assertIsNone(provenance["source_commit"])
            self.assertEqual("not_a_git_checkout", provenance["source_commit_reason"])


if __name__ == "__main__":
    unittest.main()
