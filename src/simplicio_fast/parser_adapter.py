"""Versioned parser-adapter payloads for the Python fallback path.

The adapter is deliberately a data contract around the existing language
adapters.  It does not expose snapshot offsets or replace Mapper's public
ContextGraph.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .adapters import (
    SUPPORTED_EXTENSIONS,
    csharp_workspace_fingerprint,
    language_for_path,
    parse_path,
    rust_workspace_fingerprint,
    typescript_workspace_fingerprint,
)
from .snapshot import _parse_file, stable_id

SCHEMA = "simplicio.fast.parser-adapter/v1"
SUPPORTED_MODES = {"bootstrap", "integrated"}
DEFAULT_LIMITS = {
    "max_files": 10_000,
    "max_symbols": 1_000_000,
    "max_relations": 2_000_000,
    "max_payload_bytes": 64 * 1024 * 1024,
}


def adapter_capability() -> dict[str, Any]:
    """Return the versioned, deterministic capability receipt for this adapter."""

    contract = {
        "schema": SCHEMA,
        "producer": "simplicio-fast-python-adapter",
        "limits": DEFAULT_LIMITS,
        "modes": sorted(SUPPORTED_MODES),
    }
    return {
        "schema": SCHEMA,
        "producer": contract["producer"],
        "version": "1",
        "health": "ready",
        "completeness": "contract",
        "modes": contract["modes"],
        "fingerprints": {"contract_sha256": _digest(contract)},
        "limits": dict(DEFAULT_LIMITS),
    }


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


def _adapter_limits(limits: Mapping[str, int] | None) -> dict[str, int]:
    selected = dict(DEFAULT_LIMITS)
    if limits is not None:
        for name, value in limits.items():
            if name not in selected or isinstance(value, bool) or not isinstance(value, int):
                raise ParserAdapterError("limit_invalid", name)
            if value < 1:
                raise ParserAdapterError("limit_invalid", name)
            selected[name] = value
    return selected


def _source_files(root: Path, changed_paths: Iterable[str] | None) -> list[Path]:
    if changed_paths is None:
        ignored = {
            ".git",
            ".simplicio",
            ".simplicio-fast",
            "__pycache__",
            "node_modules",
            "target",
            "bin",
            "obj",
        }
        found: list[Path] = []
        for directory, directories, filenames in os.walk(root, topdown=True):
            directories[:] = sorted(name for name in directories if name not in ignored)
            found.extend(
                Path(directory) / name
                for name in sorted(filenames)
                if language_for_path(Path(name)) is not None
            )
        return sorted(found)
    result: list[Path] = []
    for relative in changed_paths:
        safe = _safe_relative(relative)
        path = root / safe
        if path.is_file() and language_for_path(path) is not None:
            result.append(path)
    return sorted(set(result))


def _lexical_relations(
    path: Path,
    relative: str,
    language: str,
    symbols: list[Any],
    symbol_ids: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Emit bounded, deterministic relations for optional lexical adapters."""

    text = path.read_text(encoding="utf-8")
    relations: list[dict[str, Any]] = []
    for symbol in symbols:
        identifier = symbol_ids.get(symbol.qualified_name, "")
        relations.append(
            {
                "origin": symbol.qualified_name,
                "destination": symbol.qualified_name,
                "kind": "definition",
                "confidence": 0.75,
                "origin_id": identifier,
                "destination_id": identifier,
                "file": relative,
            }
        )
        if symbol.name.casefold().startswith(("test", "spec")):
            relations.append(
                {
                    "origin": relative,
                    "destination": symbol.qualified_name,
                    "kind": "test",
                    "confidence": 0.6,
                    "origin_id": "",
                    "destination_id": identifier,
                    "file": relative,
                }
            )
    import_pattern = {
        "typescript": re.compile(r"^\s*import\b.*?[\"']([^\"']+)[\"']"),
        "rust": re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+)"),
        "csharp": re.compile(r"^\s*(?:global\s+)?using\s+([^;=]+)"),
    }[language]
    for line in text.splitlines():
        match = import_pattern.search(line)
        if match:
            relations.append(
                {
                    "origin": relative,
                    "destination": match.group(1).strip(),
                    "kind": "import",
                    "confidence": 0.5,
                    "origin_id": "",
                    "destination_id": "",
                    "file": relative,
                }
            )
    return relations


def build_payload(
    root: Path,
    *,
    mapper_generation: str | None = None,
    commit: str | None = None,
    config_fingerprint: str | None = None,
    changed_paths: Iterable[str] | None = None,
    mode: str = "bootstrap",
    limits: Mapping[str, int] | None = None,
    previous_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, bounded contract payload from existing adapters."""

    root = root.resolve()
    if mode not in SUPPORTED_MODES:
        raise ParserAdapterError("mode_invalid", mode)
    if mode == "integrated" and (
        not isinstance(mapper_generation, str)
        or not mapper_generation.strip()
        or not isinstance(commit, str)
        or len(commit) != 40
    ):
        raise ParserAdapterError("mapper_required")
    selected_limits = _adapter_limits(limits)
    if previous_payload is not None and changed_paths is None:
        raise ParserAdapterError("previous_payload_requires_changed_paths")
    scoped = changed_paths is not None
    changed_set = {
        _safe_relative(path) for path in (changed_paths or ())
    }
    changed_paths = sorted(changed_set) if scoped else None
    previous: dict[str, Any] | None = None
    if previous_payload is not None:
        validate_payload(previous_payload)
        if previous_payload.get("repository") != str(root):
            raise ParserAdapterError("previous_repository_mismatch")
        previous = dict(previous_payload)
    files: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    for path in _source_files(root, changed_paths):
        if len(files) >= selected_limits["max_files"]:
            raise ParserAdapterError("file_limit_exceeded")
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
            if language == "python":
                parsed, parsed_relations = _parse_file(path, relative, str(root))
            else:
                parsed = parse_path(path, relative)
                lexical_ids = {
                    item.qualified_name: stable_id(
                        str(root), relative, language, item.qualified_name,
                        item.signature or item.kind,
                    )
                    for item in parsed
                }
                parsed_relations = _lexical_relations(
                    path, relative, language, parsed, lexical_ids
                )
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
            if len(symbols) >= selected_limits["max_symbols"]:
                raise ParserAdapterError("symbol_limit_exceeded")
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
        symbol_ids = {
            item.qualified_name: item.symbol_id for item in parsed if item.symbol_id
        }
        for relation in parsed_relations:
            if len(relations) >= selected_limits["max_relations"]:
                raise ParserAdapterError("relation_limit_exceeded")
            if isinstance(relation, Mapping):
                destination = relation["destination"]
                origin = relation["origin"]
                kind = relation["kind"]
                confidence = relation["confidence"]
                origin_id = relation.get("origin_id", "")
                supplied_destination_id = relation.get("destination_id", "")
            else:
                destination = relation.destination
                origin = relation.origin
                kind = relation.kind
                confidence = relation.confidence
                origin_id = relation.origin_id
                supplied_destination_id = relation.destination_id
            destination_id = supplied_destination_id or symbol_ids.get(destination, "")
            relations.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "kind": kind,
                    "confidence": confidence,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "file": relative,
                }
            )
    reused_paths: set[str] = set()
    deleted_paths: set[str] = set()
    if previous is not None:
        previous_files = {
            str(item["path"]): item for item in previous["files"]
        }
        current_paths = {str(item["path"]) for item in files}
        for path, item in previous_files.items():
            if path in changed_set:
                if not (root / path).is_file():
                    deleted_paths.add(path)
                continue
            if path in current_paths:
                continue
            files.append(dict(item))
            reused_paths.add(path)
        for item in previous["symbols"]:
            if item.get("file") in reused_paths:
                symbols.append(dict(item))
        for item in previous["relations"]:
            if item.get("file") in reused_paths:
                relations.append(dict(item))
        diagnostics.extend(
            item
            for item in previous["diagnostics"]
            if item.get("path") in reused_paths
        )
    files.sort(key=lambda item: str(item["path"]))
    symbols.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["id"])))
    relations.sort(
        key=lambda item: (
            str(item["file"]),
            str(item["kind"]),
            str(item["origin"]),
            str(item["destination"]),
        )
    )
    diagnostics.sort(key=lambda item: (str(item.get("path", "")), str(item.get("code", ""))))
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "adapter_version": "1",
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
        "relations": relations,
        "diagnostics": diagnostics,
        "completeness": "complete" if not diagnostics else "partial",
        "invalidation": {
            "schema": "simplicio.fast.parser-invalidation/v1",
            "requested_paths": sorted(changed_set),
            "parsed_paths": sorted(item["path"] for item in files if item["path"] not in reused_paths),
            "reused_paths": sorted(reused_paths),
            "deleted_paths": sorted(deleted_paths),
            "reason_codes": [
                "full_scan" if not scoped else "explicit_changed_paths",
                *("previous_payload_reuse" if previous is not None else "no_previous_payload",),
            ],
        },
    }
    languages = {item["language"] for item in files}
    payload["workspace_fingerprints"] = {
        language: fingerprint
        for language, fingerprint in (
            ("csharp", csharp_workspace_fingerprint(root)),
            ("rust", rust_workspace_fingerprint(root)),
            ("typescript", typescript_workspace_fingerprint(root)),
        )
        if language in languages
    }
    payload["payload_sha256"] = _digest(payload)
    if len(_canonical(payload)) > selected_limits["max_payload_bytes"]:
        raise ParserAdapterError("payload_limit_exceeded")
    return payload


def validate_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema, bounds, paths, stable IDs and canonical payload digest."""

    if value.get("schema") != SCHEMA:
        raise ParserAdapterError("schema_unsupported")
    if value.get("mode") not in SUPPORTED_MODES:
        raise ParserAdapterError("mode_invalid")
    if value.get("producer") != "simplicio-fast-python-adapter":
        raise ParserAdapterError("producer_invalid")
    if value.get("adapter_version") != "1":
        raise ParserAdapterError("adapter_version_unsupported")
    mode = value.get("mode")
    commit = value.get("commit")
    mapper_generation = value.get("mapper_generation")
    if mode == "integrated":
        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
            or not isinstance(mapper_generation, str)
            or not mapper_generation.strip()
        ):
            raise ParserAdapterError("mapper_identity_invalid")
    elif commit is not None and (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise ParserAdapterError("commit_invalid")
    changed_paths = value.get("changed_paths")
    if not isinstance(changed_paths, list):
        raise ParserAdapterError("changed_paths_invalid")
    for path in changed_paths:
        if not isinstance(path, str):
            raise ParserAdapterError("changed_path_invalid")
        _safe_relative(path)
    completeness = value.get("completeness")
    if completeness not in {"complete", "partial"}:
        raise ParserAdapterError("completeness_invalid")
    diagnostics = value.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise ParserAdapterError("diagnostics_invalid")
    invalidation = value.get("invalidation")
    if not isinstance(invalidation, Mapping):
        raise ParserAdapterError("invalidation_invalid")
    if invalidation.get("schema") != "simplicio.fast.parser-invalidation/v1":
        raise ParserAdapterError("invalidation_invalid")
    for field in ("requested_paths", "parsed_paths", "reused_paths", "deleted_paths"):
        values = invalidation.get(field)
        if not isinstance(values, list) or any(
            not isinstance(path, str) for path in values
        ):
            raise ParserAdapterError("invalidation_invalid")
        for path in values:
            _safe_relative(path)
    reason_codes = invalidation.get("reason_codes")
    if not isinstance(reason_codes, list) or any(
        not isinstance(reason, str) or not reason for reason in reason_codes
    ):
        raise ParserAdapterError("invalidation_invalid")
    workspace_fingerprints = value.get("workspace_fingerprints")
    if not isinstance(workspace_fingerprints, Mapping):
        raise ParserAdapterError("workspace_fingerprints_invalid")
    for language, fingerprint in workspace_fingerprints.items():
        if (
            language not in {"csharp", "rust", "typescript"}
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ParserAdapterError("workspace_fingerprint_invalid")
    files = value.get("files")
    symbols = value.get("symbols")
    relations = value.get("relations")
    if (
        not isinstance(files, list)
        or not isinstance(symbols, list)
        or not isinstance(relations, list)
    ):
        raise ParserAdapterError("payload_shape_invalid")
    limits = _adapter_limits(None)
    if len(files) > limits["max_files"]:
        raise ParserAdapterError("file_limit_exceeded")
    if len(symbols) > limits["max_symbols"]:
        raise ParserAdapterError("symbol_limit_exceeded")
    if len(relations) > limits["max_relations"]:
        raise ParserAdapterError("relation_limit_exceeded")
    file_paths = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise ParserAdapterError("file_invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise ParserAdapterError("file_invalid")
        normalized_path = _safe_relative(path)
        if item.get("language") not in set(SUPPORTED_EXTENSIONS.values()):
            raise ParserAdapterError("file_language_invalid")
        if item.get("encoding") != "utf-8":
            raise ParserAdapterError("file_encoding_invalid")
        file_paths.add(normalized_path)
        if not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64:
            raise ParserAdapterError("file_digest_invalid")
    ids: set[str] = set()
    for item in symbols:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("qualified_name"), str)
            or not isinstance(item.get("kind"), str)
            or not isinstance(item.get("language"), str)
            or not isinstance(item.get("line"), int)
            or isinstance(item.get("line"), bool)
            or not isinstance(item.get("end_line"), int)
            or isinstance(item.get("end_line"), bool)
            or item.get("line") < 1
            or item.get("end_line") < item.get("line")
        ):
            raise ParserAdapterError("symbol_invalid")
        if item["id"] in ids:
            raise ParserAdapterError("symbol_id_collision")
        ids.add(item["id"])
        if item.get("file") not in file_paths or item.get("language") not in set(
            SUPPORTED_EXTENSIONS.values()
        ):
            raise ParserAdapterError("symbol_file_missing")
    for item in relations:
        if (
            not isinstance(item, Mapping)
            or item.get("file") not in file_paths
            or not isinstance(item.get("origin"), str)
            or not isinstance(item.get("destination"), str)
            or not isinstance(item.get("origin_id"), str)
            or not isinstance(item.get("destination_id"), str)
            or not isinstance(item.get("confidence"), (int, float))
            or isinstance(item.get("confidence"), bool)
            or not 0 <= item.get("confidence") <= 1
        ):
            raise ParserAdapterError("relation_file_missing")
        if item.get("kind") not in {
            "import",
            "reference",
            "call",
            "definition",
            "test",
        }:
            raise ParserAdapterError("relation_kind_invalid")
    supplied = value.get("payload_sha256")
    unsigned = dict(value)
    unsigned.pop("payload_sha256", None)
    if not isinstance(supplied, str) or supplied != _digest(unsigned):
        raise ParserAdapterError("payload_digest_mismatch")
    if len(_canonical(value)) > limits["max_payload_bytes"]:
        raise ParserAdapterError("payload_limit_exceeded")
    return {
        "schema": SCHEMA,
        "status": "valid",
        "files": len(files),
        "symbols": len(symbols),
        "relations": len(relations),
        "completeness": value.get("completeness"),
    }


__all__ = [
    "DEFAULT_LIMITS",
    "SCHEMA",
    "ParserAdapterError",
    "build_payload",
    "validate_payload",
]
