# AGENTS.md

## Purpose

This repository provides binary, incremental, memory-mapped semantic storage. It does not replace
the canonical project understanding contract.

## Mandatory Mapper rule

**Every agent, LLM, Loop slot, Runtime operator or Dev CLI flow using Simplicio Fast MUST use
`simplicio-mapper` as the canonical context producer.**

- Mapper owns the public ContextGraph, stable IDs and semantic compatibility.
- Fast owns binary persistence, incremental extraction and mmap-backed lookup.
- Consumers must request context through Mapper handles.
- Consumers must not interpret `.sfast` offsets or internal records directly.
- Consumers must not create a second public context contract.
- If Mapper is missing, incompatible or unhealthy, agentic execution must stop with an actionable
  diagnostic. It must never continue with an empty or fabricated context.

The standalone Fast CLI may be used directly only for development, diagnostics, format tests and
benchmarks. Direct CLI use is not the official agent integration path.

## Required agent workflow

1. Run Mapper/Fast capability and health checks.
2. Resolve the repository default branch and canonical Mapper ContextGraph handle.
3. Build or refresh one Fast base snapshot for that branch and configuration.
4. Pin the snapshot generation for the entire attempt.
5. Ask Mapper for relevant symbols and context; Mapper may use Fast internally.
6. Verify source hashes before creating or applying a patch.
7. Modify normal source files only. Never mutate `.sfast` as source code.
8. Run the repository's tests and validation gates.
9. Refresh only changed files after a verified patch.
10. Emit generation, context, validation and fallback receipts.

## Worktrees and parallel slots

- Use one canonical base snapshot from the default branch.
- Each worktree must use an isolated incremental overlay.
- Do not rebuild the entire project independently in every slot.
- Do not expose one worktree overlay to another.
- Pin base and overlay generation IDs in checkpoints and handoffs.
- In speculative execution, only the verified winner may promote source changes or refresh state.

## Current 1.0 commands

```bash
simplicio-fast --version
simplicio-fast --help
simplicio-fast build .
simplicio-fast query UserService
simplicio-fast context UserService --root .
simplicio-fast doctor
simplicio-fast refresh .
```

All machine-facing commands emit versioned JSON. Preserve the `schema` field and reject unknown
major schema versions.

## Context safety

- Treat the source repository as the only source of truth.
- Require source SHA-256 values on context spans.
- Reject stale snapshots and run `refresh`; never apply a patch against stale spans.
- Enforce `max-results`, `max-lines` and `max-bytes` before sending context to an LLM.
- Do not send the whole repository when bounded semantic context is available.
- Never report estimated speed, CPU or token gains as measured results.

## Benchmark isolation

Benchmark code lives only under `benchmarks/` and must not be imported by the runtime package.
Generated benchmark projects and results must remain temporary or ignored. Compare baseline and
Fast with identical repository, workload, model, prompt, hardware and cache policy, using at least
ten repetitions.

## Validation before completion

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests benchmarks
python benchmarks/run.py
```

An agent may claim completion only when implementation, tests, schema compatibility, stale-source
behavior, documentation and benchmark isolation have been verified.
