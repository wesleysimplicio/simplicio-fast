from __future__ import annotations

from pathlib import Path

from simplicio_fast.snapshot import Snapshot, Symbol, _build_v2


def test_snapshot_accepts_parser_test_symbol_kind(tmp_path: Path) -> None:
    output = tmp_path / "project.sfast"
    symbol = Symbol(
        "test_example",
        "test_example",
        "test",
        "tests/test_example.py",
        1,
        1,
        "0" * 64,
    )
    _build_v2(
        [("tests/test_example.py", b"\x00" * 32, 1, (symbol,))],
        (),
        output,
    )
    snapshot = Snapshot(output)
    try:
        assert any(symbol.kind == "test" for symbol in snapshot.symbols())
    finally:
        snapshot.close()
