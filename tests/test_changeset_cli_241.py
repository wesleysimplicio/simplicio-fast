import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class ChangesetCli241Test(unittest.TestCase):
    def run_process(self, root: Path, *args: str) -> tuple[int, str, str]:
        """Run the real CLI without anonymous pipes.

        Python 3.14 on Windows can report WinError 6 while duplicating pytest's
        captured pipe handles for a nested subprocess. File-backed streams keep
        this installed-CLI test on the same process boundary without relying on
        that fragile handle path.
        """

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        with tempfile.TemporaryDirectory() as directory:
            stdout_path = Path(directory) / "stdout.txt"
            stderr_path = Path(directory) / "stderr.txt"
            with (
                stdout_path.open("w", encoding="utf-8") as stdout,
                stderr_path.open("w", encoding="utf-8") as stderr,
            ):
                result = subprocess.run(
                    [sys.executable, "-m", "simplicio_fast.cli", *args],
                    cwd=root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    check=False,
                )
            return (
                result.returncode,
                stdout_path.read_text(encoding="utf-8"),
                stderr_path.read_text(encoding="utf-8"),
            )

    def run_cli(self, root: Path, *args: str) -> dict:
        returncode, stdout, stderr = self.run_process(root, *args)
        if returncode:
            raise RuntimeError(f"stdout={stdout} stderr={stderr}")
        return json.loads(stdout)

    def prepare_and_materialize(self, root: Path, operations: list[dict]) -> dict:
        intent = root / "intent.json"
        binary = root / "changeset.sfc"
        journal = root / "changeset.journal"
        intent.write_text(json.dumps({"operations": operations}), encoding="utf-8")
        prepared = self.run_cli(
            root,
            "changeset",
            "prepare",
            str(intent),
            "--root",
            str(root),
            "--output",
            str(binary),
            "--base-generation",
            "b" * 64,
            "--overlay-generation",
            "o" * 64,
            "--attempt",
            "attempt-1",
            "--worktree-id",
            "worktree-1",
            "--lease-id",
            "lease-1",
            "--fencing-token",
            "fence-1",
        )
        self.assertEqual("sealed", prepared["status"])
        inspected = self.run_cli(root, "changeset", "inspect", str(binary))
        self.assertEqual("valid", inspected["status"])
        validated = self.run_cli(
            root, "changeset", "validate", str(binary), "--root", str(root)
        )
        self.assertEqual("valid", validated["status"])
        exported = self.run_cli(root, "changeset", "export-json", str(binary))
        self.assertEqual("simplicio.fast.binary-changeset/v1", exported["schema"])
        return self.run_cli(
            root,
            "changeset",
            "materialize",
            str(binary),
            "--root",
            str(root),
            "--journal",
            str(journal),
            "--write",
        )

    def test_all_lifecycle_commands_are_public(self) -> None:
        returncode, stdout, stderr = self.run_process(ROOT, "changeset", "--help")
        self.assertEqual(0, returncode, stderr)
        for command in (
            "prepare",
            "validate",
            "seal",
            "inspect",
            "export-json",
            "materialize",
            "recover",
        ):
            self.assertIn(command, stdout)

    def test_create_replace_rename_delete_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = b"created\n"
            replacement = b"one\nchanged\nthree\n"
            original = b"one\ntwo\nthree\n"
            source = root / "source.txt"
            source.write_bytes(original)
            renamed = root / "renamed.txt"
            receipt = self.prepare_and_materialize(
                root,
                [
                    {
                        "op": "create",
                        "path": "created.txt",
                        "after_sha256": digest(created),
                        "content_b64": encoded(created),
                    }
                ],
            )
            self.assertEqual("applied", receipt["status"])
            receipt = self.prepare_and_materialize(
                root,
                [
                    {
                        "op": "replace-range",
                        "path": "source.txt",
                        "before_sha256": digest(original),
                        "after_sha256": digest(replacement),
                        "content_b64": encoded(b"changed"),
                        "encoding": "utf-8",
                        "line_map": {"start_line": 2, "end_line": 2},
                    }
                ],
            )
            self.assertEqual("applied", receipt["status"])
            receipt = self.prepare_and_materialize(
                root,
                [
                    {
                        "op": "rename",
                        "path": "created.txt",
                        "dest": "renamed.txt",
                        "before_sha256": digest(created),
                        "after_sha256": digest(created),
                    }
                ],
            )
            self.assertEqual("applied", receipt["status"])
            receipt = self.prepare_and_materialize(
                root,
                [
                    {
                        "op": "delete",
                        "path": "renamed.txt",
                        "before_sha256": digest(created),
                    }
                ],
            )
            self.assertEqual("applied", receipt["status"])
            self.assertEqual(replacement, source.read_bytes())
            self.assertFalse(renamed.exists())
            replay = self.run_cli(
                root,
                "changeset",
                "materialize",
                str(root / "changeset.sfc"),
                "--root",
                str(root),
                "--journal",
                str(root / "changeset.journal"),
                "--write",
            )
            self.assertEqual("idempotent", replay["status"])
            recovered = self.run_cli(
                root,
                "changeset",
                "recover",
                str(root / "changeset.journal"),
                "--worktree-id",
                "worktree-1",
                "--lease-id",
                "lease-1",
                "--fencing-token",
                "fence-1",
            )
            self.assertEqual("valid", recovered["status"])
