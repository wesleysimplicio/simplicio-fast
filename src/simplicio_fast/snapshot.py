"""Versioned, bounded and memory-mapped semantic snapshots.

SFAST001/v1 remains readable for migration.  New snapshots use v2: a little-endian
section directory, checksums for every section, direct lookup indexes and typed
relationships.  The binary file is always a disposable cache; source files and
their hashes remain authoritative.
"""

from __future__ import annotations

import ast
import tokenize
import hashlib
import json
import mmap
import os
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"SFAST001"
LEGACY_VERSION = 1
VERSION = 2
ENDIAN_MARKER = 0x0102
# The agent repository's canonical v2 snapshot is about 406 MiB. Keep a hard
# bound while allowing that supported Windows checkout to publish atomically.
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_FILES = 1_000_000
MAX_SYMBOLS = 5_000_000
MAX_RELATIONS = 10_000_000
MAX_SECTIONS = 32

DEFAULT_MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
# v1 is deliberately frozen.  Do not change these structs: old snapshots must
# remain readable after a v2 writer is installed.
LEGACY_HEADER = struct.Struct("<8s7I")
LEGACY_FILE_RECORD = struct.Struct("<4I32s")
LEGACY_SYMBOL_RECORD = struct.Struct("<6I")

# v2 header: magic, version, endian marker, section count, generation,
# directory offset/size, total size and a whole-file checksum (with this field
# zeroed while calculating it).
HEADER = struct.Struct("<8sHHIQQQQ32s")
SECTION_RECORD = struct.Struct("<16sQQ32s")
FILE_RECORD = struct.Struct("<IIQ32s16s")
SYMBOL_RECORD = struct.Struct("<IIIIIIIIII32s")
REQUIRED_SECTIONS = ("files", "symbols", "relations", "indexes", "strings")
KIND_TO_ID = {
    "class": 1, "function": 2, "async_function": 3, "import": 4,
    "namespace": 5, "interface": 6, "struct": 7, "trait": 8, "enum": 9,
}
ID_TO_KIND = {value: key for key, value in KIND_TO_ID.items()}
RELATION_KINDS = {"import", "reference", "call", "definition", "test"}


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    file: str
    line: int
    end_line: int
    symbol_id: str = ""
    signature: str = ""
    base_generation: str | None = None
    overlay_generation: str | None = None


@dataclass(frozen=True, slots=True)
class Relation:
    origin: str
    destination: str
    kind: str
    confidence: float
    origin_id: str = ""
    destination_id: str = ""


@dataclass(frozen=True, slots=True)
class BuildMetrics:
    files: int
    symbols: int
    parsed_files: int
    reused_files: int
    snapshot_bytes: int
    wall_ms: float
    cpu_ms: float
    format_version: int = VERSION
    generation: str = ""
    relations: int = 0


@dataclass(frozen=True, slots=True)
class ContextSpan:
    symbol: str
    kind: str
    file: str
    start_line: int
    end_line: int
    source_sha256: str
    content: str
    symbol_id: str = ""
    tokens: int = 0
    base_generation: str | None = None
    overlay_generation: str | None = None


class StaleSnapshotError(RuntimeError):
    pass


def stable_id(repository: str, file: str, language: str, symbol: str, signature: str) -> str:
    """Return the stable 256-bit ID required by the public format contract."""

    value = "\0".join((repository, file, language, symbol, signature))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _repository_id(root: Path) -> str:
    """Use a stable repository marker without making git a runtime dependency."""

    git_marker = root / ".git"
    try:
        if git_marker.is_dir():
            config = git_marker / "config"
        elif git_marker.is_file():
            pointer = git_marker.read_text(encoding="utf-8").strip()
            gitdir = pointer.removeprefix("gitdir:").strip()
            config = Path(gitdir).resolve().parents[1] / "config"
        else:
            config = Path()
        marker = ""
        if config.is_file():
            for line in config.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("url = "):
                    marker = line.split("=", 1)[1].strip()
                    break
    except OSError:
        marker = ""
    return marker or root.name


class _Collector(ast.NodeVisitor):
    def __init__(self, file: str, repository: str) -> None:
        self.file = file
        self.repository = repository
        self.scope: list[str] = []
        self.symbols: list[Symbol] = []
        self.relations: list[Relation] = []
        self._current: list[Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, "async_function")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.relations.append(Relation(self.file, alias.name, "import", 1.0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            self.relations.append(Relation(self.file, target, "import", 1.0))

    def visit_Call(self, node: ast.Call) -> None:
        if self._current:
            target = _ast_name(node.func)
            if target:
                self.relations.append(
                    Relation(
                        self._current[-1].qualified_name,
                        target,
                        "call",
                        0.8,
                        self._current[-1].symbol_id,
                    )
                )
                if self._current[-1].name.casefold().startswith("test"):
                    self.relations.append(
                        Relation(
                            self._current[-1].qualified_name,
                            target,
                            "test",
                            0.9,
                            self._current[-1].symbol_id,
                        )
                    )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self._current and isinstance(node.ctx, ast.Load) and node.id not in {"True", "False", "None"}:
            self.relations.append(
                Relation(self._current[-1].qualified_name, node.id, "reference", 0.5, self._current[-1].symbol_id)
            )

    def _visit_scope(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
    ) -> None:
        qualified = ".".join([*self.scope, node.name])
        signature = _signature(node)
        symbol = Symbol(
            node.name,
            qualified,
            kind,
            self.file,
            node.lineno,
            node.end_lineno or node.lineno,
            stable_id(self.repository, self.file, "python", qualified, signature),
            signature,
        )
        self.symbols.append(symbol)
        self.relations.append(Relation(self.file, qualified, "definition", 1.0, "", symbol.symbol_id))
        self.scope.append(node.name)
        self._current.append(symbol)
        self.generic_visit(node)
        self._current.pop()
        self.scope.pop()


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _signature(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    if isinstance(node, ast.ClassDef):
        return ""
    try:
        return ast.unparse(node.args)
    except (AttributeError, ValueError):
        return ""


class SnapshotBuildTimeout(TimeoutError):
    """Raised when a bounded build cannot complete before its deadline."""

    code = "snapshot_build_timeout"
    recovery = "retry with a larger timeout or exclude generated/vendor source directories"


class SnapshotTooLarge(ValueError):
    """Raised before allocating or publishing a snapshot over the hard bound."""

    code = "snapshot_too_large"

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        self.recovery = "exclude generated/vendor source or use a partitioned repository snapshot"
        super().__init__(
            f"{self.code} size={size} limit={limit} recovery={self.recovery}"
        )


class SourceFileTooLarge(ValueError):
    """Raised before parsing a source file that exceeds the safety bound."""

    code = "source_file_too_large"

    def __init__(self, path: Path, size: int, limit: int) -> None:
        self.path = path.as_posix()
        self.size = size
        self.limit = limit
        self.recovery = "exclude generated/vendor source or raise --max-file-bytes explicitly"
        super().__init__(
            f"{self.code} path={self.path} size={size} limit={limit} recovery={self.recovery}"
        )

class SourceEncodingError(ValueError):
    code = "source_encoding_unreadable"

    def __init__(self, path: Path, cause: BaseException) -> None:
        self.path = path.as_posix()
        self.recovery = "add a PEP 263 encoding cookie or remove the binary-looking source file"
        super().__init__(
            f"{self.code} path={self.path} recovery={self.recovery} cause={type(cause).__name__}"
        )


def _read_python_source(path: Path) -> str:
    try:
        with tokenize.open(path) as source:
            return source.read()
    except (SyntaxError, UnicodeDecodeError) as error:
        raise SourceEncodingError(path, error) from error


def _parse_file(path: Path, relative_path: str, repository: str) -> tuple[list[Symbol], list[Relation]]:
    if path.suffix.casefold() not in {".py", ".pyi"}:
        from .adapters import parse_path

        return parse_path(path, relative_path), []
    tree = ast.parse(_read_python_source(path), filename=relative_path)
    collector = _Collector(relative_path, repository)
    collector.visit(tree)
    return collector.symbols, collector.relations


def parse_symbols(path: Path, relative_path: str) -> list[Symbol]:
    """Parse symbols using the legacy public helper signature."""

    return _parse_file(path, relative_path, path.parent.name)[0]


def source_files(root: Path) -> list[Path]:
    ignored = {".git", ".venv", "__pycache__", ".simplicio-fast", ".simplicio", "node_modules"}
    suffixes = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".rs", ".cs"}
    found: list[Path] = []
    for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in ignored)
        found.extend(
            Path(directory) / name
            for name in sorted(filenames)
            if Path(name).suffix.casefold() in suffixes
        )
    return sorted(found)


def _add_string(strings: bytearray, value: str) -> tuple[int, int]:
    encoded = value.encode("utf-8")
    offset = len(strings)
    strings.extend(encoded)
    return offset, len(encoded)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_v2(entries: list[tuple[str, bytes, int, list[Symbol]]], relations: list[Relation], output: Path) -> tuple[int, str]:
    strings = bytearray()
    file_rows: list[bytes] = []
    file_index: dict[str, int] = {}
    for index, (path, digest, size, _) in enumerate(entries):
        file_index[path] = index
        offset, length = _add_string(strings, path)
        file_id = hashlib.sha256(path.encode("utf-8")).digest()[:16]
        file_rows.append(FILE_RECORD.pack(offset, length, size, digest, file_id))

    symbols = sorted(
        (symbol for _, _, _, found in entries for symbol in found),
        key=lambda item: (item.name.casefold(), item.qualified_name, item.file, item.line),
    )
    symbol_rows: list[bytes] = []
    for symbol in symbols:
        name_offset, name_length = _add_string(strings, symbol.name)
        qualified_offset, qualified_length = _add_string(strings, symbol.qualified_name)
        signature_offset, signature_length = _add_string(strings, symbol.signature)
        symbol_rows.append(
            SYMBOL_RECORD.pack(
                name_offset,
                name_length,
                qualified_offset,
                qualified_length,
                signature_offset,
                signature_length,
                file_index[symbol.file],
                symbol.line,
                symbol.end_line,
                KIND_TO_ID[symbol.kind],
                bytes.fromhex(symbol.symbol_id),
            )
        )

    names: dict[str, list[int]] = {}
    paths: dict[str, list[int]] = {}
    kinds: dict[str, list[int]] = {}
    exact: dict[str, list[int]] = {}
    for index, symbol in enumerate(symbols):
        names.setdefault(symbol.name.casefold(), []).append(index)
        exact.setdefault(symbol.qualified_name.casefold(), []).append(index)
        paths.setdefault(symbol.file, []).append(index)
        kinds.setdefault(symbol.kind, []).append(index)
    indexes = {"exact": exact, "names": names, "paths": paths, "kinds": kinds}
    relation_payload = [
        {
            "origin": relation.origin,
            "destination": relation.destination,
            "kind": relation.kind,
            "confidence": relation.confidence,
            "origin_id": relation.origin_id,
            "destination_id": relation.destination_id,
        }
        for relation in sorted(
            relations, key=lambda item: (item.kind, item.origin, item.destination, item.confidence)
        )
    ]
    sections_data = {
        "files": b"".join(file_rows),
        "symbols": b"".join(symbol_rows),
        "relations": _json_bytes(relation_payload),
        "indexes": _json_bytes(indexes),
        "strings": bytes(strings),
    }
    section_count = len(REQUIRED_SECTIONS)
    directory_offset = HEADER.size
    directory_size = SECTION_RECORD.size * section_count
    offset = directory_offset + directory_size
    section_rows: list[bytes] = []
    section_offsets: list[tuple[str, int, bytes]] = []
    for name in REQUIRED_SECTIONS:
        data = sections_data[name]
        if offset % 8:
            offset += 8 - offset % 8
        digest = hashlib.sha256(data).digest()
        section_offsets.append((name, offset, data))
        section_rows.append(SECTION_RECORD.pack(name.encode("ascii"), offset, len(data), digest))
        offset += len(data)
    total_size = offset
    if total_size > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLarge(total_size, MAX_SNAPSHOT_BYTES)
    source_digest = hashlib.sha256(b"".join(digest for _, digest, _, _ in entries)).digest()
    generation_int = int.from_bytes(source_digest[:8], "little")
    header = HEADER.pack(MAGIC, VERSION, ENDIAN_MARKER, section_count, generation_int, directory_offset, directory_size, total_size, b"\0" * 32)
    payload = bytearray(total_size)
    payload[: HEADER.size] = header
    payload[directory_offset : directory_offset + directory_size] = b"".join(section_rows)
    for _, section_offset, data in section_offsets:
        payload[section_offset : section_offset + len(data)] = data
    checksum = hashlib.sha256(payload).digest()
    payload[: HEADER.size] = HEADER.pack(MAGIC, VERSION, ENDIAN_MARKER, section_count, generation_int, directory_offset, directory_size, total_size, checksum)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.{os.getpid()}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        # Validate the exact bytes before publishing. The old snapshot remains
        # available until this succeeds and os.replace performs one atomic swap.
        with Snapshot(temporary_path):
            pass
        os.replace(temporary_path, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return total_size, checksum.hex()


def build_snapshot(
    root: Path,
    output: Path,
    *,
    timeout_seconds: float | None = None,
    max_file_bytes: int = DEFAULT_MAX_SOURCE_FILE_BYTES,
) -> BuildMetrics:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()

    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    root = root.resolve()
    repository = _repository_id(root)
    previous: dict[str, tuple[bytes, list[Symbol]]] = {}
    previous_relations: list[Relation] = []
    previous_symbol_files: dict[str, str] = {}
    if output.exists():
        try:
            with Snapshot(output) as snapshot:
                previous = snapshot.grouped()
                previous_relations = snapshot.relations()
                previous_symbol_files = {symbol.qualified_name: symbol.file for symbol in snapshot.symbols()}
        except (OSError, ValueError):
            # A snapshot is disposable derived state. Ignore invalid/truncated
            # cache bytes and rebuild from authoritative source files. _build_v2
            # keeps the existing path untouched until its replacement validates.
            previous = {}
            previous_relations = []
            previous_symbol_files = {}

    entries: list[tuple[str, bytes, int, list[Symbol]]] = []
    relations: list[Relation] = []
    parsed = reused = 0
    deadline = None if timeout_seconds is None else wall_start + timeout_seconds
    for path in source_files(root):
        if deadline is not None and time.perf_counter() >= deadline:
            raise SnapshotBuildTimeout(
                f"snapshot build exceeded {timeout_seconds:g}s before publishing; "
                "the previous snapshot was preserved"
            )

        try:
            file_size = path.stat().st_size
        except OSError as error:
            raise OSError(f"could not stat source file {path}: {error}") from error
        if file_size > max_file_bytes:
            raise SourceFileTooLarge(path, file_size, max_file_bytes)
        relative = path.relative_to(root).as_posix()
        contents = path.read_bytes()
        digest = hashlib.sha256(contents).digest()
        cached = previous.get(relative)
        if cached and cached[0] == digest:
            symbols = cached[1]
            reused += 1
        else:
            symbols, found_relations = _parse_file(path, relative, repository)
            relations.extend(found_relations)
            parsed += 1
        entries.append((relative, digest, len(contents), symbols))
    if reused and output.exists():
        changed_paths = {path for path, digest, _, _ in entries if previous.get(path, (None, []))[0] != digest}
        relations = [
            relation
            for relation in previous_relations
            if relation.origin not in changed_paths
            and previous_symbol_files.get(relation.origin) not in changed_paths
        ] + relations
    total_size, checksum = _build_v2(entries, relations, output)
    return BuildMetrics(
        files=len(entries),
        symbols=sum(len(found) for _, _, _, found in entries),
        parsed_files=parsed,
        reused_files=reused,
        snapshot_bytes=total_size,
        wall_ms=(time.perf_counter() - wall_start) * 1000,
        cpu_ms=(time.process_time() - cpu_start) * 1000,
        generation=checksum,
        relations=len(relations),
    )


class Snapshot:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._sha256: str | None = None
        self._file = path.open("rb")
        try:
            size = os.fstat(self._file.fileno()).st_size
            if size < LEGACY_HEADER.size or size > MAX_SNAPSHOT_BYTES:
                raise ValueError("invalid snapshot size")
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            magic = self._map[:8]
            if magic != MAGIC:
                raise ValueError("invalid or unsupported Simplicio Fast snapshot")
            version = struct.unpack_from("<H", self._map, 8)[0]
            if version == LEGACY_VERSION:
                self._open_legacy()
            elif version == VERSION:
                self._open_v2()
            else:
                raise ValueError(f"unsupported Simplicio Fast snapshot version: {version}")
        except Exception:
            self.close()
            raise

    def _open_legacy(self) -> None:
        if len(self._map) < LEGACY_HEADER.size:
            raise ValueError("truncated SFAST001/v1 header")
        magic, version, self.file_count, self.symbol_count, self.files_offset, self.symbols_offset, self.strings_offset, total_size = LEGACY_HEADER.unpack_from(self._map)
        if magic != MAGIC or version != LEGACY_VERSION or total_size != len(self._map):
            raise ValueError("invalid or unsupported SFAST001/v1 snapshot")
        self.format_version = LEGACY_VERSION
        self._header_generation = ""
        self.relation_count = 0
        self._sections = {}
        if self.file_count > MAX_FILES or self.symbol_count > MAX_SYMBOLS:
            raise ValueError("snapshot record limit exceeded")
        _validate_region(self.files_offset, self.file_count * LEGACY_FILE_RECORD.size, self.strings_offset, LEGACY_HEADER.size)
        _validate_region(self.symbols_offset, self.symbol_count * LEGACY_SYMBOL_RECORD.size, self.strings_offset, LEGACY_HEADER.size)
        if not self.files_offset <= self.symbols_offset <= self.strings_offset <= total_size:
            raise ValueError("invalid v1 section offsets")
        for index in range(self.file_count):
            row = LEGACY_FILE_RECORD.unpack_from(self._map, self.files_offset + index * LEGACY_FILE_RECORD.size)
            _validate_text(row[0], row[1], total_size - self.strings_offset)
        for index in range(self.symbol_count):
            row = LEGACY_SYMBOL_RECORD.unpack_from(self._map, self.symbols_offset + index * LEGACY_SYMBOL_RECORD.size)
            _validate_text(row[0], row[1], total_size - self.strings_offset)
            if row[2] >= self.file_count or row[3] < 1 or row[4] < row[3] or row[5] not in ID_TO_KIND:
                raise ValueError("invalid v1 symbol record")

    def _open_v2(self) -> None:
        if len(self._map) < HEADER.size:
            raise ValueError("truncated SFAST001/v2 header")
        magic, version, endian, section_count, generation, directory_offset, directory_size, total_size, checksum = HEADER.unpack_from(self._map)
        if magic != MAGIC or version != VERSION or endian != ENDIAN_MARKER or total_size != len(self._map):
            raise ValueError("invalid or unsupported SFAST001/v2 header")
        if section_count < len(REQUIRED_SECTIONS) or section_count > MAX_SECTIONS:
            raise ValueError("invalid section count")
        _validate_region(directory_offset, directory_size, len(self._map), HEADER.size)
        if directory_size != section_count * SECTION_RECORD.size:
            raise ValueError("invalid section directory size")
        directory_end = directory_offset + directory_size
        check_payload = bytearray(self._map)
        check_payload[HEADER.size - 32 : HEADER.size] = b"\0" * 32
        if hashlib.sha256(check_payload).digest() != checksum:
            raise ValueError("snapshot checksum mismatch")
        sections: dict[str, tuple[int, int]] = {}
        regions: list[tuple[int, int]] = []
        for index in range(section_count):
            name_raw, offset, length, section_checksum = SECTION_RECORD.unpack_from(self._map, directory_offset + index * SECTION_RECORD.size)
            name = name_raw.rstrip(b"\0").decode("ascii", errors="strict")
            if not name or name in sections:
                raise ValueError("invalid or duplicate section name")
            _validate_region(offset, length, len(self._map), directory_end)
            if offset % 8:
                raise ValueError("unaligned section offset")
            if hashlib.sha256(self._map[offset : offset + length]).digest() != section_checksum:
                raise ValueError(f"section checksum mismatch: {name}")
            sections[name] = (offset, length)
            regions.append((offset, offset + length))
        if any(name not in sections for name in REQUIRED_SECTIONS):
            raise ValueError("snapshot is missing a required section")
        if any(a < b and c < d and max(a, c) < min(b, d) for i, (a, b) in enumerate(regions) for c, d in regions[i + 1 :]):
            raise ValueError("overlapping snapshot sections")
        self.format_version = VERSION
        self._header_generation = f"{generation:016x}"
        self._sections = sections
        if sections["files"][1] % FILE_RECORD.size or sections["symbols"][1] % SYMBOL_RECORD.size:
            raise ValueError("section length is not a whole number of records")
        self.file_count = sections["files"][1] // FILE_RECORD.size
        self.symbol_count = sections["symbols"][1] // SYMBOL_RECORD.size
        self.relation_count = 0
        if self.file_count > MAX_FILES or self.symbol_count > MAX_SYMBOLS:
            raise ValueError("snapshot record limit exceeded")
        self._validate_v2_records()

    def _validate_v2_records(self) -> None:
        string_offset, string_length = self._sections["strings"]
        for index in range(self.file_count):
            row = FILE_RECORD.unpack_from(self._map, self._sections["files"][0] + index * FILE_RECORD.size)
            _validate_text(row[0], row[1], string_length)
        for index in range(self.symbol_count):
            row = SYMBOL_RECORD.unpack_from(self._map, self._sections["symbols"][0] + index * SYMBOL_RECORD.size)
            _validate_text(row[0], row[1], string_length)
            _validate_text(row[2], row[3], string_length)
            _validate_text(row[4], row[5], string_length)
            if row[6] >= self.file_count or row[7] < 1 or row[8] < row[7] or row[9] not in ID_TO_KIND:
                raise ValueError("invalid symbol record")
        try:
            relation_data = json.loads(self._section_bytes("relations"))
            index_data = json.loads(self._section_bytes("indexes"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid index or relation section") from error
        if not isinstance(relation_data, list) or len(relation_data) > MAX_RELATIONS:
            raise ValueError("invalid relation count")
        for relation in relation_data:
            if not isinstance(relation, dict) or relation.get("kind") not in RELATION_KINDS:
                raise ValueError("invalid relation record")
            if not isinstance(relation.get("confidence"), (int, float)) or not 0 <= relation["confidence"] <= 1:
                raise ValueError("invalid relation confidence")
        if not isinstance(index_data, dict):
            raise ValueError("invalid index payload")
        for key in ("exact", "names", "paths", "kinds"):
            if not isinstance(index_data.get(key), dict):
                raise ValueError("missing direct index")
            for values in index_data[key].values():
                if not isinstance(values, list) or any(not isinstance(value, int) or value < 0 or value >= self.symbol_count for value in values):
                    raise ValueError("direct index points outside symbol section")
        self.relation_count = len(relation_data)

    def close(self) -> None:
        if hasattr(self, "_map") and not self._map.closed:
            self._map.close()
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def sha256(self) -> str:
        """Return the digest of the exact snapshot bytes opened by mmap."""
        if self._sha256 is not None:
            return self._sha256
        digest = hashlib.sha256()
        for offset in range(0, len(self._map), 1024 * 1024):
            digest.update(self._map[offset : offset + 1024 * 1024])
        self._sha256 = digest.hexdigest()
        return self._sha256

    @property
    def generation(self) -> str:
        """Return a stable generation handle derived only from snapshot bytes."""
        return f"{MAGIC.decode('ascii')}:{self.sha256}"

    def _section_bytes(self, name: str) -> bytes:
        offset, length = self._sections[name]
        return self._map[offset : offset + length]

    def _text(self, offset: int, length: int) -> str:
        if self.format_version == LEGACY_VERSION:
            _validate_text(offset, length, len(self._map) - self.strings_offset)
            start = self.strings_offset + offset
        else:
            start = self._sections["strings"][0] + offset
            _validate_text(offset, length, self._sections["strings"][1])
        return self._map[start : start + length].decode("utf-8")

    def files(self) -> list[tuple[str, bytes]]:
        result: list[tuple[str, bytes]] = []
        for index in range(self.file_count):
            if self.format_version == LEGACY_VERSION:
                row = LEGACY_FILE_RECORD.unpack_from(self._map, self.files_offset + index * LEGACY_FILE_RECORD.size)
                result.append((self._text(row[0], row[1]), row[4]))
            else:
                row = FILE_RECORD.unpack_from(self._map, self._sections["files"][0] + index * FILE_RECORD.size)
                result.append((self._text(row[0], row[1]), row[3]))
        return result

    def _symbol_at(self, index: int, files: list[str] | None = None) -> Symbol:
        files = files or [path for path, _ in self.files()]
        if self.format_version == LEGACY_VERSION:
            row = LEGACY_SYMBOL_RECORD.unpack_from(self._map, self.symbols_offset + index * LEGACY_SYMBOL_RECORD.size)
            qualified = self._text(row[0], row[1])
            file = files[row[2]]
            return Symbol(row and qualified.rsplit(".", 1)[-1], qualified, ID_TO_KIND[row[5]], file, row[3], row[4], stable_id(file.split("/", 1)[0], file, "python", qualified, ""))
        row = SYMBOL_RECORD.unpack_from(self._map, self._sections["symbols"][0] + index * SYMBOL_RECORD.size)
        name = self._text(row[0], row[1])
        qualified = self._text(row[2], row[3])
        signature = self._text(row[4], row[5])
        return Symbol(name, qualified, ID_TO_KIND[row[9]], files[row[6]], row[7], row[8], row[10].hex(), signature)

    def symbols(self) -> list[Symbol]:
        files = [path for path, _ in self.files()]
        return [self._symbol_at(index, files) for index in range(self.symbol_count)]

    def _indexes(self) -> dict[str, dict[str, list[int]]]:
        if self.format_version == LEGACY_VERSION:
            return {}
        value = json.loads(self._section_bytes("indexes"))
        return value

    def find_exact(self, query: str) -> list[Symbol]:
        if self.format_version == LEGACY_VERSION:
            return [symbol for symbol in self.symbols() if symbol.qualified_name.casefold() == query.casefold()]
        indexes = self._indexes()["exact"]
        files = [path for path, _ in self.files()]
        return [self._symbol_at(index, files) for index in indexes.get(query.casefold(), [])]

    def search(self, query: str, *, prefix: bool = False, path: str | None = None, kind: str | None = None) -> list[Symbol]:
        needle = query.casefold()
        if self.format_version == LEGACY_VERSION:
            candidates = list(range(self.symbol_count))
        else:
            indexes = self._indexes()
            if prefix:
                candidates = [index for name, values in indexes["names"].items() if name.startswith(needle) for index in values]
            else:
                candidates = [index for name, values in indexes["names"].items() if needle in name for index in values]
                candidates.extend(index for name, values in indexes["exact"].items() if needle in name for index in values)
            if path is not None:
                candidates = sorted(set(candidates).intersection(indexes["paths"].get(path, [])))
            if kind is not None:
                candidates = sorted(set(candidates).intersection(indexes["kinds"].get(kind, [])))
        files = [path_value for path_value, _ in self.files()]
        result = [self._symbol_at(index, files) for index in sorted(set(candidates))]
        return sorted(result, key=lambda item: (item.name.casefold(), item.qualified_name, item.file, item.line))

    def find(self, query: str) -> list[Symbol]:
        return self.search(query)

    def relations(self) -> list[Relation]:
        if self.format_version == LEGACY_VERSION:
            return []
        return [Relation(**value) for value in json.loads(self._section_bytes("relations"))]

    def impact(self, query: str) -> list[Relation]:
        needle = query.casefold()
        symbols = self.search(query)
        ids = {symbol.symbol_id for symbol in symbols}
        names = {symbol.name.casefold() for symbol in symbols}
        qualified = {symbol.qualified_name.casefold() for symbol in symbols}
        result = []
        for relation in self.relations():
            fields = {relation.origin.casefold(), relation.destination.casefold(), relation.origin_id.casefold(), relation.destination_id.casefold()}
            if needle in fields or any(needle in value for value in fields) or ids.intersection(fields) or names.intersection(fields) or qualified.intersection(fields):
                result.append(relation)
        return sorted(result, key=lambda item: (item.kind, item.origin, item.destination, item.confidence))

    def context(
        self,
        root: Path,
        query: str,
        *,
        max_results: int = 10,
        max_lines: int = 120,
        max_bytes: int = 32_000,
        max_tokens: int | None = None,
    ) -> list[ContextSpan]:
        if max_results < 1 or max_lines < 1 or max_bytes < 1 or (max_tokens is not None and max_tokens < 1):
            raise ValueError("context limits must be positive")
        root = root.resolve()
        expected_hashes = {path: digest for path, digest in self.files()}
        spans: list[ContextSpan] = []
        consumed = consumed_tokens = 0
        seen: set[tuple[str, int, int]] = set()
        for symbol in self.find(query):
            if len(spans) >= max_results:
                break
            if symbol.file not in expected_hashes:
                raise ValueError(f"symbol references unknown file: {symbol.file}")
            path = (root / symbol.file).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError(f"snapshot path escapes root: {symbol.file}") from error
            contents = path.read_bytes()
            actual_hash = hashlib.sha256(contents).digest()
            if actual_hash != expected_hashes[symbol.file]:
                raise StaleSnapshotError(f"source changed after snapshot: {symbol.file}; run simplicio-fast refresh")
            lines = contents.decode("utf-8").splitlines()
            start = symbol.line
            end = min(symbol.end_line, start + max_lines - 1)
            key = (symbol.file, start, end)
            if key in seen:
                continue
            snippet = "\n".join(lines[start - 1 : end])
            if consumed + len(snippet.encode("utf-8")) > max_bytes:
                remaining = max_bytes - consumed
                if remaining <= 0:
                    break
                snippet = snippet.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            encoded_size = len(snippet.encode("utf-8"))
            tokens = max(1, (encoded_size + 3) // 4) if encoded_size else 0
            if max_tokens is not None and consumed_tokens + tokens > max_tokens:
                remaining_tokens = max_tokens - consumed_tokens
                if remaining_tokens <= 0:
                    break
                snippet = snippet.encode("utf-8")[: remaining_tokens * 4].decode("utf-8", errors="ignore")
                encoded_size = len(snippet.encode("utf-8"))
                tokens = max(1, (encoded_size + 3) // 4) if encoded_size else 0
            seen.add(key)
            consumed += encoded_size
            consumed_tokens += tokens
            spans.append(ContextSpan(symbol.qualified_name, symbol.kind, symbol.file, start, end, actual_hash.hex(), snippet, symbol.symbol_id, tokens))
        return spans

    def stats(self) -> dict[str, object]:
        return {
            "format": f"SFAST001/v{self.format_version}",
            "version": self.format_version,
            "generation": self.generation,
            "bytes": len(self._map),
            "files": self.file_count,
            "symbols": self.symbol_count,
            "relations": self.relation_count,
            "sections": sorted(self._sections),
        }

    def grouped(self) -> dict[str, tuple[bytes, list[Symbol]]]:
        hashes = dict(self.files())
        grouped = {path: (digest, []) for path, digest in hashes.items()}
        for symbol in self.symbols():
            grouped[symbol.file][1].append(symbol)
        return grouped


def _validate_region(offset: int, length: int, limit: int, minimum: int) -> None:
    if offset < minimum or length < 0 or offset > limit or length > limit - offset:
        raise ValueError("snapshot offset or length is outside the mapped file")


def _validate_text(offset: int, length: int, limit: int) -> None:
    if offset < 0 or length < 0 or offset > limit or length > limit - offset:
        raise ValueError("snapshot string offset or length is outside the string section")
