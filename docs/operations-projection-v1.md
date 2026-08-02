# Operations projection v1

`OperationsProjection` ingests explicit versioned receipts and exposes a
bounded, deterministic read model for status/kind queries and snapshots. It
pins one repository and generation, rejects stale sequence regressions and
generation mixing, and reports incremental changed handles.

Fast does not read SQLite, own queues or journals, schedule work, grant leases,
reduce effects, or become completion authority. Mapper, Loop, Runtime, Dev CLI
and Resource Fabric remain canonical producers; their receipt contracts and
cross-platform operational fixtures are required before #347 can close.
