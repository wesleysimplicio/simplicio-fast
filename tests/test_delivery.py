from __future__ import annotations

import tempfile
import unittest
import contextlib
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
