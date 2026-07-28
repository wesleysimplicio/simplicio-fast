# Native backend ownership and compatibility

Runtime owns native execution. Fast owns Python semantics, snapshots, context
selection, and the adapter contract used to ask Runtime for a verified native
operation. Fast does not install or invoke a local Rust toolchain.

The legacy compatibility ABI is `simplicio.fast-native/v1`. It remains available
only for controlled migration and rollback. CI may publish precompiled
compatibility artifacts, but consumers select them only after ABI, package
version, platform, size, source commit, and SHA-256 bindings all match.

| Target | Python | Precompiled compatibility artifact |
|---|---:|---:|
| Linux x86_64 | supported | CI-published and manifest-verified |
| Linux aarch64 | supported | CI-published and manifest-verified |
| macOS aarch64 | supported | CI-published and manifest-verified |
| Windows x86_64 | supported | CI-published and manifest-verified |
| Other targets | supported | rejected as `RUST_PLATFORM_UNSUPPORTED` |

`scripts/verify_native_bundle.py` verifies an extracted release asset without
Cargo or rustc. A missing, corrupt, incompatible, timed-out, or crashed binary
never produces a native success receipt. `auto` records an explicit Python
fallback reason, `rust` fails closed, and `python` remains complete. Inputs are
immutable values, and output is committed only after a valid response.

The canonical policy is `release-policy.json`; `pyproject.toml` is the sole
source of package version and dependency metadata.
