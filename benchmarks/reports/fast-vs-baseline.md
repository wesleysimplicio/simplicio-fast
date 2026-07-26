# Fast versus baseline benchmark

- Workload: 50 files × 20 functions
- Repetitions: 10
- Build wall time: 79.834 ms
- Baseline scan total wall time: 870.171 ms
- Baseline AST-reparse total wall time: 211.669 ms
- Fast Python total wall time: 273.541 ms
- Speedup versus scan: 3.181x
- Speedup versus AST reparse: 0.774x
- Estimated input tokens without Fast: 11000
- Estimated input tokens with Fast: 2500
- Estimated tokens saved: 8500 (77.27%)
- Baseline alteration total wall time: 170.931 ms
- Fast hot alteration total wall time: 120.463 ms
- Fast alteration + refresh total wall time: 665.520 ms
- Alteration speedup (hot): 1.419x
- Alteration speedup (with refresh): 0.257x
- Alteration estimated tokens without Fast: 11000
- Alteration estimated tokens with Fast: 50

Token values use `whitespace-v1-estimate`; they are not provider billing telemetry.
Rust and Full/Loop cells remain pending until those engines/integrations are implemented.
Alteration is a deterministic local fixture (locate + edit + py_compile), not an LLM/provider delivery run.
