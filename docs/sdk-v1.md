# Embeddable SDK v1

`ProjectionSDK` provides a small in-process Python facade for publish, scoped
delta compilation, query, snapshot, save/open, context compilation and
capability inspection. It delegates validation to the typed projection/store
contracts and preserves explicit repository/generation boundaries.

The SDK is provider-neutral and read-only with respect to source authority. It
does not require a global daemon or MCP server, does not expose mmap pointers,
and does not authorize effects. Rust crate parity, resident session transport,
async-safe adapters, installed cross-platform artifacts and release receipts
remain open gates for #348.

`query_async()` and `context_async()` run the same bounded local operations in
a worker thread and return deterministic results matching the synchronous API,
without shared mutable async state or a daemon requirement.
