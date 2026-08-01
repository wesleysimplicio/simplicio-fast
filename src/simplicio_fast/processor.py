from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .integrations import run_dev_cli_changeset, run_mapper
from .snapshot import ContextSpan, Snapshot, build_snapshot
from .snapshot import DEFAULT_MAX_SOURCE_FILE_BYTES

STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "build",
    "change",
    "create",
    "do",
    "for",
    "from",
    "implement",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "update",
    "with",
    "criar",
    "de",
    "do",
    "da",
    "e",
    "em",
    "implementar",
    "o",
    "os",
    "para",
    "por",
    "um",
    "uma",
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


@dataclass(frozen=True, slots=True)
class PreparedChange:
    path: Path
    relative: str
    expected_sha256: str
    original: bytes
    updated: bytes
    replacements: int


def task_terms(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task.casefold())
    return list(dict.fromkeys(word for word in words if word not in STOP_WORDS))


class ProjectProcessor:
    def __init__(self, root: Path, snapshot_path: Path) -> None:
        self.root = root.resolve()
        self.snapshot_path = snapshot_path

    def ingest(
        self,
        *,
        timeout_seconds: float | None = None,
        max_file_bytes: int = DEFAULT_MAX_SOURCE_FILE_BYTES,
    ) -> dict[str, Any]:
        mapper = run_mapper(self.root)
        return {
            "schema": "simplicio.fast.ingest/v2",
            "snapshot": str(self.snapshot_path),
            "mapper": mapper
            or {
                "adapter": "internal-bootstrap",
                "status": "fallback",
                "reason": "simplicio-mapper is not installed",
            },
            "metrics": asdict(
                build_snapshot(
                    self.root,
                    self.snapshot_path,
                    timeout_seconds=timeout_seconds,
                    max_file_bytes=max_file_bytes,
                )
            ),
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
                [
                    "normal source files contain the requested behavior",
                    "all hash guards pass",
                ],
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

    def apply_changeset(
        self, changeset: dict[str, Any], *, write: bool
    ) -> dict[str, Any]:
        if changeset.get("schema") != "simplicio.fast.changeset/v2":
            raise ValueError("unsupported changeset schema")
        changes = changeset.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("changeset must contain at least one change")
        prepared = self._prepare_changes(changes)
        before = self._file_records(prepared)
        try:
            delegated = run_dev_cli_changeset(self.root, changeset, write=write)
        except ValueError as error:
            if "stale source hash" in str(error):
                raise
            delegated = self._native_refusal("native_adapter_error", str(error))
        except Exception as error:
            delegated = self._native_refusal("native_adapter_error", str(error))

        if delegated is not None:
            result = delegated.get("result")
            if not isinstance(result, dict):
                result = {"status": "refused", "code": "invalid_native_receipt"}
                delegated = {**delegated, "result": result}
            if self._native_succeeded(result, write=write):
                after = self._file_records(prepared)
                expected_key = "result_sha256" if write else "before_sha256"
                if any(item["after_sha256"] != item[expected_key] for item in after):
                    self._restore_prepared(prepared)
                    result = {
                        "status": "refused",
                        "code": "native_output_hash_mismatch",
                        "message": (
                            "native adapter bytes differ from the canonical Fast result; "
                            "newline and encoding normalization must be byte-exact"
                        ),
                    }
                    delegated = {**delegated, "result": result}
                else:
                    return self._receipt(
                        mode="write" if write else "dry-run",
                        executor=delegated,
                        files=after,
                        native={
                            "status": "ok",
                            "before_sha256": {
                                item["path"]: item["before_sha256"] for item in before
                            },
                            "after_sha256": {
                                item["path"]: item["after_sha256"] for item in after
                            },
                            "no_write_proof": not write,
                        },
                        no_write_proof=not write,
                        outcome="applied" if write else "dry_run",
                        applied=write,
                        write_attempted=write,
                        reason_code=None,
                        rollback={
                            "attempted": False,
                            "status": "not-needed",
                            "restored_paths": [],
                        },
                    )

            native_before = self._file_records(prepared)
            restored = self._restore_prepared(prepared)
            native_after = self._file_records(prepared)
            if any(
                item["after_sha256"] != item["before_sha256"] for item in native_after
            ):
                raise RuntimeError("native refusal could not be rolled back safely")
            return self._fallback_receipt(
                prepared,
                write=write,
                reason="native_adapter_refused",
                native={
                    "adapter": delegated.get("adapter", "simplicio-dev-cli"),
                    "status": result.get("status", "refused"),
                    "result": result,
                    "before_sha256": {
                        item["path"]: item["before_sha256"] for item in native_before
                    },
                    "after_sha256": {
                        item["path"]: item["after_sha256"] for item in native_after
                    },
                    "rollback": {"attempted": bool(restored), "restored": restored},
                    "no_write_proof": True,
                },
                reason_code=str(result.get("code") or "native_adapter_refused"),
                rollback={
                    "attempted": True,
                    "status": "restored",
                    "restored_paths": restored,
                },
            )

        return self._fallback_receipt(
            prepared,
            write=write,
            reason="simplicio-dev-cli is not installed",
            native={
                "adapter": "simplicio-dev-cli",
                "status": "unavailable",
                "before_sha256": {
                    item["path"]: item["before_sha256"] for item in before
                },
                "after_sha256": {
                    item["path"]: item["before_sha256"] for item in before
                },
                "no_write_proof": True,
            },
            reason_code="native_unavailable",
            rollback={"attempted": False, "status": "not-needed", "restored_paths": []},
        )

    def _prepare_changes(self, changes: list[Any]) -> list[PreparedChange]:
        prepared: list[PreparedChange] = []
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
            decoded = original.decode("utf-8")
            newline = "\r\n" if b"\r\n" in original else "\n"
            lines = decoded.splitlines(keepends=True)
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
                canonical_content = content.replace("\r\n", "\n").replace("\r", "\n")
                canonical_content = canonical_content.replace("\n", newline)
                suffix = (
                    newline
                    if canonical_content and not canonical_content.endswith(newline)
                    else ""
                )
                lines[start - 1 : end] = [canonical_content + suffix]
            updated = "".join(lines)
            prepared.append(
                PreparedChange(
                    path=path,
                    relative=relative,
                    expected_sha256=expected,
                    original=original,
                    updated=updated.encode("utf-8"),
                    replacements=len(replacements),
                )
            )
        return prepared

    @staticmethod
    def _native_refusal(code: str, message: str) -> dict[str, Any]:
        return {
            "adapter": "simplicio-dev-cli",
            "status": "refused",
            "result": {"status": "refused", "code": code, "message": message},
        }

    @staticmethod
    def _native_succeeded(result: dict[str, Any], *, write: bool) -> bool:
        if result.get("status") != "ok":
            return False
        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            return False
        return not (write and result.get("applied") is False)

    @staticmethod
    def _file_records(prepared: list[PreparedChange]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in prepared:
            current = item.path.read_bytes()
            records.append(
                {
                    "path": item.relative,
                    "replacements": item.replacements,
                    "expected_sha256": item.expected_sha256,
                    "before_sha256": hashlib.sha256(item.original).hexdigest(),
                    "after_sha256": hashlib.sha256(current).hexdigest(),
                    "result_sha256": hashlib.sha256(item.updated).hexdigest(),
                    "byte_representation": "raw-file-bytes",
                    "newline": "crlf" if b"\r\n" in item.updated else "lf",
                }
            )
        return records

    @staticmethod
    def _receipt(
        *,
        mode: str,
        executor: dict[str, Any],
        files: list[dict[str, Any]],
        native: dict[str, Any],
        no_write_proof: bool,
        outcome: str,
        applied: bool,
        write_attempted: bool,
        reason_code: str | None,
        rollback: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": "simplicio.fast.apply-receipt/v2",
            "mode": mode,
            "executor": executor,
            "files": files,
            "native": native,
            "no_write_proof": no_write_proof,
            "outcome": outcome,
            "applied": applied,
            "write_attempted": write_attempted,
            "reason_code": reason_code,
            "rollback": rollback,
        }

    def _fallback_receipt(
        self,
        prepared: list[PreparedChange],
        *,
        write: bool,
        reason: str,
        native: dict[str, Any],
        reason_code: str,
        rollback: dict[str, Any],
    ) -> dict[str, Any]:
        if write:
            self._write_prepared(prepared)
        files = self._file_records(prepared)
        expected_after = {
            item.relative: hashlib.sha256(
                (item.updated if write else item.original)
            ).hexdigest()
            for item in prepared
        }
        if any(item["after_sha256"] != expected_after[item["path"]] for item in files):
            raise RuntimeError("internal fallback produced an unexpected output hash")
        fallback = {
            "adapter": "internal-bootstrap",
            "status": "fallback",
            "reason": reason,
            "write_applied": write,
            "no_write_proof": not write,
        }
        return self._receipt(
            mode="write" if write else "dry-run",
            executor=fallback,
            files=files,
            native=native,
            no_write_proof=not write,
            outcome="applied" if write else "dry_run",
            applied=write,
            write_attempted=write,
            reason_code=reason_code,
            rollback=rollback,
        )

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".simplicio-fast",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(path)
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def _write_prepared(self, prepared: list[PreparedChange]) -> None:
        applied: list[PreparedChange] = []
        try:
            for item in prepared:
                if item.path.read_bytes() != item.original:
                    raise ValueError(f"stale source hash for {item.relative}")
                self._atomic_replace(item.path, item.updated)
                applied.append(item)
        except Exception:
            for item in reversed(applied):
                self._atomic_replace(item.path, item.original)
            raise

    def _restore_prepared(self, prepared: list[PreparedChange]) -> list[str]:
        restored: list[str] = []
        for item in prepared:
            if item.path.read_bytes() != item.original:
                self._atomic_replace(item.path, item.original)
                restored.append(item.relative)
        return restored


def load_changeset(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("changeset root must be an object")
    return value
