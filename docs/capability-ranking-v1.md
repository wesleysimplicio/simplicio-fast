# Advisory capability ranking v1

`CapabilityCandidate` and `rank_capabilities()` produce deterministic,
explainable `simplicio.fast.capability-fact/v1` records for skills, tools,
models, workers or execution profiles. The output reports matched/missing
capabilities, cost/latency facts, availability, trust and provenance; it never
authorizes, admits or routes a candidate. Agent, Loop and Runtime retain those
decisions.

Missing required capabilities receive an explicit reason and unavailable
candidates are penalized as facts rather than silently removed. Results are
bounded and sorted by score plus stable identity. Provider manifests and
cross-runtime parity remain required before #346 can close.
