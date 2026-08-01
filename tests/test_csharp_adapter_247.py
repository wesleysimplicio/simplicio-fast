from pathlib import Path

from simplicio_fast.adapters import (
    csharp_workspace_fingerprint,
    discover_csharp_projects,
    parse_path,
)


def test_csharp_adapter_covers_declared_constructs(tmp_path: Path) -> None:
    source = tmp_path / "Service.cs"
    source.write_text(
        """[Obsolete]\nnamespace Demo;\npublic partial record User<T> {\n    public string Name { get; set; }\n    public event EventHandler Changed;\n    public User() {}\n    public async Task SaveAsync() { }\n}\npublic interface IStore {}\npublic delegate void Handler();\npublic enum State { Ready }\n""",
        encoding="utf-8",
    )
    kinds = {(symbol.name, symbol.kind) for symbol in parse_path(source, "Service.cs")}
    assert ("Obsolete", "attribute") in kinds
    assert ("User", "record") in kinds
    assert ("Name", "property") in kinds
    assert ("Changed", "event") in kinds
    assert ("User", "constructor") in kinds
    assert ("SaveAsync", "function") in kinds
    assert ("IStore", "interface") in kinds
    assert ("Handler", "delegate") in kinds
    assert ("State", "enum") in kinds


def test_csharp_project_discovery_excludes_build_output(tmp_path: Path) -> None:
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "Directory.Build.props").write_text("<Project />", encoding="utf-8")
    output = tmp_path / "bin"
    output.mkdir()
    (output / "Generated.csproj").write_text("<Project />", encoding="utf-8")
    assert [path.name for path in discover_csharp_projects(tmp_path)] == [
        "App.csproj",
        "Directory.Build.props",
    ]


def test_csharp_workspace_fingerprint_changes_with_project_inputs(tmp_path: Path) -> None:
    project = tmp_path / "App.csproj"
    project.write_text("<Project />", encoding="utf-8")
    first = csharp_workspace_fingerprint(tmp_path)
    project.write_text("<Project><PropertyGroup /></Project>", encoding="utf-8")
    second = csharp_workspace_fingerprint(tmp_path)
    assert first != second
