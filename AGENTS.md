# AGENTS.md

## Purpose

This repository is the central in-memory project processor. It coordinates Mapper extraction,
binary/mmap storage, bounded understanding, PlanDAG compilation and Dev CLI edits.

## Mandatory Mapper rule

**Every integrated agent, LLM, Loop slot or Runtime flow using Simplicio Fast MUST use
`simplicio-mapper` for project extraction and `simplicio-dev-cli` for source mutation.**

- Mapper owns the public ContextGraph, stable IDs and semantic compatibility.
- Fast owns orchestration, binary persistence, incremental memory, context selection and PlanDAG.
- Dev CLI owns mechanical source edits and edit receipts.
- Consumers must request context through Mapper handles.
- Consumers must not interpret `.sfast` offsets or internal records directly.
- Consumers must not create a second public context contract.
- If Mapper is missing, incompatible or unhealthy, agentic execution must stop with an actionable
  diagnostic. It must never continue with an empty or fabricated context.

Fast's internal extractor/editor are explicit bootstrap fallbacks for development and tests. They
must not be reported as the fully integrated production path.

## Required agent workflow

1. Fast invokes Mapper to extract the canonical project graph.
2. Fast compiles Mapper output into the binary mmap representation.
3. Fast resolves bounded context and compiles a PlanDAG for the task.
4. Pin the snapshot generation for the entire attempt.
5. The LLM decides using only the selected context.
6. Fast compiles a hash-guarded changeset.
7. Dev CLI validates and performs normal source edits.
8. Runtime authorizes effects and records receipts when available.
9. Loop runs tests, corrections and delivery convergence.
10. Fast refreshes changed semantic inputs after validation.

## Worktrees and parallel slots

- Use one canonical base snapshot from the default branch.
- Each worktree must use an isolated incremental overlay.
- Do not rebuild the entire project independently in every slot.
- Do not expose one worktree overlay to another.
- Pin base and overlay generation IDs in checkpoints and handoffs.
- In speculative execution, only the verified winner may promote source changes or refresh state.

## Current 2.0 commands

```bash
simplicio-fast --version
simplicio-fast --help
simplicio-fast ingest .
simplicio-fast understand "implement user authentication"
simplicio-fast plan "implement user authentication"
simplicio-fast apply changeset.json
simplicio-fast apply changeset.json --write
simplicio-fast build .
simplicio-fast query UserService
simplicio-fast context UserService --root .
simplicio-fast doctor
simplicio-fast refresh .
```

`apply` is dry-run by default. All machine-facing commands emit versioned JSON. Preserve the
`schema` field and reject unknown major schema versions.

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
