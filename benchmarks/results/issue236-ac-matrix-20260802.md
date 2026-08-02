# Simplicio Fast epic AC matrix — 2026-08-02

This matrix is an evidence index, not a closure claim. `PROVEN` means the
named local check produced current evidence; `PARTIAL` means at least one
required acceptance criterion remains; `BLOCKED` means the missing proof is
owned by an external capability or service. Current master is `2f27829`;
release-integrity passes all checks. The full-suite subprocess failures in
this Windows shell are recorded separately and are not treated as functional
passes.

| Issue | Current state | Current evidence | Remaining authoritative gate |
| --- | --- | --- | --- |
| #236 | PARTIAL | Full suite: 603 tests + 67 subtests; release-integrity pass; receipts below | Final AC matrix, full coverage, Linux/Windows assets, 10 worktrees/20 readers, installed S3/S4/S5 delivery |
| #237 | PARTIAL | `829a911`; Mapper 0.26.9 installed E2E, Mapper→SFAST sidecar, 20 concurrent readers, validated changed/deleted delta paths and scoped invalidation tests | Linux/Windows compatibility, physical worktree isolation, final coverage and installed package matrix |
| #238 | PARTIAL | `2f27829`; resident session verifies manifest version, source commit, conformance digest, executable digest and pinned snapshot generation; `issue238-windows-20260802-30-r3.json` has 30 one-shot/resident samples, p50 0.0328ms resident vs 34.7761ms one-shot, one mapped generation, zero failures and one resident process; Rust 19 tests and focused Python 18 tests pass | Unix/Windows transport matrix, crash/concurrency/installed package and final coverage |
| #239 | PARTIAL | `issue239-windows-20260801.json`; 30-run indexed receipts at 10k/100k/1M; `c06f34a` 20 readers | Cross-platform receipts, physical worktrees, installed package and final coverage |
| #240 | PARTIAL | `6de3eed`; identifier-boundary normalization, confidence pruning, frozen corpus recall@20=1.0, 32-to-4 token regression, exact tokenizer receipt | Provider tokenizer matrix, real-task recall/token matrix, warm latency and coverage |
| #241 | PARTIAL | `7be3c94`; `issue241-windows-20260802-30.json` with 30-run binary/JSON/journal/Dev CLI receipt; installed CLI tests | Linux/Windows byte parity, all operations, recovery/adversarial matrix and final coverage |
| #242 | PARTIAL | `b095cba`; 30-run 10k/100k receipts; `fb55789` 1M/10 receipt; `issue242-windows-20260802-1m-30.json` with 30 repetitions, parity, complete resources and passing gates | Linux receipt, CI regression gate and final coverage |
| #243 | BLOCKED | Local workflow contract/verifier pass; run `30723612138` has `startup_failure` and no jobs | GitHub Actions administrative/runner availability, then four native target builds and downloadable verification |
| #244 | PARTIAL | `3e89acf`, `b1b4f89`; parser contract, fuzz corpus, 91% line / 88.2% branch receipt | Frozen real-corpus parity, installed cross-platform matrix and final coverage |
| #347 | PARTIAL | Operations projection now detects causal gaps/forks, preserves read-only authority, blocks inconsistent `complete` queries, and passes 8 focused tests | Canonical Mapper/Loop/Runtime streams, as-of/restart/replay E2E, Python/Rust parity, installed consumers, coverage and measured concurrency |
| #246 | BLOCKED | Rust matrix receipts at 10k/100k/1M; Full cell reports `runtime_authorization_required` | Runtime-authorized Full, Loop standalone, real delivery tasks, concurrency and final regression gate |
| #247 | PARTIAL | `3515bb5`; C# multi-project/partial/test-symbol E2E, 4 tests | Roslyn/native relations, affected-project invalidation, installed Linux/Windows E2E and coverage |
| #248 | PARTIAL | `dffc0b1`; TS monorepo/project refs/aliases/TSX E2E, 4 tests | Native compiler relations, bounded invalidation, Node/React installed E2E and coverage |
| #249 | PARTIAL | `d1ac156`; multi-crate Cargo discovery/symbol E2E, 5 tests | Native parser parity, Cargo relation/invalidation E2E, installed matrix and coverage |
| #339 | PARTIAL | typed Projection ABI, Python/Rust golden conformance including Unicode digest parity, federation, semantic diff/overlay, validation cache, Knowledge/Context/Operations projections, capability ranking, SDK facade, and synthetic cross-domain E2E are implemented across `340`–`349` child slices | Real Mapper/Runtime/Loop/Dev CLI contracts, cross-platform installed matrix, corpus/quality gates, resource benchmarks and rollout evidence |
| #340 | PARTIAL | ProjectionEnvelope now validates direct dataclass construction, payload digest, stable handles, generations and private fields; 18 focused projection/federation/E2E tests pass | Registry/producer compatibility matrix, cross-platform installed conformance, fuzz/coverage and real Mapper contract evidence |
| #343 | PARTIAL | Validation cache now distinguishes verified/provenanced reusable hits from diagnostic results, demotes conflicting results as nondeterministic, and emits affected-test reason paths; 9 focused tests pass | Canonical Dev CLI/Runtime receipts, full input/policy matrix, GC/lease/crash concurrency, Python/Rust parity, corpus false-hit rate and coverage |
| #341 | PARTIAL | Federation now rejects edges whose source/target repositories are outside the pinned member set and rejects duplicate edges; 11 focused federation/diff/E2E tests pass | Canonical Contract Registry/Stack Lock inputs, real cross-repo fixtures, bounded join quality, installed matrix, Python/Rust parity and coverage |
| #342 | PARTIAL | Semantic diff now validates add/update/remove shapes and rejects duplicate overlay records; 12 focused federation/diff/E2E tests pass | Source-hash/PlanDAG/Dev CLI parity, real cross-repo fixtures, test-selection quality, Python/Rust parity and coverage |
| #344 | PARTIAL | Knowledge projection now preserves conflict/tombstone lineage, rejects conflicted facts from retrieval, and exposes bounded metadata snapshot; 6 focused tests pass | Canonical MapperStore/Runtime adapters, trust/revocation matrix, real precedent corpus, installed consumers, coverage and cross-domain E2E |
| #345 | PARTIAL | Universal context now emits per-item digest/trust/freshness/selection boundaries, pins source generations, detects conflicting duplicates, enforces domain caps and separates wrapper/source budgets; 7 focused tests pass | Real Code/Knowledge/Operations adapters, tokenizer/trust/freshness matrix, tool schemas, external consumers, Python/Rust parity, corpus recall and coverage |
| #346 | PARTIAL | Capability ranking now separates hard-filter eligibility from advisory score, preserves owner policy eligibility/tenant scope, exposes score components, and labels unknown metrics; 8 focused tests pass | Real Loop/Runtime/Agent manifests, policy/health freshness, Pareto/quality dataset, Python/Rust parity, installed consumers and coverage |

## Closure rule

No issue is closed by this document. A row can move to `PROVEN` only after its
remaining gate has a current receipt or test that covers the stated scope.
