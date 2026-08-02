# Gemini instructions

Read [`AGENTS.md`](AGENTS.md) first and use [`docs/CLI_COMMANDS.md`](docs/CLI_COMMANDS.md)
for the full Fast capability map.

Run the matching help before every public command:

```bash
simplicio-fast --help
simplicio-fast <command> --help
simplicio-fast changeset <action> --help
simplicio-fast-cross-repo --help
```

Fast provides bounded semantic memory and guarded coordination. Mapper owns
canonical extraction, Dev CLI owns source edits, Runtime owns authorization
and Loop owns convergence. Preserve `schema` fields and never read `.sfast`
offsets or use stale context.