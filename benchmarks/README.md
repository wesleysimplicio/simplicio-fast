# Benchmarks

This directory is intentionally separate from the `simplicio_fast` runtime package.

- `run.py` creates temporary synthetic projects with 1k, 10k and 100k symbols.
- It does not alter the measured repository.
- It is not imported by the CLI or library.
- Generated results are ignored by Git.
- Recorded numbers describe one environment and are not product guarantees.

Run (the default is ten repetitions per workload):

```bash
python benchmarks/run.py
```

For a shorter local smoke run, retain the required ten repetitions and select one workload:

```bash
python benchmarks/run.py --sizes 1000 --repetitions 10
```

For comparisons, use the same machine, Python version, repository, query and cache policy. Run at
least ten repetitions and retain raw results for wall time, CPU time, peak RSS, page faults and
incremental reuse. A host that does not expose RSS/page-fault counters records `null`.
