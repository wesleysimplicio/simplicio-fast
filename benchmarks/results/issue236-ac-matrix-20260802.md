# Simplicio Fast epic AC matrix — 2026-08-02

This matrix is an evidence index, not a closure claim. `PROVEN` means the
named local check produced current evidence; `PARTIAL` means at least one
required acceptance criterion remains; `BLOCKED` means the missing proof is
owned by an external capability or service. Current master is `6ae8e64`;
the full Python suite passes `611 passed, 67 subtests` in 116.94s,
and release-integrity passes all checks.

| Issue | Current state | Current evidence | Remaining authoritative gate |
| --- | --- | --- | --- |
| #236 | PARTIAL | Full suite: 603 tests + 67 subtests; release-integrity pass; receipts below | Final AC matrix, full coverage, Linux/Windows assets, 10 worktrees/20 readers, installed S3/S4/S5 delivery |
| #237 | PARTIAL | `829a911`; Mapper 0.26.9 installed E2E, Mapper→SFAST sidecar, 20 concurrent readers, validated changed/deleted delta paths and scoped invalidation tests | Linux/Windows compatibility, physical worktree isolation, final coverage and installed package matrix |
| #238 | PARTIAL | `aa3ba71`; resident session verifies manifest version, source commit, conformance digest and executable digest; `issue238-windows-20260802-30.json` has 30 one-shot/resident samples, zero failures and one resident process; focused session tests | Unix/Windows transport matrix, crash/concurrency/installed package and final coverage |
| #239 | PARTIAL | `issue239-windows-20260801.json`; 30-run indexed receipts at 10k/100k/1M; `c06f34a` 20 readers | Cross-platform receipts, physical worktrees, installed package and final coverage |
| #240 | PARTIAL | `6de3eed`; identifier-boundary normalization, confidence pruning, frozen corpus recall@20=1.0, 32-to-4 token regression, exact tokenizer receipt | Provider tokenizer matrix, real-task recall/token matrix, warm latency and coverage |
| #241 | PARTIAL | `7be3c94`; `issue241-windows-20260802-30.json` with 30-run binary/JSON/journal/Dev CLI receipt; installed CLI tests | Linux/Windows byte parity, all operations, recovery/adversarial matrix and final coverage |
| #242 | PARTIAL | `b095cba`; 30-run 10k/100k receipts; `fb55789` 1M/10 receipt; `issue242-windows-20260802-1m-30.json` with 30 repetitions, parity, complete resources and passing gates | Linux receipt, CI regression gate and final coverage |
| #243 | BLOCKED | Local workflow contract/verifier pass; run `30723612138` has `startup_failure` and no jobs | GitHub Actions administrative/runner availability, then four native target builds and downloadable verification |
| #244 | PARTIAL | `3e89acf`, `b1b4f89`; parser contract, fuzz corpus, 91% line / 88.2% branch receipt | Frozen real-corpus parity, installed cross-platform matrix and final coverage |
| #246 | BLOCKED | Rust matrix receipts at 10k/100k/1M; Full cell reports `runtime_authorization_required` | Runtime-authorized Full, Loop standalone, real delivery tasks, concurrency and final regression gate |
| #247 | PARTIAL | `3515bb5`; C# multi-project/partial/test-symbol E2E, 4 tests | Roslyn/native relations, affected-project invalidation, installed Linux/Windows E2E and coverage |
| #248 | PARTIAL | `dffc0b1`; TS monorepo/project refs/aliases/TSX E2E, 4 tests | Native compiler relations, bounded invalidation, Node/React installed E2E and coverage |
| #249 | PARTIAL | `d1ac156`; multi-crate Cargo discovery/symbol E2E, 5 tests | Native parser parity, Cargo relation/invalidation E2E, installed matrix and coverage |
| #339 | PARTIAL | `6ae8e64`; typed `simplicio.fast.projection/v1` envelope plus Code parser, KnowledgeFacade and PrismArena producers, deterministic digest/handle and corruption/offset rejection tests | Incremental closure, installed SDK, Rust parity and cross-domain benchmark |

## Closure rule

No issue is closed by this document. A row can move to `PROVEN` only after its
remaining gate has a current receipt or test that covers the stated scope.
