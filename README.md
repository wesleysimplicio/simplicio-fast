<p align="center">
  <img src="assets/simplicio-fast-hero.webp" alt="simplicio-fast turns source code into a shared binary semantic memory for fast agent execution" width="100%" />
</p>

<h1 align="center">simplicio-fast</h1>

<p align="center">
  <strong>Binary, incremental, memory-mapped semantic context for software agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/wesleysimplicio/simplicio-fast/releases"><img src="https://img.shields.io/badge/version-1.0.0-22c55e?style=for-the-badge" alt="Version 1.0.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://github.com/wesleysimplicio/simplicio-fast/issues"><img src="https://img.shields.io/github/issues/wesleysimplicio/simplicio-fast?style=for-the-badge" alt="Open issues"></a>
  <img src="https://img.shields.io/badge/runtime_dependencies-0-111827?style=for-the-badge" alt="Zero runtime dependencies">
</p>

<p align="center">
  <strong>Languages:</strong><br>
  <a href="README.md">English</a> ·
  <a href="READMEs/README.pt-BR.md">Português</a> ·
  <a href="READMEs/README.es-ES.md">Español</a> ·
  <a href="READMEs/README.fr-FR.md">Français</a> ·
  <a href="READMEs/README.de-DE.md">Deutsch</a> ·
  <a href="READMEs/README.it-IT.md">Italiano</a> ·
  <a href="READMEs/README.ja-JP.md">日本語</a> ·
  <a href="READMEs/README.ko-KR.md">한국어</a> ·
  <a href="READMEs/README.zh-CN.md">简体中文</a> ·
  <a href="READMEs/README.ru-RU.md">Русский</a> ·
  <a href="READMEs/README.pl-PL.md">Polski</a> ·
  <a href="READMEs/README.tr-TR.md">Türkçe</a> ·
  <a href="READMEs/README.nl-NL.md">Nederlands</a> ·
  <a href="READMEs/README.hi-IN.md">हिन्दी</a> ·
  <a href="READMEs/README.ar-SA.md">العربية</a>
</p>

---

## What is simplicio-fast?

Most coding agents repeatedly reopen files, parse the same project and send oversized context to an LLM. `simplicio-fast` builds a compact binary semantic snapshot once, maps it read-only with the operating system, and incrementally reparses only changed files.

```text
normal source files
        ↓
semantic extraction + SHA-256
        ↓
versioned .sfast binary snapshot
        ↓ mmap
small context query
        ↓
LLM plan and normal source patch
        ↓
tests + incremental refresh
```

The source repository always remains the source of truth. A `.sfast` file is a disposable derived cache, never a replacement for `.py`, `.ts`, `.rs`, `.cs` or other development files.

## Why it matters

- **Fast repeated orientation** — avoid parsing the full repository for every query.
- **Incremental rebuilds** — unchanged files reuse their semantic records by SHA-256.
- **Low-copy access** — `mmap` lets the OS page only the bytes that are touched.
- **Shared project memory** — future Loop slots can pin one immutable base generation.
- **Smaller LLM context** — send selected symbols and spans instead of repository dumps.
- **Auditable execution** — generation IDs, source hashes and receipts can bind context to patches.

> `simplicio-fast` is designed for broad repository use, but speedups are workload-dependent. The current ~23× result is a measured POC query benchmark, not a universal guarantee. Small repositories and cold one-shot runs may see little or no gain.

<p align="center">
  <img src="assets/simplicio-fast-shared-memory.webp" alt="One immutable memory-mapped snapshot shared by isolated worktrees" width="920" />
</p>

<p align="center"><em>One canonical memory image, many isolated consumers and future worktree overlays.</em></p>

## Benchmark

The included benchmark generates 500 Python modules containing 1,500 symbols. It compares reparsing every AST for each query with querying the binary snapshot through `mmap`.

| Operation | Median/total wall time | CPU / incremental behavior |
|---|---:|---:|
| Traditional AST query | 40.35 ms median | 40.36 ms CPU |
| Cold snapshot build | 57.43 ms | 500 parsed |
| `mmap` snapshot query | 1.75 ms median | 1.76 ms CPU |
| Rebuild without changes | 26.65 ms | 0 parsed / 500 reused |
| Rebuild after one file change | 28.05 ms | 1 parsed / 499 reused |

**Measured query result:** approximately **23× faster** and **95.65% less query CPU** on the recorded local environment (Python 3.12.13, peak process RSS 21,268 KiB).

Reproduce it instead of trusting the table:

```bash
python benchmarks/run.py
```

The command records wall time, CPU time, peak RSS, cold build, warm query, no-change rebuild, one-file rebuild and whether the changed symbol became visible. Use at least ten repetitions and identical hardware/configuration when comparing integrations.

## Install

```bash
git clone https://github.com/wesleysimplicio/simplicio-fast
cd simplicio-fast
python -m pip install -e .
```

No runtime package is required by version 1.0.0.

## CLI

```bash
simplicio-fast --help
simplicio-fast build --help
simplicio-fast query --help
simplicio-fast serve --help
```

Build and query the current repository:

```bash
simplicio-fast build .
simplicio-fast query UserService
```

Run without installing:

```bash
PYTHONPATH=src python -m simplicio_fast.cli build .
PYTHONPATH=src python -m simplicio_fast.cli query UserService
```

## CRUD proof of concept

The repository includes a dependency-free user API to prove the full cycle: create normal source, map it, query it, alter its behavior and refresh only the changed semantic input.

```bash
simplicio-fast serve --port 3000
```

```bash
curl -X POST http://127.0.0.1:3000/users \
  -H 'content-type: application/json' \
  -d '{"name":"Wesley","email":"wesley@example.com"}'

curl http://127.0.0.1:3000/users

curl -X PUT http://127.0.0.1:3000/users/USER_ID \
  -H 'content-type: application/json' \
  -d '{"active":false}'

curl -X DELETE http://127.0.0.1:3000/users/USER_ID
```

## How an LLM should use it

An LLM should not read the `.sfast` binary directly. A deterministic adapter queries the snapshot and returns a small context packet.

1. The task reaches the Agent or Loop.
2. Mapper resolves a canonical context handle.
3. Runtime executes `search`, `context` and `impact` under policy.
4. The LLM receives only relevant symbols, source spans and hashes.
5. The Agent writes normal source patches.
6. Runtime validates hashes, tests and receipts.
7. Fast incrementally refreshes changed files.

Today, version 1.0.0 provides `build`, `query` and the Python POC. The full `context`, `impact`, Mapper, Dev CLI, Loop and Runtime integrations are tracked in the [integration epic](https://github.com/wesleysimplicio/simplicio-fast/issues/1).

<p align="center">
  <img src="assets/simplicio-fast-verified-flow.webp" alt="Compact context moving through planning, editing, testing and verification gates" width="920" />
</p>

<p align="center"><em>Compact context enters; verified normal source code leaves.</em></p>

## Binary contract 1.0

| Section | Purpose |
|---|---|
| Header | `SFAST001`, schema version, counts, offsets and total size |
| File records | path reference, source size and SHA-256 |
| Symbol records | qualified name, file ID, line range and kind |
| String table | compact UTF-8 paths and qualified names |

Safety properties:

- read-only memory mapping;
- bounds, magic, version and total-size checks;
- atomic temporary-write and replace;
- deterministic symbol ordering;
- source hashes for incremental reuse;
- safe full rebuild because snapshots are derived.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests benchmarks
```

Version 1.0.0 covers:

- complete user CRUD and later status change;
- normalized-email conflict;
- binary build and symbol query;
- unchanged-file reuse;
- one-file invalidation and new-symbol visibility.

## Current scope

Ready:

- Python 3.11+;
- Python AST classes, functions and async functions;
- versioned binary snapshot;
- read-only `mmap`;
- incremental SHA-256 reuse;
- atomic writes;
- CRUD, tests and benchmark.

Planned:

- direct/hash symbol index;
- imports, references and call graph;
- context budgets and token accounting;
- default-branch snapshot plus worktree overlays;
- TypeScript, Rust and C# adapters;
- full Mapper, Dev CLI, Loop and Runtime integration;
- central daemon, leases, receipts, shadow/canary and rollback.

## Ecosystem architecture

| Project | Responsibility |
|---|---|
| `simplicio-fast` | binary semantic storage and incremental queries |
| `simplicio-mapper` | canonical public ContextGraph and stable IDs |
| `simplicio-dev-cli` | PlanDAG compilation using context handles |
| `simplicio-loop` | orientation, slots, convergence and delivery |
| `simplicio-runtime` | deterministic execution, policy and receipts |
| `simplicio-agent` | decisions, context selection and patch strategy |
| `simplicio-code` | integrated developer experience |

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=wesleysimplicio/simplicio-fast&type=Date)](https://star-history.com/#wesleysimplicio/simplicio-fast&Date)

> GitHub stars and the chart become externally visible when repository visibility and Star History access permit it.

## Roadmap

See the granular cross-repository plan:

- [Simplicio Fast epic and core issues](https://github.com/wesleysimplicio/simplicio-fast/issues)
- [Mapper integration](https://github.com/wesleysimplicio/simplicio-mapper/issues/358)
- [Dev CLI integration](https://github.com/wesleysimplicio/simplicio-dev-cli/issues/341)
- [Loop integration](https://github.com/wesleysimplicio/simplicio-loop/issues/746)
- [Runtime integration](https://github.com/wesleysimplicio/simplicio-runtime/issues/3597)

## License and status

Version 1.0.0 is a stable **proof of concept**, not yet a mandatory production dependency of the Simplicio ecosystem. Review [CHANGELOG.md](CHANGELOG.md) and open issues before adopting its binary schema as a long-lived external contract.
