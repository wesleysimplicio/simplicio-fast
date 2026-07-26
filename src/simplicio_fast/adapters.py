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
    return [negotiate(language) for language in ("python", "typescript", "rust", "csharp")]


def language_for_path(path: Path) -> str | None:
    return SUPPORTED_EXTENSIONS.get(path.suffix.casefold())


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
            ("import", "import", re.compile(r"^\s*import\s+(?:type\s+)?(?:.+?from\s+)?[\"']([^\"']+)[\"']")),
            ("namespace", "namespace", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?namespace\s+(\w+)")),
            ("interface", "interface", re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)")),
            ("class", "class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")),
            ("function", "function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
            ("function", "function", re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(")),
        ]
    elif language == "rust":
        patterns = [
            ("use", "import", re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+)")),
            ("mod", "namespace", re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)")),
            ("struct", "struct", re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)")),
            ("trait", "trait", re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)")),
            ("enum", "enum", re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)")),
            ("function", "function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
        ]
    else:
        patterns = [
            ("using", "import", re.compile(r"^\s*using\s+(?:static\s+)?([^;=]+)")),
            ("namespace", "namespace", re.compile(r"^\s*namespace\s+([\w.]+)")),
            ("interface", "interface", re.compile(r"^\s*(?:public\s+)?interface\s+(\w+)")),
            ("class", "class", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+)?(?:abstract\s+)?class\s+(\w+)")),
            ("struct", "struct", re.compile(r"^\s*(?:public\s+)?struct\s+(\w+)")),
            ("function", "function", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+)?(?:static\s+)?[\w<>?\[\]]+\s+(\w+)\s*\([^;]*\)")),
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
