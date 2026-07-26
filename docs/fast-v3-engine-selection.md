# Fast V3 engine selection contract

This slice covers the policy boundary for issues #39 and #42. It does not claim
that a Rust core exists: the current repository remains Python-only until the
Rust implementation and conformance harness land.

select_engine() accepts a completed Rust health/capability probe and a separate
conformance result. In auto, Rust is selected only when both are true; Python
is selected only with an explicit, machine-readable fallback reason. An
explicit rust request raises EngineSelectionError instead of silently loading
Python. off selects no engine.

The function is pure and does not import either runtime. A future CLI/router
must call it before loading engine modules and persist
simplicio.fast.engine-selection/v1 alongside the normal generation receipt.
