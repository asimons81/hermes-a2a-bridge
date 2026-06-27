# Local HTTP+JSON peer fixtures

Peer type: test-only local compatibility peer.

These fixtures document Hermes client traffic against a deterministic local HTTP+JSON 1.0-compatible peer implemented under `tests/`. The peer is not official SDK-backed, not official sample-backed, and not a public real-world peer. It exists to exercise the documented Hermes subset in normal tests without credentials, cloud services, public endpoints, or optional SDK packages.

Captured paths:

- Agent Card discovery.
- `/message:send` success.
- `/message:stream` SSE success.
- `/tasks/{task_id}` lookup.
- Structured error response.
- Unsupported file-part rejection.

The file-part fixture is a rejection fixture only. It does not mean Hermes accepts inbound file parts, sends files, fetches remote URLs, downloads bytes, exposes storage paths, or advertises broad file support.
