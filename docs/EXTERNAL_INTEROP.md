# External Interoperability Notes

Phase 9 scope: external HTTP+JSON 1.0 discovery, safe real-peer readiness audit, and reusable raw request/response capture. Phase 10 scope: deterministic local HTTP+JSON 1.0-compatible peer capture for normal black-box tests. Version 0.4.5 scope: official SDK file-shape capability refresh and public/official peer search refresh for the current stored-ID boundary. Version 0.4.6 scope: release-candidate hardening against that same evidence. The Unreleased Peer Doctor pass adds safe Agent Card compatibility diagnostics for users before they send, stream, subscribe, cancel, or try file references. Its optional live probe is explicit opt-in only and sends one tiny diagnostic text message to check basic `message:send`, plus task lookup if a task ID is returned. Its optional stream probe is a separate second opt-in requiring live probe; it sends one tiny diagnostic text message through `message:stream` and reads a bounded SSE response. These passes do not expand Hermes A2A Bridge protocol support.

Current public-peer result: No public no-auth HTTP+JSON 1.0 peer was confirmed runnable from this environment. No live public endpoint received Hermes bearer tokens or private credentials.

Current local-peer result: phase 10 captured deterministic request/response/SSE fixtures from Hermes client paths talking to a test-only local HTTP+JSON 1.0 compatibility peer. Runtime inbound file parts remain rejected by default, `parts.allow_file_parts` remains false by default, and the default Agent Card still does not advertise file support.

Version 0.4.0 adds gated inbound stored local file ID support as a local bridge feature; it does not add broad file-part conformance or new external peer claims. Version 0.4.1 adds CLI `send --file-id` and `stream --file-id` request construction for stored IDs only. Version 0.4.2 adds Hermes tool `file_ids` request construction for stored IDs only. Version 0.4.3 adds local open-gate stored-ID e2e fixtures under `tests/fixtures/blackbox/stored_file_id_e2e/`. Version 0.4.4 adds gated Hermes-specific Agent Card metadata for stored-ID references only when both stored-ID gates are enabled. Version 0.4.5 confirms that the probed official SDK models cannot emit Hermes nested stored `fileId` parts, and public no-auth peer capture for stored-ID inbound behavior is still absent. Version 0.4.6 does not refresh public-peer capture; it keeps that absence explicit. Peer Doctor may classify Agent Card metadata from public or local peers, but it is best-effort metadata analysis unless live probing is explicitly enabled. A passed live probe is basic `message:send` runtime evidence only. A passed stream probe means the peer accepted one basic `message:stream` diagnostic request and emitted a parseable bounded SSE response. Neither probe sends files, fetches files, subscribes, cancels, mutates registry state, or proves full conformance.

## v0.4.5 Official SDK Capability Probe

Isolated temp environments were used for `a2a-sdk[http-server]==1.1.0` and `a2a-sdk[http-server]==1.0.3`; the SDK remains optional and is not a runtime dependency.

Both SDK versions exposed the same `Part` fields:

- `text`
- `raw`
- `url`
- `data`
- `metadata`
- `filename`
- `media_type`

Both SDK versions rejected all nested file-object probes before any HTTP request could be emitted:

```json
{ "file": { "fileId": "file_..." } }
{ "file": { "uri": "https://example.com/report.pdf" } }
{ "file": { "bytes": "..." } }
```

The parser error was that `lf.a2a.v1.Part` has no field named `file`. SDK-native `url` and `raw` fields still parse, and Hermes rejects those runtime shapes with `unsupported_part_type` as designed.

Conclusion: official SDK-to-Hermes stored fileId interop is unsupported with the probed SDK request models. No SDK-to-Hermes stored fileId request/response fixture was created, because hand-authored JSON would not be official SDK-client evidence. The SDK-to-Hermes evidence that remains applicable is Agent Card discovery and rejection of SDK-native URL/raw file-like fields.

Captured fixture directory:

- `tests/fixtures/blackbox/external_official_interop/`

## Search Methodology

The search combined web search, GitHub repository inspection, and direct official repository tree inspection.

Queries and checks included:

- `A2A HTTP+JSON 1.0 supportedInterfaces protocolVersion 1.0 agent-card sample server`
- `site:github.com a2a supportedInterfaces protocolBinding HTTP+JSON protocolVersion 1.0`
- `a2a-samples HTTP+JSON protocolVersion 1.0`
- `/.well-known/agent-card.json supportedInterfaces HTTP+JSON`
- GitHub CLI repository and code searches for `supportedInterfaces`, `protocolBinding`, `HTTP+JSON`, `protocolVersion`, and `create_rest_routes`

Primary sources checked:

- A2A specification: https://github.com/a2aproject/A2A/blob/main/docs/specification.md
- Official samples: https://github.com/a2aproject/a2a-samples
- .NET SDK: https://github.com/a2aproject/a2a-dotnet
- Java SDK: https://github.com/a2aproject/a2a-java
- A2A 1.0 compatibility discussion: https://discuss.google.dev/t/the-a2a-1-0-milestone-ensuring-and-testing-backward-compatibility/352258
- Kagent A2A docs: https://kagent.dev/docs/kagent/examples/a2a-agents

## Candidate Findings

| Candidate | Protocol/version advertised | Transport advertised | Runnable locally? | Result |
|---|---|---|---|---|
| `a2aproject/a2a-samples` older `helloworld` path | 0.3 | JSON-RPC | Previously runnable | Rejected for Hermes live operations because Hermes does not implement JSON-RPC, `/v1`, or 0.3 send envelopes. Existing fixtures remain under `tests/fixtures/blackbox/a2a_samples/`. |
| `a2aproject/a2a-samples/samples/python/agents/dice_agent_rest` | Uses Python SDK REST app and an HTTP+JSON preferred transport | HTTP+JSON | Not safely runnable without credentials | Startup checks for Google GenAI credentials unless Vertex AI mode is enabled. No private credentials were supplied. |
| `a2aproject/a2a-samples/itk/agents/python/v10` | 1.0 and 0.3 interfaces | HTTP+JSON, JSON-RPC, gRPC | Not used as a small peer | It is part of an integration test cluster and depends on a development SDK branch. Useful future target, but heavier than this local verification pass. |
| `a2aproject/a2a-dotnet` samples, including BasicA2ADemo candidates | SDK advertises v1.0 JSON-RPC and HTTP+JSON/REST support | HTTP+JSON/REST available in samples | Blocked locally | `dotnet` was not installed in this environment. |
| `a2aproject/a2a-java` SDK | SDK tree includes REST client/server transport and multi-version tests | HTTP+JSON/REST available in SDK | Not attempted | Java sample stack is heavier than the test-only phase 9 harness path and was not needed for deterministic normal tests. |
| Kagent A2A docs | A2A exposure for created agents | A2A client invocation | Not a simple no-auth peer | Requires Kagent/Kubernetes setup rather than a small local public sample server. |
| General web examples and blog posts | Mostly illustrative cards | Usually JSON-RPC or examples only | Not runnable | These helped confirm vocabulary and v1.0 shape but did not provide safe public endpoints. |

## Real-Peer Result

Real-peer run result: skipped.

No safe public or simple local no-auth HTTP+JSON 1.0 peer was found and completed. Because no exchange was completed, `tests/fixtures/blackbox/external_real_peer/` intentionally contains only `notes.md`; it does not contain synthetic `agent_card.json`, send, stream, task, or error fixtures.

The v0.4.5 refresh repeats that result for stored-ID inbound behavior. The v0.4.6 release-candidate audit did not find or add new public-peer evidence. `tests/fixtures/blackbox/external_official_interop/public_peer_search_notes.md` records the current candidate table. It does not claim public peer capture.

## Raw Capture Harness

Phase 9 adds a reusable test-only harness in `tests/raw_capture_harness.py`.

It can:

- stand up a local aiohttp peer with Agent Card, send, stream, task, and fallback routes;
- record request method, path, headers, and JSON/text body;
- redact `Authorization`, cookie, proxy auth, and API-key headers;
- redact token-like query parameters such as `token`, `access_token`, `api_key`, `signature`, `secret`, and `password`;
- redact local absolute Windows and Unix-ish paths;
- return deterministic JSON responses and SSE frames for Hermes client tests.

Example use in tests:

```python
harness = RawCaptureHarness(json_response={"task": task_payload}, sse_events=[event_payload])
base = await harness.start()
try:
    task = await client.send_message(base, "hello", token="local-test-token")
    events = [event async for event in client.stream_message(base, "stream")]
    captured = harness.captures
finally:
    await harness.close()
```

The harness is intentionally not a runtime dependency and is not installed with the plugin.

## Sanitized Fixture Status

External real-peer fixtures:

- `tests/fixtures/blackbox/external_real_peer/notes.md`

No real-peer request/response/SSE fixtures are present because the real-peer run was skipped. Normal tests assert that the notes fixture contains no bearer tokens or local absolute paths.

Local deterministic peer fixtures:

- `tests/fixtures/blackbox/local_http_json_peer/agent_card.json`
- `tests/fixtures/blackbox/local_http_json_peer/discover_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/message_send_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/message_send_response.json`
- `tests/fixtures/blackbox/local_http_json_peer/message_stream_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/message_stream_events.sse`
- `tests/fixtures/blackbox/local_http_json_peer/task_lookup_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/task_lookup_response.json`
- `tests/fixtures/blackbox/local_http_json_peer/structured_error_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/structured_error_response.json`
- `tests/fixtures/blackbox/local_http_json_peer/file_part_rejection_request.json`
- `tests/fixtures/blackbox/local_http_json_peer/file_part_rejection_response.json`
- `tests/fixtures/blackbox/local_http_json_peer/notes.md`

The local peer is a test-only compatibility peer implemented in `tests/local_http_json_peer.py`. It is not official SDK-backed, not official sample-backed, and not a public real-world peer. It exists because official/public candidates remained blocked for normal credential-free tests, while optional SDK integration coverage already exists separately.

The local fixtures cover:

- Agent Card discovery.
- `/message:send` success.
- `/message:stream` SSE success.
- `/tasks/{task_id}` lookup.
- Structured error response.
- Unsupported file-part rejection with `bridgeCode: unsupported_part_type`.

Stored-ID local e2e fixtures:

- `tests/fixtures/blackbox/stored_file_id_e2e/`

Those fixtures are generated from a local Hermes bridge server with both stored-ID gates enabled. They are local evidence only and do not replace public real-peer capture.

External official interop fixtures:

- `tests/fixtures/blackbox/external_official_interop/sdk_capability_probe_1_1_0.json`
- `tests/fixtures/blackbox/external_official_interop/sdk_capability_probe_1_0_3.json`
- `tests/fixtures/blackbox/external_official_interop/sdk_to_hermes_agent_card.json`
- `tests/fixtures/blackbox/external_official_interop/sdk_to_hermes_uri_rejection_response.json`
- `tests/fixtures/blackbox/external_official_interop/sdk_to_hermes_inline_bytes_rejection_response.json`
- `tests/fixtures/blackbox/external_official_interop/public_peer_search_notes.md`
- `tests/fixtures/blackbox/external_official_interop/notes.md`

No `sdk_to_hermes_file_id_request.json` or `sdk_to_hermes_file_id_response.json` is present because the probed official SDK models could not emit the stored fileId shape.

The file-part fixture is a rejection fixture only. It does not indicate inbound file-part support.

## Compatibility Statement

Hermes A2A Bridge remains a bounded local-first HTTP+JSON subset:

- accepts text and bounded JSON object/array data parts;
- emits SDK-compatible data parts without local-only discriminators;
- parses raw Task and `{ "task": ... }` send responses;
- parses JSON and `application/a2a+json` responses;
- parses SSE frames with comments, blank delimiters, multiline data, unknown event names, and optional numeric IDs;
- supports deterministic local SDK black-box interop with `a2a-sdk 1.1.0` and `a2a-sdk 1.0.3`.

It is not a full A2A conformance implementation. Hermes still does not implement broad runtime inbound file parts, CLI `send --file`, CLI `stream --file`, `/v1`, JSON-RPC runtime support, OAuth, signed Cards, public tunneling, or gRPC. Inbound stored local file IDs are a gated local subset, not a broad file support claim.

## File Boundary

The file boundary remains closed:

- `/message:send` accepts only stored local file ID references when both stored-ID gates are enabled.
- `/message:stream` accepts only stored local file ID references when both stored-ID gates are enabled.
- SDK-style `{ "file": ... }`, `raw`, `url`, `filename`, image, audio, and video-like parts remain unsupported.
- `parts.allow_file_parts` remains false by default.
- CLI `send --file-id`, CLI `stream --file-id`, and Hermes tool `file_ids` can generate stored ID reference parts, but this is not public-peer validation and not broad file-part conformance.
- `files.auto_fetch_remote_urls` remains false by default.
- Metadata-only remote URL records remain inert; Hermes does not GET, HEAD, fetch, cache, or download remote URL bytes.
- The default Agent Card continues to advertise text and JSON modes, not file support. With both stored-ID gates enabled, the card may include Hermes-specific limited metadata for pre-staged local stored ID references only; this is not public-peer validation and not broad file-part conformance.
- Official SDK 1.1.0 and 1.0.3 request models cannot emit the nested Hermes stored `fileId` file part shape, so SDK-to-Hermes stored-ID interop is currently unsupported, not merely uncaptured.

## Next External Target

The best next live target is a small no-auth HTTP+JSON 1.0 sample with no LLM credentials. Good candidates are:

- a reduced official Python REST sample that replaces the LLM executor with a deterministic echo executor;
- an official .NET BasicA2ADemo run on a machine with `dotnet`;
- an ITK v10-only local REST agent once the development SDK dependency is pinned or packaged.

The next public interop pass should replace or supplement the test-only local peer fixtures with sanitized captures from one of those real peers once it can be run without private credentials or cloud dependencies.
