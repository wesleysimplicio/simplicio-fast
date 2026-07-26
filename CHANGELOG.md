# Changelog

## 2.0.0 - 2026-07-26

Simplicio Fast becomes a standalone project processor instead of requiring Mapper or Dev CLI.

### Added

- Cross-platform benchmark peak-RSS collection with deterministic partial receipts when the
  operating system cannot expose the metric.
- `ingest`: absorb a repository into the incremental binary snapshot.
- `understand`: resolve bounded, hash-verified context directly from a natural-language task.
- `plan`: compile understanding into `simplicio.fast.plandag/v2`.
- `apply`: validate and optionally write `simplicio.fast.changeset/v2` operations.
- Dry-run by default and SHA-256 guards for every edited source file.
- Deterministic validation command discovery for Python, Node and Rust projects.

### Architecture

- Mapper and Dev CLI are optional compatibility adapters.
- Fast owns project ingestion, semantic context, plan compilation and structured source edits.
- Runtime remains the authority for policy, execution and receipts.
- Loop remains responsible for slots, retries, convergence and delivery.

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
