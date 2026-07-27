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

### Highlights

- **Non-blocking message sends** — `message:send` returns the accepted task immediately instead of blocking until executor completion. Agents and operators receive prompt feedback while tasks execute in the background. (#4)
- **Clean executor output** — Added `clean_result_text()` that strips ANSI escapes, Rich/TTY CLI framing, reasoning panels, session metadata, and noise banners from Hermes executor output. Custom executors emitting JSON with `resultText`/`result_text`/`final`/`answer`/`response` keys are detected and unwrapped automatically.
- **Executor stability** — Rewrote the executor to use synchronous `subprocess.Popen` in a thread pool instead of `asyncio.create_subprocess_exec`, fixing a SIGABRT (-6) crash after extended uptime. Cancellation preserved via thread-safe `ExecutorManager` with direct `proc.kill()` support. (#3)
- **Public readiness** — Added SECURITY.md with reporting and redaction guidance, fixed README Authorization header examples, labeled test-only peers as source-checkout-only, and aligned CONTRIBUTING.md with current CI workflow versions.
- **Cross-platform test hardening** — Executor and async tests now run reliably across Linux and Windows without platform-specific assumptions.

### Fixed
- Fixed executor SIGABRT (-6) after extended uptime caused by nested asyncio event loop triggering C-extension abort during Hermes subprocess teardown. The executor now runs synchronously in a thread pool executor, completely avoiding `asyncio.create_subprocess_exec`. Cancellation is preserved via a thread-safe Popen handle store with direct `proc.kill()` support. (Issue #3, reported with thorough diagnosis by a user.)
- Fixed message sends blocking until executor completion. Sends now return the accepted task immediately for prompt agent/operator feedback. (#4)
- Fixed Hermes CLI output carrying ANSI escapes, reasoning panels, and Rich/TTY session framing into A2A task result text. Output is now cleaned to machine-consumable plain text.

### Changed
- Cancelled executor handles are now removed atomically on cancellation so callers observe cancellation immediately rather than on next poll.
- Subprocess environment includes explicit `PYTHONIOENCODING=utf-8` for consistent output encoding across platforms.

### Limitations
- Non-blocking sends do not change task lifecycle semantics; tasks complete asynchronously and consumers must poll or subscribe for results.
- Output cleaning targets the verified Hermes CLI executor. Custom executors with unrecognized output structures may still produce unfiltered text.
- Executor thread-pool model requires the event loop to be running; synchronous callers must provide their own worker thread.

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
