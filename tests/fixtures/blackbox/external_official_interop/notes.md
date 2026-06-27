# External Official Interop Fixtures

These fixtures document the v0.4.5 verification pass.

- Official SDK capability probes were run against isolated `a2a-sdk` 1.1.0 and 1.0.3 environments.
- Both SDK versions rejected nested `file` objects, including Hermes stored `fileId`, URI, and bytes shapes.
- Both SDK versions accepted SDK-native `url` and `raw` part fields. Hermes continues to reject those runtime shapes with `unsupported_part_type`.
- `sdk_to_hermes_agent_card.json` records the open-gate Hermes Agent Card metadata. It is Hermes-specific limited metadata for pre-staged local stored IDs only.
- No SDK-to-Hermes stored fileId request or response fixture exists because the SDK parser could not emit that shape.
- No public no-credential peer capture exists in this directory.

This directory contains verification evidence only. It does not add runtime dependencies, remote URL inbound support, inline byte support, upload routes, local path support, `/v1`, JSON-RPC, OAuth, public tunneling, or gRPC.
