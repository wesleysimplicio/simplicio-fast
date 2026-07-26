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
        self.assertFalse(payload["rollback"]["supported"])

    def test_rust_manifest_and_digest_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simplicio-fast-rs.exe"
            path.write_bytes(b"artifact")
            manifest = '{"schema":"simplicio.fast.engine-manifest/v1","engine":"rust","status":"available"}'
            completed = type("Completed", (), {"returncode": 0, "stdout": manifest})()
            with patch.dict(os.environ, {"SIMPLICIO_FAST_RUST": str(path)}, clear=True), patch(
                "simplicio_fast.installation.subprocess.run", return_value=completed
            ):
                payload = report()
        check = payload["checks"][2]
        self.assertEqual("pass", check["status"])
        self.assertEqual("rust", check["manifest"]["engine"])
        self.assertTrue(check["sha256"])
