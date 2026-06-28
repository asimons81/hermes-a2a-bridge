# Changelog

## Unreleased

### Added

- Add GitHub Actions CI for tests, editable install verification, compile checks, and package builds.
- Add a manual release validation workflow that builds artifacts and smoke-tests the wheel without publishing.
- Add lightweight contributor guidance and GitHub issue/PR templates.
- Add A2A Peer Doctor diagnostics through `hermes a2a doctor` and `a2a_doctor_peer` for safe Agent Card compatibility checks before runtime operations.
- Add explicit opt-in Peer Doctor live probes through `hermes a2a doctor --live-probe` and `a2a_doctor_peer(live_probe=true)` to send one diagnostic text message and optionally verify returned task lookup.
- Add explicit opt-in Peer Doctor streaming probes through `hermes a2a doctor --live-probe --stream-probe` and `a2a_doctor_peer(live_probe=true, stream_probe=true)` to send one diagnostic text message through `message:stream` and read a bounded SSE response.

### Changed

- Document Hermes Agent v0.17.0 pip entry-point plugin discovery behavior and the manual `plugins.enabled` activation path for `a2a-bridge`.

## 0.4.6 (2026-06-26)

Initial release candidate for Hermes A2A Bridge.

### Highlights

- **Local-first Hermes A2A Bridge** — discover named remote agents and expose Hermes through a deliberately small A2A-shaped HTTP+JSON surface.
- **Message send, stream, tasks, subscribe/replay** — core A2A messaging operations for agent-to-agent communication over HTTP+JSON with SSE streaming.
- **Registry and CLI/tool/client surfaces** — Python client, CLI, and Hermes tool for agent discovery, message send, task management, and stream operations.
- **Gated stored file ID references** — pre-staged local file IDs can be referenced as `{file:{field}}` metadata only. Both file-part gates are closed by default.
- **Metadata-only file safety** — file part ingestion and sending are both gated off by default. The Agent Card advertises only stored-file-ID references (when explicitly enabled), never broad file-part support.
- **Closed defaults** — bearer auth required, remote hosts disabled, file gates closed, executor requires explicit configuration.
- **SDK compatibility findings** — validated against A2A SDK captured fixtures (1.0.3 and 1.1.0). Full SDK interop requires transport negotiation that the bridge does not implement.
- **Packaging and release artifact verification** — wheel and sdist build cleanly, wheel install smoke passes in isolated venv, bundled skill and plugin entry points register correctly.

### Limitations

- No full A2A conformance claim
- No `/v1` API versioning
- No JSON-RPC runtime
- No OAuth / signing / tunnel / gRPC
- No public stored-ID peer capture
- No inline bytes support
- No remote URL fetch
- No `--file PATH` CLI flag
- No file upload routes
- SDK interop requires transport negotiation not implemented by this bridge
