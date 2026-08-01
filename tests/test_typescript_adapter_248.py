from pathlib import Path

from simplicio_fast.adapters import discover_typescript_projects, parse_path


def test_typescript_adapter_covers_modules_types_and_tests(tmp_path: Path) -> None:
    source = tmp_path / "component.tsx"
    source.write_text(
        """import { useState } from 'react';\nexport type UserId = string;\nexport enum State { Ready }\nexport interface User { id: UserId; }\nexport class Store {\n  value: string;\n  async load<T>(): Promise<T> { return null as T; }\n}\ntest('loads users', () => {});\n""",
        encoding="utf-8",
    )
    kinds = {
        (symbol.name, symbol.kind) for symbol in parse_path(source, "component.tsx")
    }
    assert ("react", "import") in kinds
    assert ("UserId", "type") in kinds
    assert ("State", "enum") in kinds
    assert ("User", "interface") in kinds
    assert ("Store", "class") in kinds
    assert ("value", "property") in kinds
    assert ("load", "function") in kinds
    assert ("loads users", "test") in kinds


def test_typescript_project_discovery_excludes_dependencies(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    dependencies = tmp_path / "node_modules"
    dependencies.mkdir()
    (dependencies / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert [path.name for path in discover_typescript_projects(tmp_path)] == [
        "package.json",
        "tsconfig.json",
    ]
