# Changelog

## 2.0.14 - 2026-07-27

- Publish the current master line with accelerated incremental refresh and the
  Loop-validated integrated-ready contract.

## 2.0.9 - 2026-07-27

- Publish the canonical .simplicio/fast snapshot defaults and V3 integration updates.

## 2.0.8 - 2026-07-27

### Fixed

- Publishes auditable Fast engine-selection receipts with measured Rust probing and fail-closed conformance routing.
- Restores Windows Python 3.14 close-on-exec handling for Git and Mapper subprocess probes.

## 2.0.6 - 2026-07-26

### Fixed

- Raises the validated snapshot capacity to 512 MiB for supported large Windows repositories.
- Emits SnapshotTooLarge before allocation/publication when the hard bound is exceeded.


## 2.0.5 - 2026-07-26

### Fixed

- Fails closed before parsing oversized source files on Windows with a structured error receipt.
- Propagates timeout and source-size bounds through the ingest path used by Loop.

## 2.0.4 - 2026-07-26

### Fixed

- Stabilizes Fast git, Mapper and workspace subprocess receipts on Windows Python 3.14 with explicit handle inheritance control.
- Keeps the structured dry-run native-output hash-mismatch rollback contract covered by regression tests.


### Fixed

- Recovers truncated or corrupt derived snapshots from authoritative source files.
- Validates rebuilt snapshots before atomic publication and preserves the prior generation on failure or timeout.
- Adds structured recovery and timeout receipts for Windows execution.
- Makes concurrent overlay JSON publication resilient to transient Windows locks.
- Uses physical-byte hashes for guarded apply while preserving CRLF and adapter validation.
- Adds regression coverage for recovery, timeout, concurrency and consecutive changesets.

## 2.0.2 - 2026-07-26

Safe native-hash fallback for guarded `apply` on Windows.

### Fixed

- Native Dev CLI refusals now produce a machine-readable
  `simplicio.fast.apply-receipt/v2` instead of an unversioned error.
- `apply` proves no-write or rolls back partial native mutations before using
  the explicit internal atomic fallback.
- Apply receipts include raw before/after SHA-256 values, outcome, reason code,
  write status and rollback evidence.
- Stale source hashes remain fail-closed, including immediately before each
  fallback replacement.

## 2.0.1 - 2026-07-26

Documentation and visual refresh for the Fast 2.x contract.

### Changed

- Clarified Fast's role for agents and LLMs: semantic project memory plus guarded change
  coordination, not an LLM, scheduler or policy authority.
- Expanded top-level CLI help and command inventory for tool discovery.
- Rewrote `AGENTS.md` around Mapper, Dev CLI, Loop and Runtime boundaries, receipts and safe
  generation handling.
- Added new hero, shared-memory and verified-flow illustrations under `assets/`.

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
