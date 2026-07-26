# Issue #2 validation receipt

Commit: `c160c6488cfb5e1baa1ff361b4b15b95e7d119ef`

Pull request: https://github.com/wesleysimplicio/simplicio-fast/pull/12 (OPEN, base `master`)

Mapper was refreshed after the verified patch. The final handoff was fresh and ready with
`pack_hash=2cd81e5381dc3ebb57be3e8a81c9df6acac8e704a31c73c96a0be4b2a1d928d6`, 102 symbols and
114 relationships; `.simplicio/` remains derived and ignored.

## Validation

| Gate | Result |
|---|---|
| Unit, integration and regression | `MEASURED| 7 tests passed: PYTHONPATH=src; python -m unittest discover -s tests -v` |
| Compile | `MEASURED| python -m compileall -q src tests benchmarks` passed |
| CLI/system | `MEASURED| build/search/context/impact/stats/doctor --json` passed; doctor reported SFAST001/v2 ready |
| Corruption/property-style | `MEASURED| truncation, checksum mutation and 20 deterministic random byte mutations were rejected with ValueError` |
| v1 migration | `MEASURED| frozen SFAST001/v1 fixture opens and exact lookup succeeds` |
| Coverage | `MEASURED| snapshot.py 85% line coverage; total 87%; branch coverage 72% from coverage.py --branch` |
| Benchmark | `MEASURED| 10 repetitions at 1k/10k/100k symbols; query speedup 10.35x, 7.11x and 10.16x respectively; Windows RSS/page-fault fields were null` |

The `simplicio-dev-cli` mutation attempt was `UNVERIFIED|blocked` because the Mapper handoff did
not initially include `snapshot.py` (`target_resolution_failed`); no files were changed by that
attempt. Runtime `simplicio doctor --json` did not complete within the bounded command window and
`simplicio status` reported idle; no Runtime proof or token/RSS estimate is claimed.
