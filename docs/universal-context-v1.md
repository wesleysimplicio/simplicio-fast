# Universal Context Compiler v1

`compile_context()` consumes typed projection envelopes from Code, Knowledge and
Operations and emits the provider/tool-neutral
`simplicio.fast.universal-context/v1` packet. Inputs are ordered by projection
type and stable handle, scoped by repository, and bounded by item, byte and
estimated-token budgets. Truncation reasons are explicit.

The packet contains derived facts with producer provenance and marks
`authority=facts_only` and `instructions=false`. It does not execute an LLM,
convert retrieved content into trusted instructions, authorize tools, or
replace the existing Code context hot path. Trust/authority policy, Runtime
admission and external adapters remain outside Fast.
