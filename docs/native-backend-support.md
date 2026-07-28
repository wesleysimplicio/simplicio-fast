# Optional native backend support

The public ABI is `simplicio.fast-native/v1`. Python is always available.
Native artifacts are selected only when ABI, platform and SHA-256 manifest
bindings all match; every native failure degrades to Python with a reason code.

| Target | Python | Rust artifact |
|---|---:|---:|
| Linux x86_64 | supported | protocol supported; CI artifact required |
| Linux aarch64 | supported | protocol supported; CI artifact required |
| macOS aarch64 | supported | protocol supported; CI artifact required |
| Windows x86_64 | supported | protocol supported; CI artifact required |
| Other targets | supported | rejected as `RUST_PLATFORM_UNSUPPORTED` |

The source distribution does not require a Rust compiler. A missing, corrupt,
incompatible, timed-out or crashed native executable never becomes a successful
native receipt. Inputs are immutable values, and overlay output is committed by
the Python caller only after a valid response.
