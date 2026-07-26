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

from simplicio_fast.cli import main, source_commit
from simplicio_fast.snapshot import build_snapshot


class ContextProvenanceTest(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with patch.object(sys, "argv", ["simplicio-fast", *args]), contextlib.redirect_stdout(output):
            try:
                main()
            except SystemExit as error:
                exit_code = int(error.code)
            else:
                exit_code = 0
        return exit_code, json.loads(output.getvalue())

    def make_git_repo(self, root: Path) -> str:
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Simplicio Tests"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "sample.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_git_receipt_is_deterministic_and_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("class User:\n    def save(self):\n        return True\n", encoding="utf-8")
            commit = self.make_git_repo(root)
            snapshot = root / "snapshot.sfast"
            build_snapshot(root, snapshot)
            command = ("context", "save", "--root", str(root), "--snapshot", str(snapshot), "--max-tokens", "80")

            first_code, first = self.invoke(*command)
            second_code, second = self.invoke(*command)
            self.assertEqual(0, first_code)
            self.assertEqual(0, second_code)
            receipt = first["provenance"]
            self.assertEqual(receipt, second["provenance"])
            self.assertEqual("simplicio.fast.provenance/v1", receipt["schema"])
            self.assertEqual(str(root.resolve()), receipt["repository_root"])
            self.assertEqual(commit, receipt["source_commit"])
            self.assertIsNone(receipt["source_commit_reason"])
            self.assertEqual(str(snapshot.resolve()), receipt["snapshot_path"])
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            self.assertEqual(digest, receipt["snapshot_sha256"])
            self.assertEqual(f"SFAST001:{digest}", receipt["snapshot_generation"])
            self.assertEqual(first["limits"], receipt["limits"])
            self.assertEqual(len(first["spans"]), receipt["span_count"])

            (root / "sample.py").write_text("class User:\n    def save(self):\n        return False\n", encoding="utf-8")
            build_snapshot(root, snapshot)
            changed_code, changed = self.invoke(*command)
            self.assertEqual(0, changed_code)
            self.assertNotEqual(receipt["snapshot_sha256"], changed["provenance"]["snapshot_sha256"])
            self.assertNotEqual(receipt["snapshot_generation"], changed["provenance"]["snapshot_generation"])

    def test_emit_is_safe_for_legacy_windows_console_encoding(self) -> None:
        from simplicio_fast.cli import emit

        encoded = io.BytesIO()
        console = io.TextIOWrapper(encoded, encoding="cp1252")
        with contextlib.redirect_stdout(console):
            emit({"symbol": "route → local"})
        console.flush()

        output = encoded.getvalue().decode("cp1252")
        self.assertIn("\\u2192", output)
        self.assertEqual({"symbol": "route → local"}, json.loads(output))

    def test_non_git_and_stale_source_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("def save():\n    return True\n", encoding="utf-8")
            snapshot = root / "snapshot.sfast"
            build_snapshot(root, snapshot)
            code, payload = self.invoke("context", "save", "--root", str(root), "--snapshot", str(snapshot))
            self.assertEqual(0, code)
            self.assertIsNone(payload["provenance"]["source_commit"])
            self.assertEqual("not_a_git_checkout", payload["provenance"]["source_commit_reason"])

            source.write_text("def save():\n    return False\n", encoding="utf-8")
            code, payload = self.invoke("context", "save", "--root", str(root), "--snapshot", str(snapshot))
            self.assertEqual(2, code)
            self.assertEqual("simplicio.fast.error/v1", payload["schema"])
            self.assertEqual("StaleSnapshotError", payload["error"])

    def test_corrupt_snapshot_fails_closed_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("def save():\n    return True\n", encoding="utf-8")
            snapshot = root / "snapshot.sfast"
            build_snapshot(root, snapshot)
            snapshot.write_bytes(snapshot.read_bytes()[:-1])
            code, payload = self.invoke("context", "save", "--root", str(root), "--snapshot", str(snapshot))
            self.assertEqual(2, code)
            self.assertEqual("simplicio.fast.error/v1", payload["schema"])
            self.assertNotIn("provenance", payload)

    def test_other_json_commands_and_git_unavailable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.py"
            source.write_text("def save():\n    return True\n", encoding="utf-8")
            snapshot = root / "snapshot.sfast"
            code, payload = self.invoke("build", str(root), "-o", str(snapshot))
            self.assertEqual(0, code)
            self.assertEqual("simplicio.fast.build/v1", payload["schema"])
            for command in (
                ("query", "save"),
                ("search", "save"),
                ("impact", "save"),
                ("stats",),
                ("doctor",),
            ):
                code, payload = self.invoke(*command, "--snapshot", str(snapshot))
                self.assertEqual(0, code)
                self.assertTrue(payload["schema"].startswith("simplicio.fast."))

        with patch("simplicio_fast.cli.subprocess.run", side_effect=OSError("git unavailable")):
            self.assertEqual((None, "git_unavailable"), source_commit(Path(".")))

    def test_doctor_reports_stable_recovery_and_refresh_repairs_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("def recovered():\n    return True\n")
            snapshot = root / "project.sfast"
            snapshot.write_bytes(b"truncated")

            code, doctor = self.invoke("doctor", "--snapshot", str(snapshot))
            self.assertEqual(1, code)
            integrity = next(item for item in doctor["checks"] if item["name"] == "snapshot_integrity")
            self.assertEqual("snapshot_corrupt_rebuild", integrity["detail"]["recovery_code"])

            code, refreshed = self.invoke(
                "refresh", str(root), "--output", str(snapshot), "--timeout", "30"
            )
            self.assertEqual(0, code)
            self.assertEqual("simplicio.fast.build/v1", refreshed["schema"])
            code, query = self.invoke("query", "recovered", "--snapshot", str(snapshot))
            self.assertEqual(0, code)
            self.assertEqual(1, len(query["matches"]))


if __name__ == "__main__":
    unittest.main()
