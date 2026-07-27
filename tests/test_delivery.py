from __future__ import annotations

import tempfile
import unittest
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from simplicio_fast.cli import main
from simplicio_fast.delivery import DeliveryEngine
from simplicio_fast.engine import select_engine
from simplicio_fast.snapshot import build_snapshot


class DeliveryEngineTest(unittest.TestCase):
    def test_prepare_emits_receipt_and_second_attempt_hits_l0_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def create_user(name):\n    return name\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            engine = DeliveryEngine(root, snapshot)
            selection = select_engine("python").receipt()
            first = engine.prepare("understand create_user and validate tests", profile="loop-standalone", engine_receipt=selection)
            second = engine.prepare("understand create_user and validate tests", profile="loop-standalone", engine_receipt=selection)
            self.assertEqual("simplicio.fast.delivery-engine/v1", first["schema"])
            self.assertEqual("miss", first["cache"]["L0_attempt"])
            self.assertEqual("hit", second["cache"]["L0_attempt"])
            self.assertFalse(first["ownership"]["mutation_applied"])

    def test_cache_stats_reports_disposable_cache_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            engine = DeliveryEngine(root, snapshot)
            self.assertEqual(
                {"schema": "simplicio.fast.delivery-cache/v1", "entries": 0, "bytes": 0},
                engine.cache_stats(),
            )
            engine.prepare("validate ping", profile="loop-standalone", engine_receipt=select_engine("python").receipt())
            stats = engine.cache_stats()
            self.assertEqual("simplicio.fast.delivery-cache/v1", stats["schema"])
            self.assertEqual(1, stats["entries"])
            self.assertGreater(stats["bytes"], 0)

    def test_full_profile_records_runtime_authority_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            receipt = DeliveryEngine(root, snapshot).prepare(
                "validate ping",
                profile="full",
                engine_receipt=select_engine("python").receipt(),
            )
            self.assertEqual("simplicio-runtime", receipt["ownership"]["full_effect_authority"])
            self.assertFalse(receipt["ownership"]["mutation_applied"])

    def test_cli_delivery_is_a_system_receipt_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            output = io.StringIO()
            argv = ["simplicio-fast", "delivery", "validate ping", "--root", str(root), "--snapshot", str(snapshot), "--fast-engine", "python"]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                main()
            self.assertEqual("simplicio.fast.delivery-engine/v1", json.loads(output.getvalue())["schema"])

    def test_loop_delivery_dry_run_write_and_idempotent_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            changeset = {
                "schema": "simplicio.fast.changeset/v2",
                "changes": [
                    {
                        "path": "service.py",
                        "expected_sha256": expected,
                        "replacements": [{"start_line": 2, "end_line": 2, "content": "    return False"}],
                    }
                ],
            }
            selection = select_engine("python").receipt()
            engine = DeliveryEngine(root, snapshot)
            with patch("simplicio_fast.processor.run_dev_cli_changeset", return_value=None):
                dry_run = engine.deliver(changeset, profile="loop-standalone", engine_receipt=selection)
                self.assertEqual("dry_run", dry_run["status"])
                self.assertTrue(dry_run["apply"]["no_write_proof"])
                self.assertIn("return True", source.read_text(encoding="utf-8"))
                applied = engine.deliver(changeset, profile="loop-standalone", engine_receipt=selection, write=True)
            self.assertEqual("applied", applied["status"])
            self.assertTrue(applied["ownership"]["mutation_applied"])
            self.assertEqual("    return False", source.read_text(encoding="utf-8").splitlines()[1])
            retry = engine.deliver(changeset, profile="loop-standalone", engine_receipt=selection, write=True)
            self.assertEqual("hit", retry["cache"]["L0_delivery"])
            self.assertTrue(retry["idempotency"]["replayed"])

    def test_cli_delivery_changeset_uses_guarded_executor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            changeset = root / "changeset.json"
            changeset.write_text(
                json.dumps(
                    {
                        "schema": "simplicio.fast.changeset/v2",
                        "changes": [
                            {
                                "path": "service.py",
                                "expected_sha256": expected,
                                "replacements": [{"start_line": 2, "end_line": 2, "content": "    return False"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            argv = [
                "simplicio-fast",
                "delivery",
                "apply ping",
                "--root",
                str(root),
                "--snapshot",
                str(snapshot),
                "--changeset",
                str(changeset),
                "--write",
                "--fast-engine",
                "python",
            ]
            with patch("simplicio_fast.processor.run_dev_cli_changeset", return_value=None):
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                    main()
            payload = json.loads(output.getvalue())
            self.assertEqual("applied", payload["status"])
            self.assertEqual("simplicio.fast.delivery-engine/v1", payload["schema"])

    def test_full_write_fails_closed_without_runtime_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def ping():\n    return True\n", encoding="utf-8")
            snapshot = root / "project.sfast"
            build_snapshot(root, snapshot)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            changeset = {
                "schema": "simplicio.fast.changeset/v2",
                "changes": [{"path": "service.py", "expected_sha256": expected, "replacements": [{"start_line": 2, "end_line": 2, "content": "    return False"}]}],
            }
            receipt = DeliveryEngine(root, snapshot).deliver(
                changeset, profile="full", engine_receipt=select_engine("python").receipt(), write=True
            )
            self.assertEqual("blocked", receipt["status"])
            self.assertIn("runtime_authorization_required", receipt["reason_codes"])
            self.assertIn("return True", source.read_text(encoding="utf-8"))
