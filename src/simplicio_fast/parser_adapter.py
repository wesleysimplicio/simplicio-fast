"""Versioned parser-adapter payloads for the Python fallback path.

The adapter is deliberately a data contract around the existing language
adapters.  It does not expose snapshot offsets or replace Mapper's public
ContextGraph.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adapters import SUPPORTED_EXTENSIONS, language_for_path, parse_path
from .snapshot import stable_id

SCHEMA = "simplicio.fast.parser-adapter/v1"
SUPPORTED_MODES = {"bootstrap", "integrated"}


class ParserAdapterError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path:
        raise ParserAdapterError("path_escape", path)
    return candidate.as_posix()


def _source_files(root: Path, changed_paths: Iterable[str] | None) -> list[Path]:
    if changed_paths is None:
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and language_for_path(path) is not None
        )
    result: list[Path] = []
    for relative in changed_paths:
        safe = _safe_relative(relative)
        path = root / safe
        if path.is_file() and language_for_path(path) is not None:
            result.append(path)
    return sorted(set(result))


def build_payload(
    root: Path,
    *,
    mapper_generation: str | None = None,
    commit: str | None = None,
    config_fingerprint: str | None = None,
    changed_paths: Iterable[str] | None = None,
    mode: str = "bootstrap",
) -> dict[str, Any]:
    """Build a deterministic, bounded contract payload from existing adapters."""

    root = root.resolve()
    if mode not in SUPPORTED_MODES:
        raise ParserAdapterError("mode_invalid", mode)
    files: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    for path in _source_files(root, changed_paths):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            diagnostics.append(
                {"path": relative, "code": "encoding_invalid", "detail": str(error)}
            )
            continue
        language = SUPPORTED_EXTENSIONS[path.suffix.casefold()]
        files.append(
            {
                "path": relative,
                "language": language,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "encoding": "utf-8",
            }
        )
        try:
            parsed = parse_path(path, relative)
        except (OSError, SyntaxError, UnicodeDecodeError) as error:
            diagnostics.append(
                {
                    "path": relative,
                    "code": "parse_failed",
                    "detail": type(error).__name__,
                }
            )
            continue
        for item in parsed:
            signature = item.signature or item.kind
            symbol_id = stable_id(
                str(root), relative, language, item.qualified_name, signature
            )
            if symbol_id in seen_ids:
                raise ParserAdapterError("symbol_id_collision", symbol_id)
            seen_ids.add(symbol_id)
            symbols.append(
                {
                    "id": symbol_id,
                    "name": item.name,
                    "qualified_name": item.qualified_name,
                    "kind": item.kind,
                    "language": language,
                    "file": relative,
                    "line": item.line,
                    "end_line": item.end_line,
                    "signature": item.signature,
                }
            )
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": mode,
        "producer": "simplicio-fast-python-adapter",
        "repository": str(root),
        "commit": commit,
        "mapper_generation": mapper_generation,
        "config_fingerprint": config_fingerprint,
        "changed_paths": sorted(
            {_safe_relative(path) for path in (changed_paths or ())}
        ),
        "files": files,
        "symbols": symbols,
        "relations": [],
        "diagnostics": diagnostics,
        "completeness": "complete" if not diagnostics else "partial",
    }
    payload["payload_sha256"] = _digest(payload)
    return payload


def validate_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema, bounds, paths, stable IDs and canonical payload digest."""

    if value.get("schema") != SCHEMA:
        raise ParserAdapterError("schema_unsupported")
    if value.get("mode") not in SUPPORTED_MODES:
        raise ParserAdapterError("mode_invalid")
    if value.get("producer") != "simplicio-fast-python-adapter":
        raise ParserAdapterError("producer_invalid")
    files = value.get("files")
    symbols = value.get("symbols")
    if not isinstance(files, list) or not isinstance(symbols, list):
        raise ParserAdapterError("payload_shape_invalid")
    file_paths = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise ParserAdapterError("file_invalid")
        file_paths.add(_safe_relative(str(item.get("path", ""))))
        if not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64:
            raise ParserAdapterError("file_digest_invalid")
    ids: set[str] = set()
    for item in symbols:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise ParserAdapterError("symbol_invalid")
        if item["id"] in ids:
            raise ParserAdapterError("symbol_id_collision")
        ids.add(item["id"])
        if item.get("file") not in file_paths:
            raise ParserAdapterError("symbol_file_missing")
    supplied = value.get("payload_sha256")
    unsigned = dict(value)
    unsigned.pop("payload_sha256", None)
    if not isinstance(supplied, str) or supplied != _digest(unsigned):
        raise ParserAdapterError("payload_digest_mismatch")
    return {
        "schema": SCHEMA,
        "status": "valid",
        "files": len(files),
        "symbols": len(symbols),
        "completeness": value.get("completeness"),
    }


__all__ = ["SCHEMA", "ParserAdapterError", "build_payload", "validate_payload"]
