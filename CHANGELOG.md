# Changelog

## 0.4.8 - 2026-07-04

### Added
- Added read-only Install Doctor for Hermes activation/setup diagnostics.
- Added console entry point `hermes-a2a-bridge doctor-install`.
- Added `python -m hermes_a2a_bridge doctor-install`.
- Added fast local HTTP+JSON fake peer for smoke/release validation.
- Added docs for agent-friendly setup and local fake peer testing.

### Changed
- Tightened README and added AGENTS.md for lower-token agent-friendly onboarding.
- Moved detailed version history and interop narrative from README to docs/INTEROP.md, linked from README and AGENTS.md.

### Limitations
- Install Doctor is read-only by default; it does not mutate config or registry.
- Users on Hermes v0.18.0 may still need manual `plugins.enabled` activation.
- Fast fake peer is dev/test-only, local-only, and not real-world interop proof.
- Upstream Hermes PR #54150 is not required and is not merged.

## 0.5.0 - 2026-07-27

### Added
- Added `hermes a2a setup`, read-only `hermes a2a status`, `hermes a2a maintenance doctor`, `send --wait`, and `send --follow` operator controls.
- Added bounded pending-queue admission, deterministic idempotency for `message:send` and `message:stream`, and SQLite conflict-safe task creation.
- Added verified named-peer Agent Card endpoint caching with TTL and registry mutation invalidation; bearer tokens are never cached.
- Added `/health/live`, `/health/ready`, and authenticated `/status` operational endpoints.

### Fixed
- Fixed executor SIGABRT (-6) after extended uptime caused by nested asyncio
  event loop triggering C-extension abort during Hermes subprocess teardown.
  The executor now runs synchronously in a thread pool executor, completely
  avoiding `asyncio.create_subprocess_exec`. Cancellation is preserved via
  a thread-safe Popen handle store with direct `proc.kill()` support.
  (Issue #3, reported with thorough diagnosis by a user.)

### Limitations
- The bridge remains a deliberately small HTTP+JSON subset; it does not claim full A2A conformance, `/v1`, JSON-RPC, OAuth, signed Agent Cards, gRPC, or public tunneling.
- File-part defaults remain closed. Only explicitly enabled, pre-staged local stored file IDs are accepted; inline bytes, remote URL inbound references, arbitrary paths, and upload routes remain unsupported.
- SQLite coordination prevents local shared-database over-admission and duplicate execution, but it is not a distributed queue or broker.
- `send --wait` polls task state; `send --follow` requires a peer that supports the bridge's bounded SSE stream behavior.

## 0.4.7 (2026-06-28)

### Highlights

- Added A2A Peer Doctor diagnostics via `hermes a2a doctor` and `a2a_doctor_peer` for safe Agent Card compatibility checks before runtime operations.
- Added opt-in live probes (`--live-probe` / `live_probe=true`) that send one diagnostic `message:send` and optionally verify returned task lookup.
- Added opt-in streaming probes (`--live-probe --stream-probe` / `live_probe=true, stream_probe=true`) that send one diagnostic `message:stream` and read a bounded SSE response.
- Added Python 3.13 CI coverage.
- Tightened Python package metadata to `>=3.11,<4.0`.
- Added and validated GitHub Actions CI, package, and release-check workflows.
- Fixed an executor cancellation race.
- Documented Hermes v0.17.0 pip entry-point plugin discovery behavior and manual `plugins.enabled` activation path.

### Added

- Add GitHub Actions CI for tests, editable install verification, compile checks, and package builds.
- Add a manual release validation workflow that builds artifacts and smoke-tests the wheel without publishing.
- Add lightweight contributor guidance and GitHub issue/PR templates.
- Add A2A Peer Doctor diagnostics through `hermes a2a doctor` and `a2a_doctor_peer` for safe Agent Card compatibility checks before runtime operations.
- Add explicit opt-in Peer Doctor live probes through `hermes a2a doctor --live-probe` and `a2a_doctor_peer(live_probe=true)` to send one diagnostic text message and optionally verify returned task lookup.
- Add explicit opt-in Peer Doctor streaming probes through `hermes a2a doctor --live-probe --stream-probe` and `a2a_doctor_peer(live_probe=true, stream_probe=true)` to send one diagnostic text message through `message:stream` and read a bounded SSE response.

### Changed

- Document Hermes Agent v0.17.0 pip entry-point plugin discovery behavior and the manual `plugins.enabled` activation path for `a2a-bridge`.

### Limitations

- Peer Doctor does not prove full A2A conformance; it is metadata-only by default.
- Live probe proves only basic `message:send` and optional task lookup; it does not prove full A2A conformance.
- Stream probe proves only bounded parseable SSE for a basic diagnostic stream; it does not prove full A2A conformance.
- File-boundary posture remains closed by default.
- Hermes Agent upstream `plugins list` / `plugins enable` CLI discovery for pip entry-point plugins is pending separately and is not required to use this bridge.

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
