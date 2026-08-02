from pathlib import Path

from simplicio_fast.adapters import (
    discover_rust_projects,
    parse_path,
    rust_workspace_fingerprint,
)


def test_rust_adapter_covers_workspace_constructs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir()
    source.write_text(
        """use std::fmt::Display;
pub mod model;
pub struct Item<T> { value: T }
pub enum State { Ready, Done }
pub trait Render { fn render(&self); }
impl<T> Render for Item<T> { fn render(&self) {} }
pub type Id = u64;
pub const LIMIT: usize = 4;
pub static mut CACHE: usize = 0;
macro_rules! make_item { () => {} }
pub async fn load() {}
""",
        encoding="utf-8",
    )

    symbols = parse_path(source, "src/lib.rs")
    names_by_kind = {(symbol.name, symbol.kind) for symbol in symbols}

    assert ("std::fmt::Display", "import") in names_by_kind
    assert {
        "model",
        "Item",
        "State",
        "Render",
        "Item",
        "Id",
        "LIMIT",
        "CACHE",
        "make_item",
        "load",
    } <= {symbol.name for symbol in symbols}
    assert {"namespace", "struct", "enum", "trait", "function", "import"} <= {
        symbol.kind for symbol in symbols
    }


def test_rust_workspace_discovery_excludes_generated_and_vendor(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers=[]\n", encoding="utf-8")
    (tmp_path / "crates" / "core").mkdir(parents=True)
    (tmp_path / "crates" / "core" / "Cargo.toml").write_text(
        "[package]\nname='core'\nversion='0.1.0'\n", encoding="utf-8"
    )
    for directory in (tmp_path / "target", tmp_path / "vendor" / "dep"):
        directory.mkdir(parents=True)
        (directory / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    assert [
        path.relative_to(tmp_path).as_posix()
        for path in discover_rust_projects(tmp_path)
    ] == [
        "Cargo.toml",
        "crates/core/Cargo.toml",
    ]


def test_rust_workspace_fingerprint_changes_with_cargo_inputs(tmp_path: Path) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[workspace]\nmembers=[]\n", encoding="utf-8")
    first = rust_workspace_fingerprint(tmp_path)
    manifest.write_text("[workspace]\nmembers=['crate']\n", encoding="utf-8")
    second = rust_workspace_fingerprint(tmp_path)
    assert first != second


def test_rust_workspace_fingerprint_binds_toolchain_and_cargo_config(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text("[package]\nname='app'\nversion='0.1.0'\n", encoding="utf-8")
    toolchain = tmp_path / "rust-toolchain.toml"
    toolchain.write_text('[toolchain]\nchannel="stable"\n', encoding="utf-8")
    cargo_config = tmp_path / ".cargo" / "config.toml"
    cargo_config.parent.mkdir()
    cargo_config.write_text("[build]\nrustflags=[]\n", encoding="utf-8")

    baseline = rust_workspace_fingerprint(tmp_path)
    toolchain.write_text('[toolchain]\nchannel="1.85.0"\n', encoding="utf-8")
    changed_toolchain = rust_workspace_fingerprint(tmp_path)
    assert changed_toolchain != baseline

    cargo_config.write_text("[build]\nrustflags=[\"-Ctarget-cpu=native\"]\n", encoding="utf-8")
    changed_config = rust_workspace_fingerprint(tmp_path)
    assert changed_config != changed_toolchain


def test_rust_multi_crate_workspace_discovers_and_parses_each_member(
    tmp_path: Path,
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        "[workspace]\nmembers=['crates/core','crates/app']\n",
        encoding="utf-8",
    )
    core = tmp_path / "crates" / "core"
    app = tmp_path / "crates" / "app"
    for crate in (core, app):
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text(
            f"[package]\nname='{crate.name}'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
    (core / "src" / "lib.rs").write_text(
        "pub trait Render { fn render(&self); }\n"
        "pub struct Item;\n"
        "impl Render for Item {\n"
        "    fn render(&self) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (app / "src" / "main.rs").write_text(
        "use core::Item;\n"
        "fn main() { let _item = Item; }\n",
        encoding="utf-8",
    )

    manifests = [path.relative_to(tmp_path).as_posix() for path in discover_rust_projects(tmp_path)]
    assert manifests == ["Cargo.toml", "crates/app/Cargo.toml", "crates/core/Cargo.toml"]
    core_symbols = parse_path(core / "src" / "lib.rs", "crates/core/src/lib.rs")
    app_symbols = parse_path(app / "src" / "main.rs", "crates/app/src/main.rs")
    assert {symbol.name for symbol in core_symbols} >= {"Render", "Item", "render"}
    assert {symbol.name for symbol in app_symbols} >= {"core::Item", "main"}
