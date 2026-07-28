from __future__ import annotations

from simplicio_fast.runtime_bridge import discover_native_binary, execute_via_bridge


def test_discover_never_uses_cargo(monkeypatch):
    monkeypatch.delenv("SIMPLICIO_FAST_NATIVE_BIN", raising=False)
    monkeypatch.delenv("SIMPLICIO_FAST_RUST", raising=False)
    monkeypatch.setattr("simplicio_fast.runtime_bridge.shutil.which", lambda _name: None)
    result = discover_native_binary()
    assert result["cargo_used"] is False
    assert result["status"] == "missing"
    assert result["reason_code"] == "RUST_UNAVAILABLE"


def test_execute_falls_back_to_python(monkeypatch):
    monkeypatch.setattr(
        "simplicio_fast.runtime_bridge.discover_native_binary",
        lambda: {
            "schema": "simplicio.fast.runtime-bridge/v1",
            "status": "missing",
            "reason_code": "RUST_UNAVAILABLE",
            "cargo_used": False,
        },
    )
    out = execute_via_bridge({"op": "capabilities"})
    assert out["status"] == "fallback"
    assert out["backend"] == "python"
    assert out["cargo_used"] is False
