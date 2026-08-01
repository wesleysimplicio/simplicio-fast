"""Language capability negotiation and conservative semantic adapters.

The adapters intentionally return the public :class:`Symbol` contract only.  They
do not expose binary offsets, so Mapper remains the owner of public ContextGraph
handles while Fast owns extraction and persistence.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .snapshot import Symbol


@dataclass(frozen=True, slots=True)
class AdapterCapability:
    language: str
    status: str
    parser: str
    reason: str | None = None
    fallback: str | None = None


SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".rs": "rust",
    ".cs": "csharp",
}


def negotiate(language: str) -> AdapterCapability:
    normalized = language.casefold().replace("c#", "csharp").replace("ts", "typescript")
    if normalized == "python":
        return AdapterCapability("python", "available", "python-ast")
    if normalized in {"typescript", "rust", "csharp"}:
        # Tree-sitter/compiler bindings are optional.  The deterministic lexical
        # adapter is explicit so callers can distinguish it from native parsing.
        return AdapterCapability(
            normalized,
            "fallback",
            "lexical",
            reason="native parser binding unavailable",
            fallback="bounded lexical extraction; verify with native toolchain",
        )
    return AdapterCapability(
        normalized,
        "unavailable",
        "none",
        reason=f"no adapter registered for {language}",
        fallback="preserve source and request a Mapper-native capability",
    )


def capability_report() -> list[AdapterCapability]:
    return [
        negotiate(language) for language in ("python", "typescript", "rust", "csharp")
    ]


def language_for_path(path: Path) -> str | None:
    return SUPPORTED_EXTENSIONS.get(path.suffix.casefold())


def discover_typescript_projects(root: Path) -> list[Path]:
    """Return deterministic TypeScript workspace/config inputs."""
    names = {
        "tsconfig.json",
        "jsconfig.json",
        "package.json",
        "package-lock.json",
        "pnpm-workspace.yaml",
    }
    ignored = {".git", ".simplicio", "node_modules", "dist", "build", "coverage"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name in names
            or path.name.startswith("tsconfig.")
            or path.name.startswith("jsconfig.")
        )
        and not any(part in ignored for part in path.parts)
    )


def discover_csharp_projects(root: Path) -> list[Path]:
    """Return deterministic .NET solution/project/config inputs."""
    names = {
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
    }
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.suffix.casefold() in {".sln", ".slnx", ".csproj"} or path.name in names
        )
        and not any(part in {".git", ".simplicio", "bin", "obj"} for part in path.parts)
    )


def parse_path(path: Path, relative_path: str | None = None) -> list[Symbol]:
    relative = relative_path or path.as_posix()
    language = language_for_path(path)
    if language is None:
        return []
    if language == "python":
        return _parse_python(path, relative)
    return _parse_lexical(path, relative, language)


def _parse_python(path: Path, relative: str) -> list[Symbol]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    result: list[Symbol] = []
    scopes: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "async_function"
                if isinstance(node, ast.AsyncFunctionDef)
                else "function"
            )
            qualified = ".".join([*scopes, node.name])
            result.append(
                Symbol(
                    node.name,
                    qualified,
                    kind,
                    relative,
                    node.lineno,
                    getattr(node, "end_lineno", None) or node.lineno,
                )
            )
            scopes.append(node.name)
            for child in ast.iter_child_nodes(node):
                visit(child)
            scopes.pop()
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return result


def _parse_lexical(path: Path, relative: str, language: str) -> list[Symbol]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    patterns: list[tuple[str, str, re.Pattern[str]]] = []
    if language == "typescript":
        patterns = [
            (
                "import",
                "import",
                re.compile(
                    r"^\s*import\s+(?:type\s+)?(?:.+?from\s+)?[\"']([^\"']+)[\"']"
                ),
            ),
            (
                "type",
                "type",
                re.compile(r"^\s*(?:export\s+)?type\s+(\w+)"),
            ),
            (
                "enum",
                "enum",
                re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+(\w+)"),
            ),
            (
                "namespace",
                "namespace",
                re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?namespace\s+(\w+)"),
            ),
            (
                "interface",
                "interface",
                re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)"),
            ),
            (
                "class",
                "class",
                re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
            ),
            (
                "function",
                "function",
                re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
            ),
            (
                "function",
                "function",
                re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\("),
            ),
            (
                "method",
                "function",
                re.compile(
                    r"^\s*(?:public|private|protected|static|async|readonly)?\s*(\w+)\s*<[^>]+>\s*\("
                ),
            ),
            (
                "property",
                "property",
                re.compile(
                    r"^\s*(?:public|private|protected|readonly|static)?\s*(\w+)\??\s*:\s*[^=;]+[;=]"
                ),
            ),
            (
                "test",
                "test",
                re.compile(r"^\s*(?:describe|it|test)\s*\(\s*[\"']([^\"']+)"),
            ),
        ]
    elif language == "rust":
        patterns = [
            ("use", "import", re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+)")),
            ("mod", "namespace", re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)")),
            ("struct", "struct", re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)")),
            ("trait", "trait", re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)")),
            ("enum", "enum", re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)")),
            (
                "function",
                "function",
                re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
            ),
        ]
    else:
        patterns = [
            ("attribute", "attribute", re.compile(r"^\s*\[\s*([\w.]+)")),
            (
                "using",
                "import",
                re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([^;=]+)"),
            ),
            (
                "namespace",
                "namespace",
                re.compile(r"^\s*(?:file\s+)?namespace\s+([\w.]+)"),
            ),
            (
                "delegate",
                "delegate",
                re.compile(
                    r"^\s*(?:public\s+|internal\s+|private\s+)?delegate\s+[^\s]+\s+(\w+)"
                ),
            ),
            (
                "interface",
                "interface",
                re.compile(
                    r"^\s*(?:public\s+|internal\s+|private\s+|partial\s+)*interface\s+(\w+)"
                ),
            ),
            (
                "record",
                "record",
                re.compile(
                    r"^\s*(?:public\s+|internal\s+|private\s+|partial\s+)*(?:record\s+(?:class\s+|struct\s+)?)(\w+)"
                ),
            ),
            (
                "class",
                "class",
                re.compile(
                    r"^\s*(?:public\s+|internal\s+|private\s+|partial\s+)*(?:abstract\s+)?class\s+(\w+)"
                ),
            ),
            (
                "struct",
                "struct",
                re.compile(
                    r"^\s*(?:public\s+|internal\s+|private\s+|partial\s+)*struct\s+(\w+)"
                ),
            ),
            (
                "enum",
                "enum",
                re.compile(r"^\s*(?:public\s+|internal\s+|private\s+)*enum\s+(\w+)"),
            ),
            (
                "event",
                "event",
                re.compile(
                    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*event\s+[^\s]+\s+(\w+)"
                ),
            ),
            (
                "property",
                "property",
                re.compile(
                    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|virtual\s+|override\s+|required\s+)*[\w<>?,.\[\]]+\s+(\w+)\s*\{\s*(?:get|set|init)"
                ),
            ),
            (
                "constructor",
                "constructor",
                re.compile(
                    r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+|static\s+|async\s+)*([A-Z]\w*)\s*\([^;]*\)"
                ),
            ),
            (
                "method",
                "function",
                re.compile(
                    r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+|static\s+|async\s+|virtual\s+|override\s+|partial\s+)*[\w<>?,.\[\]]+\s+(\w+)\s*\([^;]*\)"
                ),
            ),
            (
                "field",
                "field",
                re.compile(
                    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|readonly\s+|const\s+)+[\w<>?,.\[\]]+\s+(\w+)\s*(?:=|;)"
                ),
            ),
        ]
    result: list[Symbol] = []
    for index, line in enumerate(lines, 1):
        for _, kind, pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            name = match.group(1).strip()
            if kind == "import":
                name = name.replace(" ", "")
            qualified = name
            result.append(Symbol(name, qualified, kind, relative, index, index))
            break
    return result
