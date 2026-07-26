from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .integrations import run_dev_cli_changeset, run_mapper
from .snapshot import ContextSpan, Snapshot, build_snapshot

STOP_WORDS = {
    "a", "an", "and", "as", "at", "build", "change", "create", "do", "for", "from",
    "implement", "in", "into", "of", "on", "or", "the", "to", "update", "with",
    "criar", "de", "do", "da", "e", "em", "implementar", "o", "os", "para", "por", "um", "uma",
}


@dataclass(frozen=True, slots=True)
class Understanding:
    schema: str
    task: str
    terms: list[str]
    files: list[str]
    symbols: list[str]
    context: list[ContextSpan]


@dataclass(frozen=True, slots=True)
class PlanNode:
    id: str
    kind: str
    depends_on: list[str]
    inputs: dict[str, Any]
    acceptance: list[str]


def task_terms(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task.casefold())
    return list(dict.fromkeys(word for word in words if word not in STOP_WORDS))


class ProjectProcessor:
    def __init__(self, root: Path, snapshot_path: Path) -> None:
        self.root = root.resolve()
        self.snapshot_path = snapshot_path

    def ingest(self) -> dict[str, Any]:
        mapper = run_mapper(self.root)
        return {
            "schema": "simplicio.fast.ingest/v2",
            "snapshot": str(self.snapshot_path),
            "mapper": mapper or {
                "adapter": "internal-bootstrap",
                "status": "fallback",
                "reason": "simplicio-mapper is not installed",
            },
            "metrics": asdict(build_snapshot(self.root, self.snapshot_path)),
        }

    def understand(
        self,
        task: str,
        *,
        max_results: int = 12,
        max_bytes: int = 48_000,
    ) -> Understanding:
        if not self.snapshot_path.exists():
            self.ingest()
        terms = task_terms(task)
        contexts: list[ContextSpan] = []
        seen: set[tuple[str, int, int]] = set()
        remaining = max_bytes
        with Snapshot(self.snapshot_path) as snapshot:
            symbols = snapshot.symbols()
            for term in terms:
                if remaining <= 0 or len(contexts) >= max_results:
                    break
                matches = snapshot.context(
                    self.root,
                    term,
                    max_results=max_results - len(contexts),
                    max_bytes=remaining,
                )
                for match in matches:
                    key = (match.file, match.start_line, match.end_line)
                    if key in seen:
                        continue
                    seen.add(key)
                    contexts.append(match)
                    remaining -= len(match.content.encode())
                    if remaining <= 0 or len(contexts) >= max_results:
                        break
            if not contexts:
                ranked = sorted(
                    symbols,
                    key=lambda item: (
                        0 if item.kind == "class" else 1,
                        len(item.qualified_name),
                        item.file,
                    ),
                )
                for symbol in ranked[: min(max_results, 5)]:
                    for match in snapshot.context(
                        self.root,
                        symbol.qualified_name,
                        max_results=1,
                        max_bytes=max(1, remaining),
                    ):
                        key = (match.file, match.start_line, match.end_line)
                        if key not in seen:
                            seen.add(key)
                            contexts.append(match)
                            remaining -= len(match.content.encode())
        return Understanding(
            schema="simplicio.fast.understanding/v2",
            task=task,
            terms=terms,
            files=sorted({item.file for item in contexts}),
            symbols=[item.symbol for item in contexts],
            context=contexts,
        )

    def plan(self, task: str, *, max_bytes: int = 48_000) -> dict[str, Any]:
        understanding = self.understand(task, max_bytes=max_bytes)
        source_hashes = {
            item.file: item.source_sha256 for item in understanding.context
        }
        validation = self._validation_commands()
        nodes = [
            PlanNode(
                "orient",
                "context",
                [],
                {
                    "task": task,
                    "files": understanding.files,
                    "symbols": understanding.symbols,
                    "source_hashes": source_hashes,
                },
                ["context spans are current and bounded"],
            ),
            PlanNode(
                "modify",
                "structured_patch",
                ["orient"],
                {
                    "allowed_files": understanding.files,
                    "required_hashes": source_hashes,
                    "format": "simplicio.fast.changeset/v2",
                },
                ["normal source files contain the requested behavior", "all hash guards pass"],
            ),
            PlanNode(
                "validate",
                "command_gate",
                ["modify"],
                {"commands": validation},
                ["all configured validation commands exit successfully"],
            ),
            PlanNode(
                "refresh",
                "snapshot_refresh",
                ["validate"],
                {"snapshot": str(self.snapshot_path)},
                ["changed files are visible in the next snapshot generation"],
            ),
        ]
        return {
            "schema": "simplicio.fast.plandag/v2",
            "task": task,
            "root": str(self.root),
            "understanding": asdict(understanding),
            "nodes": [asdict(node) for node in nodes],
        }

    def _validation_commands(self) -> list[list[str]]:
        commands: list[list[str]] = []
        if (self.root / "pyproject.toml").exists() or (self.root / "tests").exists():
            commands.append(
                ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
            )
        if (self.root / "package.json").exists():
            commands.append(["npm", "test"])
        if (self.root / "Cargo.toml").exists():
            commands.append(["cargo", "test"])
        if not commands:
            commands.append(["python", "-m", "compileall", "-q", "."])
        return commands

    def apply_changeset(self, changeset: dict[str, Any], *, write: bool) -> dict[str, Any]:
        if changeset.get("schema") != "simplicio.fast.changeset/v2":
            raise ValueError("unsupported changeset schema")
        changes = changeset.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("changeset must contain at least one change")
        delegated = run_dev_cli_changeset(self.root, changeset, write=write)
        if delegated is not None:
            status = delegated["result"].get("status")
            if status != "ok":
                raise ValueError(f"simplicio-dev-cli refused changeset: {delegated['result']}")
            return {
                "schema": "simplicio.fast.apply-receipt/v2",
                "mode": "write" if write else "dry-run",
                "executor": delegated,
                "files": delegated["result"].get("files", []),
            }
        prepared: list[tuple[Path, str, str, int]] = []
        for change in changes:
            relative = change.get("path")
            expected = change.get("expected_sha256")
            replacements = change.get("replacements")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ValueError("each change requires path and expected_sha256")
            path = (self.root / relative).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise ValueError(f"change path escapes root: {relative}") from error
            original = path.read_bytes()
            actual = hashlib.sha256(original).hexdigest()
            if actual != expected:
                raise ValueError(f"stale source hash for {relative}")
            if not isinstance(replacements, list) or not replacements:
                raise ValueError(f"change requires replacements: {relative}")
            lines = original.decode("utf-8").splitlines(keepends=True)
            normalized: list[tuple[int, int, str]] = []
            for replacement in replacements:
                start = replacement.get("start_line")
                end = replacement.get("end_line")
                content = replacement.get("content")
                if (
                    not isinstance(start, int)
                    or not isinstance(end, int)
                    or not isinstance(content, str)
                    or start < 1
                    or end < start
                    or end > len(lines)
                ):
                    raise ValueError(f"invalid line replacement for {relative}")
                normalized.append((start, end, content))
            normalized.sort(reverse=True)
            for index, (start, end, content) in enumerate(normalized):
                if index and end >= normalized[index - 1][0]:
                    raise ValueError(f"overlapping replacements for {relative}")
                suffix = "\n" if content and not content.endswith("\n") else ""
                lines[start - 1 : end] = [content + suffix]
            updated = "".join(lines)
            prepared.append((path, relative, updated, len(replacements)))
        receipt = {
            "schema": "simplicio.fast.apply-receipt/v2",
            "mode": "write" if write else "dry-run",
            "executor": {
                "adapter": "internal-bootstrap",
                "status": "fallback",
                "reason": "simplicio-dev-cli is not installed",
            },
            "files": [
                {
                    "path": relative,
                    "replacements": count,
                    "result_sha256": hashlib.sha256(updated.encode()).hexdigest(),
                }
                for _, relative, updated, count in prepared
            ],
        }
        if write:
            temporary: list[tuple[Path, Path]] = []
            try:
                for path, _, updated, _ in prepared:
                    temp = path.with_suffix(f"{path.suffix}.{os.getpid()}.simplicio-fast")
                    temp.write_text(updated, encoding="utf-8")
                    temporary.append((temp, path))
                for temp, path in temporary:
                    temp.replace(path)
            finally:
                for temp, _ in temporary:
                    temp.unlink(missing_ok=True)
        return receipt


def load_changeset(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("changeset root must be an object")
    return value
