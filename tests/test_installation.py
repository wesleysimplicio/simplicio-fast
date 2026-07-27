from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simplicio_fast.installation import report


class InstallationReportTest(unittest.TestCase):
    def test_python_only_report_is_ready_without_rust_or_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("simplicio_fast.installation.shutil.which", return_value=None):
            payload = report()
        self.assertEqual("simplicio.fast.installation/v1", payload["schema"])
        self.assertEqual("ready", payload["status"])
        self.assertEqual("pass", payload["checks"][1]["status"])
        self.assertEqual("artifact_missing", payload["checks"][2]["reason"])
        self.assertEqual("python", payload["resolution"]["selected_engine"])
        self.assertEqual("rust_artifact_missing", payload["resolution"]["reason_code"])
        self.assertFalse(payload["rollback"]["supported"])

    def test_rust_manifest_and_digest_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simplicio-fast-rs.exe"
            path.write_bytes(b"artifact")
            manifest = '{"schema":"simplicio.fast.engine-manifest/v1","engine":"rust","status":"available","version":"2.0.9"}'
            completed = type("Completed", (), {"returncode": 0, "stdout": manifest})()
            with patch.dict(os.environ, {"SIMPLICIO_FAST_RUST": str(path)}, clear=True), patch(
                "simplicio_fast.installation.subprocess.run", return_value=completed
            ):
                payload = report()
        check = payload["checks"][2]
        self.assertEqual("pass", check["status"])
        self.assertEqual("rust", check["manifest"]["engine"])
        self.assertTrue(check["sha256"])
        self.assertEqual("rust", payload["resolution"]["selected_engine"])
        self.assertEqual("rust_manifest_available", payload["resolution"]["reason_code"])

    def test_missing_rust_manifest_version_is_degraded_and_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simplicio-fast-rs.exe"
            path.write_bytes(b"artifact")
            completed = type(
                "Completed", (), {"returncode": 0, "stdout": '{\"schema\":\"simplicio.fast.engine-manifest/v1\",\"engine\":\"rust\",\"status\":\"available\"}'}
            )()
            with patch.dict(os.environ, {"SIMPLICIO_FAST_RUST": str(path)}, clear=True), patch(
                "simplicio_fast.installation.subprocess.run", return_value=completed
            ):
                payload = report()
        check = payload["checks"][2]
        self.assertEqual("degraded", payload["status"])
        self.assertEqual("manifest_version_missing", check["reason"])
        self.assertEqual("python", payload["resolution"]["selected_engine"])

    def test_divergent_rust_manifest_version_is_degraded_and_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simplicio-fast-rs.exe"
            path.write_bytes(b"artifact")
            completed = type(
                "Completed", (), {"returncode": 0, "stdout": '{\"schema\":\"simplicio.fast.engine-manifest/v1\",\"engine\":\"rust\",\"status\":\"available\",\"version\":\"9.9.9\"}'}
            )()
            with patch.dict(os.environ, {"SIMPLICIO_FAST_RUST": str(path)}, clear=True), patch(
                "simplicio_fast.installation.subprocess.run", return_value=completed
            ):
                payload = report()
        check = payload["checks"][2]
        self.assertEqual("degraded", payload["status"])
        self.assertEqual("manifest_version_mismatch", check["reason"])
        self.assertEqual("python", payload["resolution"]["selected_engine"])

    def test_incompatible_rust_manifest_is_degraded_and_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simplicio-fast-rs.exe"
            path.write_bytes(b"artifact")
            completed = type(
                "Completed", (), {"returncode": 0, "stdout": '{"schema":"wrong"}' }
            )()
            with patch.dict(os.environ, {"SIMPLICIO_FAST_RUST": str(path)}, clear=True), patch(
                "simplicio_fast.installation.subprocess.run", return_value=completed
            ):
                payload = report()
        check = payload["checks"][2]
        self.assertEqual("degraded", payload["status"])
        self.assertEqual("fail", check["status"])
        self.assertEqual("manifest_schema_mismatch", check["reason"])
        self.assertEqual("python", payload["resolution"]["selected_engine"])
        self.assertEqual("rust_artifact_unusable:manifest_schema_mismatch", payload["resolution"]["reason_code"])
