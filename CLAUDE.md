# CLAUDE.md

Use [`AGENTS.md`](AGENTS.md) as the canonical repository contract.
Use [`docs/CLI_COMMANDS.md`](docs/CLI_COMMANDS.md) as the complete feature and
command index. Before executing a public operation, run:

```bash
simplicio-fast --help
simplicio-fast <command> --help
simplicio-fast changeset <action> --help
simplicio-fast-cross-repo --help
```

Preserve versioned JSON schemas, treat source files as authoritative, keep
writes explicit and stop on missing/incompatible Mapper, Dev CLI, Runtime or
Loop contracts. GitHub issue bodies use objective plus `Execution` for
implementation/deployment and tests; do not add an Acceptance Criteria section.