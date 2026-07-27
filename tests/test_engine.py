import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from simplicio_fast.engine import (
    EngineSelection,
    EngineSelectionError,
    python_manifest,
    select_engine,
)
from simplicio_fast.cli import main


class EngineSelectionTest(unittest.TestCase):
    def test_auto_selects_python_with_explicit_rust_gap(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("simplicio_fast.engine.shutil.which", return_value=None):
                selection = select_engine("auto")
        self.assertEqual("python", selection.selected)
        self.assertEqual("rust_executable_missing", selection.reason)
        self.assertTrue(selection.manifest["reference"])

    def test_python_selection_never_probes_or_imports_rust_engine(self) -> None:
        with patch("simplicio_fast.engine.probe_rust", side_effect=AssertionError):
            selection = select_engine("off")
        self.assertEqual("off", selection.selected)
        self.assertEqual("explicitly_disabled", selection.reason)

    def test_explicit_rust_fails_closed_when_missing(self) -> None:
        with patch("simplicio_fast.engine.shutil.which", return_value=None):
            with self.assertRaises(EngineSelectionError) as raised:
                select_engine("rust")
        self.assertEqual("rust_executable_missing", raised.exception.receipt["reason"])
        self.assertEqual("unavailable", raised.exception.receipt["selected"])

    def test_probe_requires_conformance_before_auto_promotion(self) -> None:
        manifest = {
            "schema": "simplicio.fast.engine-manifest/v1",
            "engine": "rust",
            "status": "available",
            "conformance": {"passed": False},
        }
        with patch("simplicio_fast.engine._rust_executable", return_value="rust.exe"), patch(
            "simplicio_fast.engine.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(manifest)
            run.return_value.stderr = ""
            selection = select_engine("auto")
        self.assertEqual("python", selection.selected)
        self.assertEqual("rust_conformance_missing", selection.reason)

    def test_manifest_is_versioned_and_declares_reference_capabilities(self) -> None:
        manifest = python_manifest()
        self.assertEqual("simplicio.fast.engine-manifest/v1", manifest["schema"])
        self.assertIn("context", manifest["capabilities"])
        self.assertIn("apply", manifest["capabilities"])

    def test_cli_accepts_engine_selector_after_command(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "argv", ["simplicio-fast", "capabilities", "--fast-engine", "python"]), contextlib.redirect_stdout(output):
            main()
        payload = json.loads(output.getvalue())
        self.assertEqual("python", payload["engine"]["selected"])

    def test_cli_bridges_rust_query_without_importing_python_snapshot(self) -> None:
        output = io.StringIO()
        selection = EngineSelection(
            "rust",
            "rust",
            "rust_probe_passed",
            "simplicio-fast-rs",
            {"schema": "simplicio.fast.engine-manifest/v1", "engine": "rust", "status": "available"},
        )
        completed = subprocess.CompletedProcess(
            ["simplicio-fast-rs"],
            0,
            '{"schema":"simplicio.fast.query/v1","engine":"rust","matches":[{"name":"save"}]}',
            "",
        )
        with patch.object(sys, "argv", [
            "simplicio-fast", "query", "save", "--snapshot", "snapshot.sfast", "--fast-engine", "rust"
        ]), patch("simplicio_fast.cli.select_engine", return_value=selection), patch(
            "simplicio_fast.cli.subprocess.run", return_value=completed
        ) as run, contextlib.redirect_stdout(output):
            main()
        payload = json.loads(output.getvalue())
        self.assertEqual("simplicio.fast.query/v1", payload["schema"])
        self.assertEqual("rust", payload["engine"])
        self.assertEqual([{"name": "save"}], payload["matches"])
        run.assert_called_once()
        self.assertIn("--query", run.call_args.args[0])

    def test_cli_bridges_rust_context_with_bounded_limits(self) -> None:
        output = io.StringIO()
        selection = EngineSelection(
            "rust",
            "rust",
            "rust_probe_passed",
            "simplicio-fast-rs",
            {"schema": "simplicio.fast.engine-manifest/v1", "engine": "rust", "status": "available"},
        )
        completed = subprocess.CompletedProcess(
            ["simplicio-fast-rs"],
            0,
            '{"schema":"simplicio.fast.context/v1","engine":"rust","spans":[]}',
            "",
        )
        with patch.object(sys, "argv", [
            "simplicio-fast", "context", "save", "--root", ".", "--snapshot", "snapshot.sfast",
            "--max-results", "2", "--max-lines", "9", "--max-bytes", "100", "--max-tokens", "30",
            "--fast-engine", "rust"
        ]), patch("simplicio_fast.cli.select_engine", return_value=selection), patch(
            "simplicio_fast.cli.subprocess.run", return_value=completed
        ) as run, contextlib.redirect_stdout(output):
            main()
        payload = json.loads(output.getvalue())
        self.assertEqual("simplicio.fast.context/v1", payload["schema"])
        command = run.call_args.args[0]
        self.assertIn("--max-lines", command)
        self.assertIn("9", command)
