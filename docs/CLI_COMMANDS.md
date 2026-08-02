# Simplicio Fast: command and feature index

This is the discovery contract for humans, LLMs and agent integrations.
Fast owns semantic project memory, bounded context, PlanDAG compilation,
generation/overlay coordination, guarded changesets and deterministic
receipts. Source files remain authoritative; snapshots are disposable
derived state.

## Entry points

| Entry point | Purpose | Help |
| --- | --- | --- |
| `simplicio-fast` | Snapshot, context, PlanDAG, changeset and workspace CLI | `simplicio-fast --help` |
| `simplicio-fast-cross-repo` | Validate the pinned cross-repository stack lock | `simplicio-fast-cross-repo --help` |

Every public command and nested action must explain its purpose through
`--help`/`-h`. Read help at the exact level being invoked before execution.

## Top-level commands

| Command | Function | Help |
| --- | --- | --- |
| `build`, `refresh`, `ingest` | Create or incrementally update the binary semantic snapshot | `simplicio-fast <command> --help` |
| `query`, `search` | Resolve symbols through snapshot indexes | `simplicio-fast <command> --help` |
| `context` | Return bounded, hash-verified source spans for an LLM | `simplicio-fast context --help` |
| `navigate` | Follow one bounded structural relation from a canonical handle | `simplicio-fast navigate --help` |
| `impact` | Return typed imports, references, calls and tests | `simplicio-fast impact --help` |
| `stats` | Report snapshot generation and section statistics | `simplicio-fast stats --help` |
| `query-plan` | Explain the deterministic query/index budget plan | `simplicio-fast query-plan --help` |
| `segments` | Publish, validate or map immutable snapshot sections | `simplicio-fast segments --help` |
| `understand`, `plan` | Turn a natural-language task into bounded context or a PlanDAG | `simplicio-fast <command> --help` |
| `delivery` | Prepare or execute guarded delivery with provenance/idempotency receipts | `simplicio-fast delivery --help` |
| `apply` | Validate or apply a hash-guarded changeset; dry-run by default | `simplicio-fast apply --help` |
| `doctor` | Diagnose installation, integration and snapshot integrity | `simplicio-fast doctor --help` |
| `rollout` | Record shadow/canary/integrated rollout transitions | `simplicio-fast rollout --help` |
| `serve` | Run the small user CRUD HTTP proof-of-concept | `simplicio-fast serve --help` |
| `semantic-score` | Rank bounded candidates with Runtime-aware fallback | `simplicio-fast semantic-score --help` |
| `capabilities` | Report parser, SDK, adapter, security and engine capabilities | `simplicio-fast capabilities --help` |
| `parser-payload` | Convert a validated Mapper handoff to parser-adapter JSON | `simplicio-fast parser-payload --help` |
| `pin`, `release`, `gc`, `watch` | Protect, release, collect or refresh workspace generations | `simplicio-fast <command> --help` |
| `base`, `overlay`, `delta`, `handoff`, `merge` | Build and query canonical/isolated workspace views | `simplicio-fast <command> --help` |

## Changeset actions

`changeset` is a public command family. Each action has its own help:

| Action | Function |
| --- | --- |
| `prepare` | Compile JSON intent into a sealed binary changeset |
| `validate` | Validate a binary changeset against source hashes and leases |
| `seal` | Copy and verify a binary changeset into sealed output |
| `inspect` | Inspect binary metadata without exposing raw offsets |
| `export-json` | Export a binary changeset as versioned JSON |
| `materialize` | Materialize through the installed Dev CLI adapter and refresh inputs |
| `reconcile` | Reconcile a locked unknown Dev CLI effect before retry |
| `recover` | Recover an incomplete binary journal tail |

Use: `simplicio-fast changeset <action> --help`.

## Cross-repository validation

`simplicio-fast-cross-repo validate --file stack-lock.json --profile
`loop-standalone` validates pinned Mapper/Dev/Fast/Loop compatibility and
emits `simplicio.fast.cross-repo-receipt/v1` JSON. The operation is
read-only and fail-closed.

## Safe operating sequence

```text
--help -> build/ingest -> context/understand -> plan -> changeset validate
-> apply/delivery dry-run -> Dev CLI -> tests -> refresh -> rollout receipt
```

Fast does not replace Mapper extraction, Dev CLI source mutation, Runtime
authorization or Loop convergence. Do not read `.sfast` offsets directly.