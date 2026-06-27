# Interoperability ledger

This ledger records black-box A2A interoperability evidence for Hermes A2A Bridge. It is intentionally blunt: this bridge is still a local-first subset and is not full A2A conformance.

## Environment

- Date: 2026-06-26
- Bridge version under test: 0.4.6
- Official SDK packages: `a2a-sdk 1.0.3`, `a2a-sdk 1.1.0`
- SDK install location: isolated temporary venv, not a runtime project dependency
- SDK extra used: `a2a-sdk[http-server]`
- Additional isolated probe dependency: `uvicorn 0.38.0`
- Current A2A protocol target observed through SDK: 1.0 HTTP+JSON/REST
- Older compatibility surface observed through SDK: 0.3 REST compatibility adapter
- v0.3 local protocol expansion: structured JSON data parts and structured artifacts
- v0.3.1 compatibility tightening: Hermes emits SDK-compatible data parts without `kind`/`type`
- v0.3.8 file hardening: configuration, SQLite metadata, storage helpers, local CLI staging/metadata management, metadata-only remote URL references, authenticated local metadata/byte routes, stored Hermes-owned file artifact references with SSE replay, SDK file-part rejection fixtures, file-reference fixtures, old nullable-column migration coverage, lifecycle maintenance, integrity verification, orphan cleanup, and conservative repair tooling; runtime inbound A2A file parts remain unsupported
- v0.3.9 phase 8 verification: optional SDK 1.1.0 and 1.0.3 black-box integration tests were rerun against current behavior, existing fixtures were audited as still matching the runtime boundary, and `docs/FILE_BOUNDARY_STATUS.md` records the route/CLI/config/Agent Card gate.
- v0.3.10 phase 9 verification: external HTTP+JSON 1.0 discovery did not find a safe public no-auth runnable peer, `tests/raw_capture_harness.py` adds a deterministic sanitized raw capture utility, and `docs/EXTERNAL_INTEROP.md` records the search trail and blockers.
- v0.3.11 phase 10 verification: `tests/local_http_json_peer.py` adds a deterministic test-only local HTTP+JSON 1.0 compatibility peer, `tests/fixtures/blackbox/local_http_json_peer/` records sanitized discovery/send/stream/task/error/file-rejection fixtures, and the file boundary remains closed.
- v0.4.0 stored-ID inbound pass: `docs/INBOUND_FILE_PARTS_DESIGN.md` now reflects implemented pre-staged local stored file ID references behind closed-by-default gates. This is local-gated support only, not broad file-part conformance.
- v0.4.1 CLI stored-ID pass: `send --file-id` and `stream --file-id` build stored file ID reference parts only. They do not add `--file`, path reads, remote URL inbound references, inline bytes, auto-staging, downloads, or broad file-part conformance.
- v0.4.2 Hermes tool stored-ID pass: `a2a_send_message` accepts optional `file_ids` and builds stored file ID reference parts only. This is not broad file-part conformance.
- v0.4.3 local stored-ID e2e pass: `tests/fixtures/blackbox/stored_file_id_e2e/` records sanitized local open-gate client, CLI, tool, stream, task lookup, replay, and rejection evidence. This is local evidence only, not public peer compatibility or full file-part conformance.
- v0.4.4 Agent Card truthfulness pass: default and half-open Agent Cards remain quiet; when both stored-ID gates are enabled, `metadata.hermesA2ABridge.fileReferences` advertises only pre-staged local stored ID references and explicitly says inline bytes, URI references, remote fetch, arbitrary local paths, and uploads are unsupported. This is Hermes-specific metadata, not broad file-part conformance.
- v0.4.5 official/external interop refresh: optional SDK tests pass against isolated `a2a-sdk` 1.1.0 and 1.0.3 interpreters; both SDK models reject nested `file` objects, including Hermes stored `fileId`; public no-credential HTTP+JSON stored-ID peer capture remains absent.
- v0.4.6 release-candidate hardening audit: package metadata, docs wording, CLI/tool surfaces, fixture safety, config defaults, and smoke paths were checked without adding runtime protocol features.
- Unreleased Peer Doctor pass: `hermes a2a doctor` and `a2a_doctor_peer` fetch only a remote Agent Card and classify likely compatibility with Hermes' HTTP+JSON 1.x subset. The diagnostic does not send messages, stream, subscribe, cancel tasks, fetch files, download remote URLs, mutate registry state, implement `/v1`, implement JSON-RPC, or prove full A2A conformance.

## Commands used

Baseline:

```bash
python -m pytest
python -m pip install -e .
python -m compileall -q hermes_a2a_bridge tests
```

SDK setup, isolated outside project dependencies:

```bash
python -m venv %TEMP%\hermes-a2a-sdk-blackbox-1.1.0
%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe -m pip install "a2a-sdk[http-server]==1.1.0" uvicorn==0.38.0
python -m venv %TEMP%\hermes-a2a-sdk-blackbox-1.0.3
%TEMP%\hermes-a2a-sdk-blackbox-1.0.3\Scripts\python.exe -m pip install "a2a-sdk[http-server]==1.0.3" uvicorn==0.38.0
```

Optional repeatable integration tests:

```bash
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.0.3\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py
```

Normal project tests do not require internet or the SDK package. If `A2A_SDK_PYTHON` is not set, SDK integration tests skip cleanly.

## v0.3.9 phase 8 verification refresh

This pass reran the optional SDK black-box tests against current 0.3.9 behavior using isolated temporary venvs for both SDK versions:

```bash
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py -q
# 3 passed

set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.0.3\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py -q
# 3 passed
```

Covered paths:

- Hermes client to official SDK server text, structured data, mixed text/data, streaming, Agent Card discovery, and task lookup.
- Official SDK client to Hermes text, structured data, mixed text/data, streaming, Agent Card discovery, task lookup, and cancellation.
- Official SDK client file-part probes against Hermes for non-streaming and streaming request paths.

Observed boundary:

- SDK 1.1.0 file-like `raw`, `filename`, media-type, and URL parts remain rejected with `unsupported_part_type`.
- SDK 1.0.3 file-like `raw`, `filename`, media-type, and URL parts remain rejected with `unsupported_part_type`.
- Metadata-only remote URL fixtures still describe inert metadata records: no GET, HEAD, byte download, cache, or background fetch.
- Hermes-owned file-reference fixtures still describe already staged local bytes served only through authenticated local routes.
- Agent Card snapshots still advertise streaming and text/JSON modes, not broad file support.
- Error-envelope and SSE replay fixtures still match the documented 1.0 HTTP+JSON subset.

No black-box fixture payloads required refresh in this pass. The normal suite now includes explicit route, CLI, config, Agent Card, inbound file rejection, remote URL no-fetch, and fixture safety assertions for the file boundary.

## v0.3.10 phase 9 external interop discovery

This pass searched for public or safely runnable HTTP+JSON 1.0 A2A peers and did not complete a live real-peer exchange. The search found active official v1.0 specification and SDK work, including official samples with REST candidates, .NET REST samples, Java REST transport support, and Kagent A2A exposure. The practical blockers were public endpoint availability, required Google GenAI credentials for the Python REST dice sample, missing local `dotnet` for the .NET sample, and heavier ITK/Java/Kubernetes setup paths.

The full search methodology, candidate table, skipped real-peer reason, and raw capture harness usage are in `docs/EXTERNAL_INTEROP.md`. The fixture directory `tests/fixtures/blackbox/external_real_peer/` intentionally contains only `notes.md`; it does not include synthetic real-peer request/response fixtures.

The normal suite adds deterministic raw-capture tests that record Hermes client JSON requests and SSE responses while redacting Authorization headers, token-like query parameters, bearer strings, and local absolute paths. Optional SDK 1.1.0 and 1.0.3 integration paths remain unchanged and still pass when `A2A_SDK_PYTHON` points at an isolated SDK interpreter.

## v0.3.11 phase 10 deterministic local HTTP+JSON peer capture

This pass adds a normal-test local compatibility peer under `tests/local_http_json_peer.py`. It uses the existing aiohttp test stack and raw-capture sanitizers rather than the official SDK, because the official SDK path is already optional and should not be required for normal fixture validation.

Captured fixture directory:

- `tests/fixtures/blackbox/local_http_json_peer/`

Covered operations:

- Agent Card discovery.
- `/message:send` success.
- `/message:stream` SSE success.
- `/tasks/{task_id}` lookup.
- Structured `google.rpc.Status`-style error response.
- Unsupported file-part rejection with `bridgeCode: unsupported_part_type`.

The peer is explicitly a test-only local compatibility peer, not a public real-world peer and not a full conformance target. No runtime routes, `/v1`, JSON-RPC, OAuth, gRPC, tunnel behavior, remote URL fetch, SDK runtime dependency, or file-part acceptance were added.

## v0.4.5 official SDK file-shape refresh

This pass reran optional SDK integration against isolated temp interpreters:

```bash
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py -q
# 4 passed

set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.0.3\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py -q
# 4 passed
```

The added capability probe checked whether the official SDK request model can represent:

```json
{ "file": { "fileId": "file_..." } }
{ "file": { "uri": "https://example.com/report.pdf" } }
{ "file": { "bytes": "..." } }
```

Observed for both `a2a-sdk 1.1.0` and `a2a-sdk 1.0.3`:

- `Part` fields are `text`, `raw`, `url`, `data`, `metadata`, `filename`, and `media_type`.
- Nested `file` objects are rejected by the SDK parser with no field named `file`.
- SDK-native `url` and `raw` parts parse.
- Hermes rejects SDK-native `url` and `raw` runtime inputs with `unsupported_part_type`.

Conclusion: SDK-to-Hermes stored fileId interop is unsupported with these SDK request models. No stored fileId SDK request/response fixture was created, because constructing raw JSON by hand would not be official SDK-client evidence.

Captured evidence:

- `tests/fixtures/blackbox/external_official_interop/`

The same pass searched again for public/official no-credential HTTP+JSON peers. No public stored-ID peer exchange was completed. The candidate table is in `docs/EXTERNAL_INTEROP.md` and `tests/fixtures/blackbox/external_official_interop/public_peer_search_notes.md`.

## v0.3.7 file hardening fixtures

This pass adds fixture coverage without broadening runtime file support. File parts are still rejected on `/message:send` and `/message:stream`, `parts.allow_file_parts` remains false by default, and the Agent Card still advertises only text and JSON modes.

Captured and validated fixture locations:

- `tests/fixtures/blackbox/file_parts/sdk_1_1_0/`
- `tests/fixtures/blackbox/file_parts/sdk_1_0_3/`
- `tests/fixtures/blackbox/file_references/hermes_owned/`
- `tests/fixtures/blackbox/file_references/remote_url/`

SDK 1.1.0 and 1.0.3 can construct `raw`, `filename`, `url`, and multimodal media-type file-like parts at the model layer. Hermes rejects those requests with `unsupported_part_type`. For version-negotiated A2A 1.0 callers, the response uses a `google.rpc.Status`-style error with `ErrorInfo.metadata.bridgeCode: unsupported_part_type`; legacy callers keep receiving the bridge `success: false` error shape.

Hermes-owned file-reference fixtures document public metadata responses, task artifacts, artifact update events, SSE replay, and byte-route headers for files already staged through the local boundary. Remote URL fixtures document metadata-only references with `metadataOnly: true`, `bytesAvailable: false`, no local byte `uri`, and `file_bytes_unavailable` for the byte route. Remote URLs are not fetched, probed, cached, or downloaded.

The normal suite also covers an old 0.3.5-like on-disk `file_attachments` schema where `size_bytes`, `sha256`, and `storage_path` were non-null and `source_url` was absent. Current startup migrates that table, preserves existing local rows plus task/event/registry data, and permits new metadata-only remote URL rows with null byte fields.

Warning: these fixtures are interoperability and safety evidence, not full file support. Hermes still does not implement CLI `send --file`, CLI `stream --file`, JSON-RPC runtime routes, OAuth, signed Cards, public tunneling, or gRPC.

## v0.3.8 file lifecycle tooling

This pass adds local maintenance tooling only. `files verify`, `files scan`, `files cleanup-orphans`, and `files repair` operate against local SQLite metadata and the controlled storage root. They do not accept runtime A2A file parts, do not fetch remote URLs, do not add broad file-part protocol support, and do not add new server routes.

Integrity verification checks local byte presence, storage-root containment, metadata size, and SHA-256. Metadata-only remote URL records report `metadata_only` and no checksum verification is attempted because Hermes has no local bytes.

Orphan cleanup is dry-run by default and deletes only byte files under the controlled storage root when `--confirm` is supplied. Repair is also dry-run by default and removes only local-byte metadata rows whose bytes are missing when `--confirm` is supplied. It does not remove remote URL metadata, task rows, task event rows, registry rows, or file artifact references.

## SDK data-part capability check

Observed directly through the official `a2a-sdk` generated protobuf models in isolated venvs for both `a2a-sdk 1.1.0` and `a2a-sdk 1.0.3`:

- `Part` fields are `text`, `raw`, `url`, `data`, `metadata`, `filename`, and `mediaType`.
- Structured data parts are encoded as `{"data": ...}`. There is no `kind` or `type` field on the SDK `Part` model.
- `{"data": {"alpha": 1}}` parses as a message part.
- `{"data": [{"alpha": 1}]}` parses as a message part.
- Mixed text plus data messages parse when the data part omits `kind` and `type`.
- Task data artifacts parse when artifact parts use `{"data": ...}`.
- Streaming `TaskArtifactUpdateEvent` data artifacts parse when artifact parts use `{"data": ...}`.
- `kind: data` and `type: data` are rejected by the official SDK parser as unknown fields.
- The SDK protobuf layer accepts scalar `data` values because `data` is `google.protobuf.Value`; Hermes continues to reject non-object and non-array data at runtime to keep the v0.3 subset bounded.
- SDK file-capable fields such as `raw`, `url`, `filename`, and `mediaType` parse at the SDK model layer. Hermes still rejects file/raw/url/blob/multimodal parts clearly and does not implement file upload/download.

Low-risk compatibility fix made in v0.3.1:

- Hermes still accepts inbound `kind: data`, `type: data`, and no-discriminator data parts for local compatibility.
- Hermes-generated client requests, task history, artifacts, and SSE artifact updates now emit data parts as SDK-compatible `{"data": ...}` without `kind` or `type`.

## SDK 1.0.3 result

`a2a-sdk[http-server]==1.0.3` installed successfully in `%TEMP%\hermes-a2a-sdk-blackbox-1.0.3`. The existing optional SDK harness passed both directions:

```bash
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.0.3\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py -q
# 2 passed
```

Observed:

- SDK package name: `a2a-sdk`
- SDK version: `1.0.3`
- Protocol version observed: `1.0` for HTTP+JSON `supportedInterfaces`
- Server routes observed: `/.well-known/agent-card.json`, `/message:send`, `/message:stream`, `/tasks/{id}`, `/tasks/{id}:cancel`; 0.3 compatibility routes remain under `/v1` and are not implemented by Hermes
- Agent Card shape: `supportedInterfaces` with `protocolBinding: HTTP+JSON` and `protocolVersion: 1.0`
- Send envelope shape: top-level `message` with `messageId`, `role`, `parts`, and optional `metadata`
- Stream behavior: data-only SSE from the SDK server; Hermes SSE with `id`, `event`, and JSON `data` accepted by the SDK client
- Error envelope behavior: `google.rpc.Status`-style error object for negotiated 1.0 unsupported-message paths
- Task lookup behavior: `GET /tasks/{id}` returned a raw Task object
- Cancel behavior: SDK client cancellation against a submitted Hermes task succeeded

Captured SDK 1.0.3 fixtures:

- `tests/fixtures/blackbox/sdk_1_0_3/agent_card.json`
- `tests/fixtures/blackbox/sdk_1_0_3/message_send_request.json`
- `tests/fixtures/blackbox/sdk_1_0_3/message_send_response.json`
- `tests/fixtures/blackbox/sdk_1_0_3/task_lookup_response.json`
- `tests/fixtures/blackbox/sdk_1_0_3/stream_events.sse`
- `tests/fixtures/blackbox/sdk_1_0_3/error_response.json`
- `tests/fixtures/blackbox/sdk_1_0_3/sdk_client_sees_hermes_card.json`
- `tests/fixtures/blackbox/sdk_1_0_3/sdk_client_send_to_hermes_request.json`
- `tests/fixtures/blackbox/sdk_1_0_3/sdk_client_send_from_hermes_response.json`
- `tests/fixtures/blackbox/sdk_1_0_3/sdk_client_stream_from_hermes.sse`
- `tests/fixtures/blackbox/sdk_1_0_3/unsupported_part_error.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_0_3/data_message_send_request.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_0_3/data_message_send_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_0_3/data_artifact_stream_events.sse`
- `tests/fixtures/blackbox/data_parts/sdk_1_0_3/notes.md`

## Official a2a-samples result

The official `a2aproject/a2a-samples` repository was cloned to a temporary directory and `samples/python/agents/helloworld` was started locally with `uv run .`.

Discovery passed. The public Agent Card advertises:

- `protocolVersion: 0.3`
- `preferredTransport: JSONRPC`
- `supportedInterfaces[0].protocolBinding: JSONRPC`

Send, streaming, task lookup, and unsupported-part probes were skipped because this bridge intentionally does not implement JSON-RPC, `/v1` REST, or 0.3 send-envelope runtime support. The blocker is captured in `tests/fixtures/blackbox/a2a_samples/notes.md`.

Captured sample fixtures:

- `tests/fixtures/blackbox/a2a_samples/agent_card.json`
- `tests/fixtures/blackbox/a2a_samples/message_send_request.json`
- `tests/fixtures/blackbox/a2a_samples/message_send_response.json`
- `tests/fixtures/blackbox/a2a_samples/stream_events.sse`
- `tests/fixtures/blackbox/a2a_samples/error_response.json`
- `tests/fixtures/blackbox/a2a_samples/notes.md`

## Hermes client to official SDK server

Backed by a local Starlette REST server using the official `a2a.server.routes.create_rest_routes` and `create_agent_card_routes` helpers.

Passed:

- Agent Card discovery from `/.well-known/agent-card.json`.
- `message:send` with A2A 1.0 request headers and text part.
- Wrapped `{ "task": ... }` send response parsing.
- `GET /tasks/{id}` task lookup.
- `message:stream` over SSE where SDK emits data-only SSE frames with no `id` or `event` fields.
- Structured `google.rpc.Status`-style SDK errors.
- Request metadata preservation after adding client-side metadata passthrough.
- Data-only `message:send` from Hermes client to the SDK-backed harness using the SDK-compatible no-discriminator data part shape.
- Mixed text plus data `message:send` from Hermes client to the SDK-backed harness.
- Data artifact task responses emitted by the SDK-backed harness and parsed by Hermes.
- Streaming data artifact updates emitted by the SDK-backed harness and parsed by Hermes.

Captured fixtures:

- `tests/fixtures/blackbox/sdk_1_0_agent_card.json`
- `tests/fixtures/blackbox/sdk_1_0_message_send_request.json`
- `tests/fixtures/blackbox/sdk_1_0_message_send_response.json`
- `tests/fixtures/blackbox/sdk_1_0_task_lookup_response.json`
- `tests/fixtures/blackbox/sdk_1_0_stream_events.sse`
- `tests/fixtures/blackbox/sdk_1_0_error_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/data_message_send_request.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/data_message_send_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/mixed_text_data_send_request.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/mixed_text_data_send_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/data_artifact_task_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/data_artifact_stream_events.sse`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/unsupported_data_error.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/notes.md`

Fixture caveat: the data response and stream fixtures are sanitized local harness captures using official SDK server routes and SDK-compatible models. They are not captures from a public external SDK service.

## v0.3 structured data subset

Hermes A2A Bridge v0.3.0 added deterministic local support for structured JSON data parts and structured data artifacts while preserving the SDK black-box fixtures captured in v0.2.7. Version 0.3.1 tightened data-part emission to match the official SDK `Part` shape.

Supported locally:

- Incoming message parts with `kind: data` or `type: data`.
- Data values that are JSON objects or arrays.
- Mixed text plus data messages.
- Data-part metadata preservation.
- SDK-compatible outbound data parts with no `kind` or `type` discriminator.
- Executor prompt rendering as inert, pretty JSON in a delimited block.
- Plain text executor output as text artifacts.
- JSON object or array executor output as data artifacts when `artifacts.parse_json_output: true`.
- SSE `artifactUpdate` events containing data artifacts.
- Durable SQLite replay of data artifact events.
- Client, CLI `--json`, and Hermes tools preserving structured artifacts.

Still unsupported:

- File parts, binary/blob data, URI file references, and image/audio/video parts.
- Push notifications, OAuth, signed Agent Cards, public tunneling, JSON-RPC runtime, and gRPC.

New v0.3 fixtures:

- `tests/fixtures/a2a/message_send_data_request.json`
- `tests/fixtures/a2a/message_send_mixed_text_data_request.json`
- `tests/fixtures/a2a/task_completed_data_artifact.json`
- `tests/fixtures/a2a/artifact_update_data.json`
- `tests/fixtures/a2a/oversized_data_part_error.json`
- `tests/fixtures/blackbox/sdk_style_data_part_request.json`
- `tests/fixtures/blackbox/sdk_style_data_artifact_response.json`
- `tests/fixtures/blackbox/data_parts/sdk_1_1_0/`
- `tests/fixtures/blackbox/data_parts/sdk_1_0_3/`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/`

External live data-part interoperability is validated through the optional local official-SDK harness for `a2a-sdk 1.1.0` and repeated successfully with `a2a-sdk 1.0.3`. It is still local-first evidence, not a full conformance result or broad public-network validation.

## Official SDK client to Hermes server

Backed by the official `A2ACardResolver`, `ClientFactory`, and HTTP+JSON transport from `a2a-sdk 1.1.0`.

Passed after fixes:

- Agent Card discovery. The SDK accepted the public Hermes Card after its compatibility parser mapped legacy fields and selected the 1.0 `supportedInterfaces` entry.
- Non-streaming `message:send`. Hermes now returns the 1.0 `{ "task": ... }` envelope only when `A2A-Version: 1.0` is supplied.
- Streaming `message:stream`. The SDK accepted Hermes SSE frames with `id`, `event: message`, and JSON `data` fields.
- `GET /tasks/{id}`.
- `POST /tasks/{id}:cancel` against a submitted task.
- Unsupported file-part rejection. Hermes now returns an A2A 1.0 `google.rpc.Status`-style error when the SDK negotiates `A2A-Version: 1.0`.
- SDK client data-only `message:send` to Hermes.
- SDK client mixed text plus data `message:send` to Hermes.
- SDK client receiving Hermes data artifacts when the configured executor output is JSON.
- SDK client receiving Hermes streaming data artifact updates.

Original failures fixed in 0.2.6:

- SDK non-streaming send failed because Hermes returned a raw Task instead of a `SendMessageResponse` wrapper.
- SDK unsupported-part handling crashed on Hermes' legacy string-valued `error` field. Version-negotiated 1.0 errors now use nested `error.message` and `ErrorInfo` details.

Captured fixtures:

- `tests/fixtures/blackbox/hermes_server_agent_card_seen_by_sdk.json`
- `tests/fixtures/blackbox/sdk_client_message_send_to_hermes.json`
- `tests/fixtures/blackbox/sdk_client_message_send_from_hermes.json`
- `tests/fixtures/blackbox/sdk_client_stream_from_hermes.sse`
- `tests/fixtures/blackbox/sdk_client_unsupported_part_error.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/sdk_data_send_to_hermes_request.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/hermes_data_send_response_to_sdk.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/sdk_mixed_send_to_hermes_request.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/hermes_mixed_send_response_to_sdk.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/hermes_data_stream_to_sdk.sse`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/hermes_file_part_rejection_to_sdk.json`
- `tests/fixtures/blackbox/data_parts/sdk_client_to_hermes/notes.md`

## A2A 0.3 compatibility findings

Observed through `a2a-sdk 1.1.0` compatibility code, not implemented as Hermes runtime support.

- SDK 0.3 REST routes live under `/v1`, such as `/v1/message:send` and `/v1/message:stream`.
- SDK 0.3 send requests use a top-level `request` object and `content` array, not the 1.0 `message.parts` envelope.
- SDK 0.3 request JSON uses `message_id` and `configuration.blocking`.
- SDK 0.3 REST requests send `A2A-Version: 0.3` and `Content-Type: application/json`.
- Roles are still represented as `ROLE_USER`/`ROLE_AGENT` in the official compatibility path.
- SDK 0.3 stream status updates include a direct `final` field under `statusUpdate`; A2A 1.0 does not define that field.
- SDK 0.3 REST intentionally omits List Tasks.

Captured fixtures:

- `tests/fixtures/blackbox/compatibility_0_3_agent_card.json`
- `tests/fixtures/blackbox/compatibility_0_3_message_send.json`
- `tests/fixtures/blackbox/compatibility_0_3_notes.md`

Concrete Hermes deviations from 0.3:

- Hermes does not expose `/v1` routes.
- Hermes does not accept the 0.3 `request/content` send envelope.
- Hermes does not implement the 0.3 top-level `statusUpdate.final` stream shape.
- Hermes does not implement JSON-RPC runtime support.
- When discovery succeeds but a peer only advertises unsupported 0.3 behavior, send/stream/task route selection fails locally with:

```json
{
  "success": false,
  "code": "unsupported_protocol_version",
  "error": "Peer advertises A2A 0.3 REST behavior, which Hermes A2A Bridge does not implement. This bridge supports its documented A2A 1.0 HTTP+JSON text and data-part subset.",
  "protocol_version": "0.3"
}
```

CLI behavior:

- Human output prints the same clear unsupported 0.3 explanation as an error.
- `--json` prints only the structured JSON error payload.

## Intentional unsupported scope

- File parts: v0.4.0 accepts only pre-staged local stored file ID references when both `parts.allow_file_parts` and `parts.allow_file_id_references` are explicitly enabled. v0.4.1 adds CLI `send --file-id` and `stream --file-id` construction for that stored-ID shape only. v0.4.2 adds Hermes tool `file_ids` construction for that same stored-ID shape only. v0.4.3 adds local open-gate end-to-end stored-ID fixtures for client, CLI, and tool paths. v0.4.4 adds Hermes-specific gated Agent Card metadata for that same stored-ID subset only. v0.4.5 adds official SDK capability evidence showing SDK 1.1.0 and 1.0.3 cannot emit that nested stored-ID shape. v0.4.6 adds release-candidate audit coverage and keeps runtime acceptance unchanged. The v0.4 support boundary is documented in `docs/FILE_PARTS_DESIGN.md`, the gated inbound stored-file-ID behavior is documented in `docs/INBOUND_FILE_PARTS_DESIGN.md`, and the current route/CLI/config/Agent Card audit is documented in `docs/FILE_BOUNDARY_STATUS.md`; the bridge implements local CLI staging, metadata-only remote URL references, metadata management, authenticated local file retrieval routes for local bytes, Hermes-owned stored file artifact references for already staged or recorded files, inbound stored-ID fixtures, SDK rejection fixtures, old-database migration coverage, local lifecycle tooling, boundary audit tests, phase 9 raw capture tests, phase 10 local compatibility-peer rejection fixtures, local open-gate stored-ID e2e fixtures, gated Agent Card truthfulness fixtures, v0.4.5 official/external interop fixtures, and v0.4.6 fixture/package/smoke audit tests. Remote URL references are metadata-only local records and are not accepted as inbound message parts. Public external interop for file artifacts and inbound stored file ID references is not broadly validated yet.
- Data parts: supported for JSON objects and arrays.
- Structured artifacts: supported for text and JSON data parts.
- Push notifications: not implemented.
- OAuth and additional auth schemes: not implemented.
- Signed Agent Cards: not implemented.
- JSON-RPC runtime support: not implemented.
- gRPC runtime support: not implemented.
- Public tunneling or remote exposure: not implemented.

## Protocol matrix

| Capability | Status |
|---|---|
| Text parts | Yes |
| Data parts | Yes |
| Local file staging CLI | Yes, local metadata/storage only |
| Authenticated local file routes | Yes, staged files only; not A2A file parts |
| Stored file artifact references | Yes, Hermes-owned staged files only; external interop not broadly validated |
| Metadata-only remote URL references | Yes, local CLI/storage metadata only; no fetch, no HEAD, no byte route |
| File lifecycle maintenance | Yes, local verify/scan/cleanup/repair only |
| File-reference fixtures | Yes, Hermes-owned and metadata-only URL fixtures; not runtime file-part support |
| File parts | Stored local file IDs only, gated off by default |
| Structured artifacts | Yes |
| Streaming data artifact updates | Yes |
| Push notifications | No |
| OAuth | No |
| JSON-RPC runtime | No |
| gRPC | No |
| Signed Cards | No |

## Ambiguities and ecosystem variance

- The SDK's 1.0 REST server returns JSON responses as `application/json`, while the spec recommends `application/a2a+json`. Hermes tolerates both.
- The SDK emits data-only SSE frames. Hermes emits `id`, `event`, and `data` so local durable replay works. The SDK client accepted those extra SSE fields.
- The SDK's compatibility parser is forgiving of legacy Agent Card connection fields, but actual 0.3 operation routes are separate and incompatible with Hermes' 1.0-like routes.

## Next interoperability targets

- Run a small no-auth HTTP+JSON 1.0 real peer once one is available or once a deterministic official sample can replace LLM credentials with an echo executor.
- Use `tests/raw_capture_harness.py` to preserve exact raw Hermes client requests and peer responses for future interop runs.
- Continue any gated file-part design only after preserving current unsupported-file rejection fixtures and localhost/auth defaults.
- Track official SDK file model changes before attempting SDK-client stored-file-ID interop again.
