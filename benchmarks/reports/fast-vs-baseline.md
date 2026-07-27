# Fast versus baseline benchmark

- Workload: 50 files × 20 functions
- Repetitions: 10
- Build wall time: 89.855 ms
- Baseline scan total wall time: 646.456 ms
- Baseline AST-reparse total wall time: 273.461 ms
- Fast Python total wall time: 334.486 ms
- Speedup versus scan: 1.933x
- Speedup versus AST reparse: 0.818x
- Estimated input tokens without Fast: 11000
- Estimated input tokens with Fast: 2500
- Estimated tokens saved: 8500 (77.27%)
- Baseline alteration total wall time: 226.541 ms
- Fast hot alteration total wall time: 143.004 ms
- Fast alteration + refresh total wall time: 876.387 ms
- Alteration speedup (hot): 1.584x
- Alteration speedup (with refresh): 0.258x
- Alteration estimated tokens without Fast: 11000
- Alteration estimated tokens with Fast: 50
- Rust standalone status: complete
- Rust standalone total wall time: 1436.6533 ms
- Rust standalone speedup versus Python Fast: 0.23282290863077404x
- Full standalone status: blocked (cross_repo_runtime_integration_missing)
- Loop standalone status: blocked (cross_repo_runtime_integration_missing)

Token values use `whitespace-v1-estimate`; they are not provider billing telemetry.
Rust standalone is a real subprocess/IPC read over a Python-built snapshot; it is not an end-to-end Rust build measurement.
Full/Loop cells are blocked with an explicit integration reason, not inferred as measured.
Alteration is a deterministic local fixture (locate + edit + py_compile), not an LLM/provider delivery run.
