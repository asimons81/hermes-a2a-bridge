# A2A 0.3 compatibility notes

Observed with the `a2a-sdk` 1.1.0 compatibility transport and server adapter on 2026-06-24.

- REST operations are rooted at `/v1`, such as `POST /v1/message:send` and `POST /v1/message:stream`.
- The send body uses `request`, not `message`; message content is under `content`, not `parts`.
- Captured protobuf JSON uses `message_id` and a `configuration.blocking` boolean.
- The compatibility transport sends `A2A-Version: 0.3` and `Content-Type: application/json`.
- Roles remain `ROLE_USER` and `ROLE_AGENT` in the official SDK compatibility representation.
- A 0.3 status update carries `final` directly on `statusUpdate`; 1.0 does not define that field.
- Legacy cards use top-level `url`, `preferredTransport`, and `protocolVersion`. The 1.1.0 resolver converts those fields into `supportedInterfaces` internally.
- The SDK intentionally omits List Tasks from its 0.3 REST adapter.

Hermes accepts several legacy card and message field variants, but it does not expose the `/v1` 0.3 REST binding or the 0.3 `request/content` operation envelope. This is a documented deviation, not a conformance claim.
