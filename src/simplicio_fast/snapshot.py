from __future__ import annotations

import ast
import hashlib
import mmap
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"SFAST001"
VERSION = 1
HEADER = struct.Struct("<8s7I")
FILE_RECORD = struct.Struct("<4I32s")
SYMBOL_RECORD = struct.Struct("<6I")
KIND_TO_ID = {"class": 1, "function": 2, "async_function": 3}
ID_TO_KIND = {value: key for key, value in KIND_TO_ID.items()}


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    file: str
    line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class BuildMetrics:
    files: int
    symbols: int
    parsed_files: int
    reused_files: int
    snapshot_bytes: int
    wall_ms: float
    cpu_ms: float


@dataclass(frozen=True, slots=True)
class ContextSpan:
    symbol: str
    kind: str
    file: str
    start_line: int
    end_line: int
    source_sha256: str
    content: str


class StaleSnapshotError(RuntimeError):
    pass


class _Collector(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.scope: list[str] = []
        self.symbols: list[Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, "async_function")

    def _visit_scope(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
    ) -> None:
        qualified = ".".join([*self.scope, node.name])
        self.symbols.append(
            Symbol(node.name, qualified, kind, self.file, node.lineno, node.end_lineno or node.lineno)
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def parse_symbols(path: Path, relative_path: str) -> list[Symbol]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    collector = _Collector(relative_path)
    collector.visit(tree)
    return collector.symbols


def source_files(root: Path) -> list[Path]:
    ignored = {".git", ".venv", "__pycache__", ".simplicio-fast", "node_modules"}
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part in ignored for part in path.relative_to(root).parts)
    )


def build_snapshot(root: Path, output: Path) -> BuildMetrics:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    root = root.resolve()
    previous: dict[str, tuple[bytes, list[Symbol]]] = {}
    if output.exists():
        with Snapshot(output) as snapshot:
            previous = snapshot.grouped()

    entries: list[tuple[str, bytes, int, list[Symbol]]] = []
    parsed = reused = 0
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        contents = path.read_bytes()
        digest = hashlib.sha256(contents).digest()
        cached = previous.get(relative)
        if cached and cached[0] == digest:
            symbols = cached[1]
            reused += 1
        else:
            symbols = parse_symbols(path, relative)
            parsed += 1
        entries.append((relative, digest, len(contents), symbols))

    strings = bytearray()

    def add(value: str) -> tuple[int, int]:
        encoded = value.encode()
        offset = len(strings)
        strings.extend(encoded)
        return offset, len(encoded)

    file_rows: list[tuple[int, int, int, int, bytes]] = []
    file_index: dict[str, int] = {}
    for index, (path, digest, size, _) in enumerate(entries):
        file_index[path] = index
        offset, length = add(path)
        file_rows.append((offset, length, size, 0, digest))

    symbols = sorted(
        (symbol for _, _, _, found in entries for symbol in found),
        key=lambda item: (item.name, item.qualified_name, item.file, item.line),
    )
    symbol_rows: list[tuple[int, int, int, int, int, int]] = []
    for symbol in symbols:
        offset, length = add(symbol.qualified_name)
        symbol_rows.append(
            (
                offset,
                length,
                file_index[symbol.file],
                symbol.line,
                symbol.end_line,
                KIND_TO_ID[symbol.kind],
            )
        )

    files_offset = HEADER.size
    symbols_offset = files_offset + len(file_rows) * FILE_RECORD.size
    strings_offset = symbols_offset + len(symbol_rows) * SYMBOL_RECORD.size
    total_size = strings_offset + len(strings)
    payload = bytearray(total_size)
    HEADER.pack_into(
        payload,
        0,
        MAGIC,
        VERSION,
        len(file_rows),
        len(symbol_rows),
        files_offset,
        symbols_offset,
        strings_offset,
        total_size,
    )
    for index, row in enumerate(file_rows):
        FILE_RECORD.pack_into(payload, files_offset + index * FILE_RECORD.size, *row)
    for index, row in enumerate(symbol_rows):
        SYMBOL_RECORD.pack_into(payload, symbols_offset + index * SYMBOL_RECORD.size, *row)
    payload[strings_offset:] = strings

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(output)
    return BuildMetrics(
        files=len(entries),
        symbols=len(symbols),
        parsed_files=parsed,
        reused_files=reused,
        snapshot_bytes=total_size,
        wall_ms=(time.perf_counter() - wall_start) * 1000,
        cpu_ms=(time.process_time() - cpu_start) * 1000,
    )


class Snapshot:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._sha256: str | None = None
        self._file = path.open("rb")
        self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        (
            magic,
            version,
            self.file_count,
            self.symbol_count,
            self.files_offset,
            self.symbols_offset,
            self.strings_offset,
            total_size,
        ) = HEADER.unpack_from(self._map)
        if magic != MAGIC or version != VERSION or total_size != len(self._map):
            self.close()
            raise ValueError("invalid or unsupported Simplicio Fast snapshot")

    def close(self) -> None:
        if not self._map.closed:
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
        return f"SFAST{VERSION:03d}:{self.sha256}"

    def _text(self, offset: int, length: int) -> str:
        start = self.strings_offset + offset
        return self._map[start : start + length].decode()

    def files(self) -> list[tuple[str, bytes]]:
        result = []
        for index in range(self.file_count):
            row = FILE_RECORD.unpack_from(self._map, self.files_offset + index * FILE_RECORD.size)
            result.append((self._text(row[0], row[1]), row[4]))
        return result

    def symbols(self) -> list[Symbol]:
        files = [path for path, _ in self.files()]
        result = []
        for index in range(self.symbol_count):
            row = SYMBOL_RECORD.unpack_from(
                self._map, self.symbols_offset + index * SYMBOL_RECORD.size
            )
            qualified = self._text(row[0], row[1])
            result.append(
                Symbol(
                    qualified.rsplit(".", 1)[-1],
                    qualified,
                    ID_TO_KIND[row[5]],
                    files[row[2]],
                    row[3],
                    row[4],
                )
            )
        return result

    def find(self, query: str) -> list[Symbol]:
        needle = query.casefold()
        return [
            symbol
            for symbol in self.symbols()
            if needle in symbol.name.casefold() or needle in symbol.qualified_name.casefold()
        ]

    def context(
        self,
        root: Path,
        query: str,
        *,
        max_results: int = 10,
        max_lines: int = 120,
        max_bytes: int = 32_000,
    ) -> list[ContextSpan]:
        if max_results < 1 or max_lines < 1 or max_bytes < 1:
            raise ValueError("context limits must be positive")
        root = root.resolve()
        expected_hashes = {path: digest for path, digest in self.files()}
        spans: list[ContextSpan] = []
        consumed = 0
        seen: set[tuple[str, int, int]] = set()
        for symbol in self.find(query):
            if len(spans) >= max_results:
                break
            path = (root / symbol.file).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError(f"snapshot path escapes root: {symbol.file}") from error
            contents = path.read_bytes()
            actual_hash = hashlib.sha256(contents).digest()
            if actual_hash != expected_hashes[symbol.file]:
                raise StaleSnapshotError(
                    f"source changed after snapshot: {symbol.file}; run simplicio-fast refresh"
                )
            start = symbol.line
            end = min(symbol.end_line, start + max_lines - 1)
            key = (symbol.file, start, end)
            if key in seen:
                continue
            lines = contents.decode("utf-8").splitlines()
            snippet = "\n".join(lines[start - 1 : end])
            size = len(snippet.encode())
            if consumed + size > max_bytes:
                remaining = max_bytes - consumed
                if remaining <= 0:
                    break
                snippet = snippet.encode()[:remaining].decode("utf-8", errors="ignore")
                size = len(snippet.encode())
            seen.add(key)
            consumed += size
            spans.append(
                ContextSpan(
                    symbol=symbol.qualified_name,
                    kind=symbol.kind,
                    file=symbol.file,
                    start_line=start,
                    end_line=end,
                    source_sha256=actual_hash.hex(),
                    content=snippet,
                )
            )
        return spans

    def grouped(self) -> dict[str, tuple[bytes, list[Symbol]]]:
        hashes = dict(self.files())
        grouped = {path: (digest, []) for path, digest in hashes.items()}
        for symbol in self.symbols():
            grouped[symbol.file][1].append(symbol)
        return grouped
