from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_integrity_module():
    path = ROOT / "scripts" / "integrity_check.py"
    spec = importlib.util.spec_from_file_location("integrity_check", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_integrity_check_script_runs(capsys):
    mod = _load_integrity_module()
    code = mod.main(["--check"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "OK: no integrity drift" in out
