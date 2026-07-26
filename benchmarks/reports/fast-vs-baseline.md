# Fast versus baseline benchmark

- Workload: 50 files × 20 functions
- Repetitions: 10
- Build wall time: 92.399 ms
- Baseline scan total wall time: 207.131 ms
- Baseline AST-reparse total wall time: 266.376 ms
- Fast Python total wall time: 338.094 ms
- Speedup versus scan: 0.613x
- Speedup versus AST reparse: 0.788x
- Estimated input tokens without Fast: 11000
- Estimated input tokens with Fast: 2500
- Estimated tokens saved: 8500 (77.27%)
- Baseline alteration total wall time: 194.340 ms
- Fast hot alteration total wall time: 148.740 ms
- Fast alteration + refresh total wall time: 805.438 ms
- Alteration speedup (hot): 1.307x
- Alteration speedup (with refresh): 0.241x
- Alteration estimated tokens without Fast: 11000
- Alteration estimated tokens with Fast: 50
- Rust standalone status: complete
- Rust standalone total wall time: 1453.9108 ms
- Rust standalone speedup versus Python Fast: 0.23254074459038337x
- Full standalone status: blocked (cross_repo_runtime_integration_missing)
- Loop standalone status: blocked (cross_repo_runtime_integration_missing)

Token values use `whitespace-v1-estimate`; they are not provider billing telemetry.
Rust standalone is a real subprocess/IPC read over a Python-built snapshot; it is not an end-to-end Rust build measurement.
Full/Loop cells are blocked with an explicit integration reason, not inferred as measured.
Alteration is a deterministic local fixture (locate + edit + py_compile), not an LLM/provider delivery run.
