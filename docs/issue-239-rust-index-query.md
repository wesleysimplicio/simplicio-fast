# Rust indexed query evidence

The Rust reader validates the persisted `exact`, `names`, `paths` and `kinds`
indexes when a snapshot is opened, then reuses the validated representation for
resident queries. Indexed queries do not call `SnapshotReader::symbols()`.

The benchmark `benchmarks/bench_rust_query_239.py` runs 30 warm repetitions at
10k, 100k and 1M symbols. The committed Windows receipt records the selected
index, candidates visited, records decoded, raw wall samples and process RSS.
The RSS gate is measured relative to the resident process after the warmup
query; mmap/index-open RSS is reported separately as the baseline.

Legacy snapshots or unindexed predicates retain a labeled scan fallback in the
planner receipt. A corrupt snapshot or out-of-bounds persisted index fails
closed during open/query validation.
