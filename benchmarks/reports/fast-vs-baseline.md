# Fast versus baseline benchmark

- Workload: 50 files × 20 functions
- Repetitions: 10
- Build wall time: 109.249 ms
- Baseline scan total wall time: 206.701 ms
- Baseline AST-reparse total wall time: 344.910 ms
- Fast Python total wall time: 393.904 ms
- Speedup versus scan: 0.525x
- Speedup versus AST reparse: 0.876x
- Estimated input tokens without Fast: 11000
- Estimated input tokens with Fast: 2500
- Estimated tokens saved: 8500 (77.27%)

Token values use `whitespace-v1-estimate`; they are not provider billing telemetry.
Rust and Full/Loop cells remain pending until those engines/integrations are implemented.
