# Benchmarks

This directory is intentionally separate from the `simplicio_fast` runtime package.

- `run.py` creates a temporary synthetic project.
- It does not alter the measured repository.
- It is not imported by the CLI or library.
- Generated results are ignored by Git.
- Recorded numbers describe one environment and are not product guarantees.

Run:

```bash
python benchmarks/run.py
```

For comparisons, use the same machine, Python version, repository, query and cache policy. Run at
least ten repetitions and retain raw results for wall time, CPU time, peak RSS and incremental
reuse.

`environment.peak_rss_kib` is normalized to KiB. POSIX uses the standard-library
`resource.getrusage`; Windows uses `GetProcessMemoryInfo` through `ctypes`, so no runtime
dependency is added. If the operating system cannot expose peak RSS, the benchmark still emits a
partial JSON receipt with schema `simplicio.fast.benchmark/v1`, `status: "partial"`,
`peak_rss_kib: null`, a deterministic `peak_rss_reason` code and `metrics_status: "partial"`.
