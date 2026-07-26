# Changelog

## 1.0.0 - 2026-07-26

First stable proof-of-concept release.

### Included

- Dependency-free Python user CRUD with HTTP endpoints.
- Versioned `.sfast` binary semantic snapshot.
- Read-only memory mapping with Python `mmap`.
- Python AST extraction for classes, functions and async functions.
- SHA-256 incremental reuse for unchanged source files.
- Atomic snapshot replacement.
- CLI commands `build`, `query` and `serve`, all with `--help`.
- Unit tests for CRUD, conflicts, snapshot queries and incremental rebuilds.
- Reproducible synthetic benchmark for wall time, CPU, RSS and incremental behavior.

### Compatibility contract

- Python 3.11 or newer.
- Source files remain the only source of truth.
- Snapshot format magic: `SFAST001`.
- Snapshot schema version: `1`.
- Snapshots are derived caches and may be rebuilt safely.

### Known limitations

- Python-only source analysis.
- Symbol lookup currently scans fixed binary records.
- No import, reference or call graph yet.
- No direct LLM, Mapper, Runtime or Loop adapter yet.
