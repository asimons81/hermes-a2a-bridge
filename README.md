# Hermes A2A Bridge

[![CI](https://github.com/asimons81/hermes-a2a-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/asimons81/hermes-a2a-bridge/actions/workflows/ci.yml)

> MCP lets agents use tools. A2A lets agents call other agents. Hermes A2A Bridge gives Hermes the second half.

`hermes-a2a-bridge` is a thin, local-first bridge for HTTP+JSON agent calls with text parts and bounded structured JSON data parts. It lets Hermes discover named remote agents and exposes Hermes itself through a deliberately small A2A-shaped surface. It does not claim full A2A compliance.

![Hermes A2A Bridge README hero showing local-first Agent-to-Agent messaging, gated file ID references, and secure Hermes integration.](docs/assets/hermes-a2a-bridge-hero.png)

Current protocol expansion release: **v0.4.6**.

## Install

Python 3.11 or newer is required.

```bash
python -m pip install -e .
hermes plugins enable a2a-bridge
hermes a2a init
```

The package exposes the `hermes_agent.plugins` entry point `a2a-bridge = hermes_a2a_bridge`.

## Enable Plugin

Hermes Agent v0.17.0 currently has a host-side UI gap: pip entry-point plugins load at runtime, but `hermes plugins list` and `hermes plugins enable` may not show them. If that happens, add `a2a-bridge` to `plugins.enabled` in `~/.hermes/config.yaml`. The runtime loader path, CLI command registration, tools, and bundled skill were verified with that workaround.

## Quickstart

```bash
hermes a2a init
hermes a2a card --json
hermes a2a serve
curl http://127.0.0.1:8765/.well-known/agent-card.json
```

The first run creates `~/.hermes/a2a/config.yaml` and `~/.hermes/a2a/tasks.sqlite3`. By default the bridge binds to `127.0.0.1:8765`, requires bearer auth for task endpoints, and uses the verified one-shot argv:

```text
hermes chat -q {prompt}
```

If you set `executor.command: null`, tasks fail cleanly with:

```text
No Hermes executor command configured. Set executor.command in ~/.hermes/a2a/config.yaml
```

## Local Server Example

```bash
hermes a2a serve
```

Example config snippet:

```yaml
server:
  host: 127.0.0.1
  port: 8765
  public_url: http://127.0.0.1:8765
  require_auth: true
  allow_remote_hosts: false
executor:
  command:
    - hermes
    - chat
    - -q
    - "{prompt}"
  cancel_grace_seconds: 3
retention:
  max_events_per_task: 500
  max_event_age_days: 30
  prune_on_startup: true
recovery:
  stale_task_after_seconds: 900
  recover_on_startup: true
  stale_working_state: TASK_STATE_FAILED
ownership:
  lease_seconds: 60
  heartbeat_interval_seconds: 10
  recover_expired_leases_on_startup: true
  expired_lease_state: TASK_STATE_FAILED
sqlite:
  busy_timeout_ms: 5000
  journal_mode: WAL
  synchronous: NORMAL
  maintenance_vacuum: false
cancellation:
  request_ttl_seconds: 300
  poll_interval_seconds: 0.5
observability:
  lease_warning_seconds: 20
  include_diagnostics_in_stats: true
faults:
  sqlite_retry_attempts: 3
  sqlite_retry_backoff_seconds: 0.05
streaming:
  poll_interval_seconds: 0.5
  max_replay_events: 1000
  replay_gap_status_code: 409
  replay_gap_error_code: replay_gap
parts:
  max_data_part_bytes: 65536
  allow_data_parts: true
  allow_file_parts: false
  allow_file_id_references: false
  allow_remote_url_file_references: false
  allow_inline_file_bytes: false
files:
  storage_dir: "~/.hermes/a2a/files"
  max_file_bytes: 10485760
  max_total_storage_bytes: 1073741824
  allowed_mime_types:
    - "text/plain"
    - "application/json"
    - "text/markdown"
    - "text/csv"
    - "application/pdf"
    - "image/png"
    - "image/jpeg"
  reject_unknown_mime: true
  allow_remote_url_references: true
  auto_fetch_remote_urls: false
  allow_inline_bytes: false
  max_inline_bytes: 0
  cleanup_deleted_task_files: false
  shard_depth: 2
artifacts:
  parse_json_output: true
  max_artifact_data_bytes: 65536
```

## curl Examples

Read the generated bearer token locally from `~/.hermes/a2a/config.yaml`, then:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/.well-known/agent-card.json
curl -X POST http://127.0.0.1:8765/message:send \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Hello","mediaType":"text/plain"}]}}'
curl -X POST http://127.0.0.1:8765/message:send \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"data":{"topic":"status","count":2}}]}}'
curl -X POST http://127.0.0.1:8765/message:send \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Summarize this","mediaType":"text/plain"},{"data":[{"name":"Ada"},{"name":"Grace"}]}]}}'
curl -N -X POST http://127.0.0.1:8765/message:stream \
  -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Hello","mediaType":"text/plain"}]}}'
curl -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  http://127.0.0.1:8765/tasks
```

## CLI Reference

```text
hermes a2a init
hermes a2a card [--json]
hermes a2a serve [--host HOST] [--port PORT]
hermes a2a token rotate [--show-token] [--json]
hermes a2a discover URL [--json]
hermes a2a doctor AGENT_OR_URL [--token TOKEN] [--timeout N] [--live-probe] [--live-probe-message TEXT] [--stream-probe] [--stream-probe-timeout N] [--stream-probe-max-events N] [--json]
hermes a2a registry add NAME URL [--token TOKEN] [--json]
hermes a2a registry list [--json]
hermes a2a registry remove NAME [--json]
hermes a2a files ingest PATH [--task-id TASK_ID] [--artifact-id ARTIFACT_ID] [--name NAME] [--mime-type MIME] [--metadata-json JSON] [--json]
hermes a2a files add-url URL [--name NAME] [--mime-type MIME] [--size-bytes N] [--sha256 SHA256] [--task-id TASK_ID] [--artifact-id ARTIFACT_ID] [--metadata-json JSON] [--json]
hermes a2a files list [--task-id TASK_ID] [--artifact-id ARTIFACT_ID] [--limit N] [--json]
hermes a2a files show FILE_ID [--json]
hermes a2a files delete FILE_ID [--delete-bytes] [--json]
hermes a2a files attach-artifact FILE_ID TASK_ID [--artifact-id ARTIFACT_ID] [--name NAME] [--json]
hermes a2a files fetch-metadata FILE_ID --agent AGENT_OR_URL [--token TOKEN] [--json]
hermes a2a files download FILE_ID OUTPUT_PATH --agent AGENT_OR_URL [--token TOKEN] [--json]
hermes a2a files verify FILE_ID [--json]
hermes a2a files scan [--json]
hermes a2a files cleanup-orphans [--dry-run] [--confirm] [--json]
hermes a2a files repair [--dry-run] [--confirm] [--json]
hermes a2a files stats [--json]
hermes a2a maintenance stats [--json]
hermes a2a maintenance prune-events [--json]
hermes a2a maintenance recover-stale [--json]
hermes a2a maintenance leases [--json]
hermes a2a maintenance cancellations [--json]
hermes a2a maintenance recover-leases [--json]
hermes a2a send AGENT_OR_URL MESSAGE [--file-id FILE_ID]... [--token TOKEN] [--json]
hermes a2a stream AGENT_OR_URL MESSAGE [--file-id FILE_ID]... [--token TOKEN] [--json]
hermes a2a subscribe TASK_ID [--agent AGENT_OR_URL] [--token TOKEN] [--last-event-id ID] [--json]
hermes a2a tasks [--agent AGENT_OR_URL] [--token TOKEN] [--json]
hermes a2a task TASK_ID [--agent AGENT_OR_URL] [--token TOKEN] [--json]
hermes a2a cancel TASK_ID [--agent AGENT_OR_URL] [--token TOKEN] [--json]
```

For example:

```bash
hermes a2a stream demo "Summarize the current task" --json
hermes a2a send demo "Summarize the staged report" --file-id file_abcdefghijklmnopqrstuv --json
hermes a2a stream demo "Summarize the staged report" --file-id file_abcdefghijklmnopqrstuv --json
hermes a2a subscribe TASK_ID --agent demo --json
hermes a2a subscribe TASK_ID --agent demo --last-event-id 12 --json
hermes a2a doctor demo --json
hermes a2a doctor demo --live-probe --json
hermes a2a doctor demo --live-probe --stream-probe --json
```

Notes:

- `--json` prints JSON only.
- `registry list --json` reports `hasToken` and never prints token values.
- `doctor` is metadata-only by default: it fetches only the peer Agent Card and reports whether the advertised metadata looks compatible with Hermes' HTTP+JSON 1.x subset. It does not send messages, open streams, mutate registry state, fetch files, download remote URLs, or prove full A2A conformance.
- `doctor --live-probe` is an explicit opt-in runtime check. It sends one small diagnostic text message, records whether `message:send` worked, and attempts `GET /tasks/{task_id}` only if the send response includes a task ID. The live probe does not send files, fetch files, cancel tasks, subscribe, stream, or prove full A2A conformance.
- `doctor --live-probe --stream-probe` is a second explicit opt-in runtime check. It sends one diagnostic text message through `message:stream`, reads a bounded SSE response, and reports event count, event types, observed task ID, and terminal status observation. Defaults are 10 seconds and 20 events. `--stream-probe` without `--live-probe` is skipped with `live_probe_required`. The stream probe does not send files, fetch files, cancel tasks, subscribe, mutate registry state, or prove full A2A conformance; it only shows that the peer accepted a basic streaming diagnostic request and emitted a parseable bounded stream.
- `send --json` returns the remote task plus `resultText` when the task message includes text; structured data artifacts are preserved in the task JSON.
- `stream --json` and `subscribe --json` print one `{id,event,data}` stream envelope per line with no extra prose.
- `send --file-id` and `stream --file-id` append stored file ID reference parts only, shaped as `{ "file": { "fileId": "file_..." } }`. The target server must explicitly enable both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`.
- `send --file` and `stream --file` are not supported. The CLI does not read local files, stage files automatically, fetch remote URLs, or embed file bytes for send/stream requests.
- Human output summarizes structured data parts, for example `[data part: object, 3 keys]`, instead of dumping large JSON by default.
- Python `stream_message()` and `subscribe_task()` yield the same `{id,event,data}` envelope. Pass `last_event_id` to `subscribe_task()` to resume.
- A terminal task replays any stored events newer than `Last-Event-ID` and closes. If no newer event exists, the server returns JSON `409 no_new_events` before opening SSE.
- If pruning removed history required by `Last-Event-ID`, the server returns JSON `409 replay_gap` before opening SSE. The Python client exposes the structured error and JSON CLI mode prints that object only.
- `token rotate` does not print the new token unless `--show-token` is used.

## Local File Staging, Artifacts, And Retrieval

Version 0.4.6 supports explicit local file staging, metadata-only remote URL file references, safe task artifact references for already recorded files, SSE replay of those references, authenticated HTTP retrieval routes for local staged bytes, black-box fixtures for stored Hermes-owned file references, metadata-only remote URL references, gated inbound stored file ID references, CLI `send --file-id` / `stream --file-id` request construction for stored IDs only, Hermes tool `file_ids` request construction for stored IDs only, local open-gate stored-ID end-to-end fixtures, gated limited Agent Card metadata for stored file ID references, local lifecycle maintenance tooling, a phase 8 file-boundary audit, phase 9/10 external interoperability discovery/capture audits, a v0.4.5 official SDK capability refresh, and a v0.4.6 release-candidate hardening audit:

```bash
hermes a2a files ingest ./report.txt --json
hermes a2a files add-url https://example.com/report.pdf --name report.pdf --mime-type application/pdf --json
hermes a2a files attach-artifact FILE_ID TASK_ID --json
hermes a2a task TASK_ID --json
hermes a2a files list --json
hermes a2a files show FILE_ID --json
curl -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  http://127.0.0.1:8765/files/FILE_ID/metadata
curl -H "Authorization: Bearer YOUR_LOCAL_TOKEN" \
  -OJ http://127.0.0.1:8765/files/FILE_ID
hermes a2a files fetch-metadata FILE_ID --agent http://127.0.0.1:8765 --token YOUR_LOCAL_TOKEN --json
hermes a2a files download FILE_ID ./downloaded-report.txt --agent http://127.0.0.1:8765 --token YOUR_LOCAL_TOKEN --json
hermes a2a files verify file_...
hermes a2a files scan --json
hermes a2a files cleanup-orphans --dry-run
hermes a2a files cleanup-orphans --confirm
hermes a2a files repair --dry-run
hermes a2a files delete FILE_ID --delete-bytes --json
hermes a2a files stats --json
```

`ingest` is an explicit local CLI action. It copies the file into the configured controlled storage root, records metadata in SQLite, computes SHA-256, enforces per-file size, MIME allowlist, unknown-MIME policy, and total storage quota, and returns safe public metadata. CLI JSON does not expose raw storage paths or the source path.

`add-url` is an explicit local CLI metadata action. It records an HTTP(S) remote URL file reference without fetching the URL, sending a HEAD request, downloading bytes, or creating background fetch work. Public output strips credentials, query strings, and fragments from `sourceUrl`, and may omit `sourceUrl` entirely when the host or path itself looks credential-bearing. The command rejects unsupported schemes such as `file://`, `ftp://`, `data:`, `javascript:`, Windows drive paths, UNC paths, and bare local paths.

`attach-artifact` references an existing staged local file or metadata-only remote URL reference from an existing local task artifact. It does not read a new path, alter file bytes, fetch remote URLs, send the file to a remote agent, or embed bytes in task JSON. Task artifacts and `artifactUpdate` SSE events preserve safe metadata such as `fileId`, name, MIME type, optional size, optional SHA-256, optional safe localhost file route URI for local bytes, optional safe remote `sourceUrl` for URL references, and attachment metadata. Human CLI output summarizes file artifacts as metadata, while `--json` preserves the structured file part.

`GET /files/{file_id}/metadata` returns safe public metadata only. `GET /files/{file_id}` returns bytes only for local metadata rows with controlled stored bytes, after bearer auth, storage-root validation, size validation, and checksum verification. For metadata-only remote URL references, the byte route returns `file_bytes_unavailable`. Neither route exposes `storage_path`.

File routes use the same protected-route auth behavior as task routes. With the default `server.require_auth: true`, missing or invalid bearer auth is rejected. If you deliberately set `server.require_auth: false` for local development, file routes follow that same local-dev behavior; there is no separate unauthenticated file bypass.

`files download` only works for locally staged bytes served by the authenticated byte route. Metadata-only remote URL references are intentionally not downloadable through Hermes because Hermes never fetched the bytes.

`files verify FILE_ID` checks a single local attachment record for missing bytes, unsafe storage paths, size mismatches, and SHA-256 mismatches. Metadata-only remote URL records return `metadata_only` and do not trigger GET, HEAD, fetch, cache, or download behavior.

`files scan` reports local storage health without modifying anything. It detects orphaned byte files under the controlled storage root, local metadata rows with missing bytes or integrity issues, remote URL rows that unexpectedly have local storage paths, and stored task/event artifact references whose file IDs no longer have metadata. Scan output avoids raw storage paths by default.

`files cleanup-orphans` deletes only untracked byte files under the controlled storage root, and only with `--confirm`. The default behavior is dry-run. `files repair` removes local-byte metadata rows whose bytes are missing, and only with `--confirm`; it does not remove metadata-only remote URL records and does not rewrite task artifacts, task events, tasks, or registry rows.

Inbound message file parts remain closed by default. If both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true` are set, `/message:send` and `/message:stream` accept only pre-staged local stored file ID references shaped as `{ "file": { "fileId": "file_..." } }`. Accepted references are rendered to the executor as safe metadata only and are stored under task `metadata.inputFileReferences`.

`send --file-id`, `stream --file-id`, and the Hermes `a2a_send_message` tool `file_ids` argument are conveniences for constructing stored file ID reference parts. They validate the opaque ID format locally, preserve normal text and data behavior, and do not look up local storage. The target server must explicitly enable both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`; the server remains authoritative for gates, existence, source type, byte availability, integrity, and rejection details.

Version 0.4.3 adds deterministic local end-to-end evidence for that stored-ID path under `tests/fixtures/blackbox/stored_file_id_e2e/`. Those captures use a local bridge server with both stored-ID gates enabled, stage files through existing controlled storage helpers, and cover client, CLI, tool, stream, task lookup, replay, and rejection behavior. They are local open-gate evidence only, not public peer compatibility evidence and not full file-part conformance.

Version 0.4.4 keeps the default Agent Card quiet: it advertises text and JSON modes only, with no file/image/audio/video modes and no upload, inline-byte, URI, or remote-fetch claim. When both stored-ID gates are enabled, the Agent Card adds Hermes-specific `metadata.hermesA2ABridge.fileReferences` describing only pre-staged local stored file ID references, requiring auth and the two config gates, and explicitly listing inline bytes, URI references, remote URL fetch, arbitrary local paths, and uploads as unsupported. This metadata is not a broad file-part conformance claim.

Version 0.4.5 refreshes official/external interop evidence without changing runtime file support. Isolated official SDK probes for `a2a-sdk` 1.1.0 and 1.0.3 showed that the SDK request model cannot emit nested `{ "file": { "fileId": "file_..." } }`, `{ "file": { "uri": "..." } }`, or `{ "file": { "bytes": "..." } }` parts; SDK-native `url` and `raw` fields remain rejected by Hermes. Public no-credential HTTP+JSON stored-ID peer capture remains absent. Fixtures live under `tests/fixtures/blackbox/external_official_interop/`.

Version 0.4.6 is a release-candidate audit pass. It adds package/metadata checks, a broad black-box fixture safety scan, and smoke coverage for import/config/Agent Card/health/CLI help/tool-schema surfaces. It does not add protocol features or expand runtime file acceptance.

### Current Boundaries

There is still no `send --file`, no `stream --file`, no tool path or URL argument, no remote URL inbound acceptance, no inline bytes, no arbitrary local path reads, no remote fetching, and no automatic artifact creation from inbound references. Metadata-only URL references remain local records and are not accepted as inbound message parts.

The current route, CLI, config, fixture, and Agent Card boundary is summarized in `docs/FILE_BOUNDARY_STATUS.md`.
External HTTP+JSON 1.0 peer discovery, raw capture harness notes, and deterministic local peer fixture capture are summarized in `docs/EXTERNAL_INTEROP.md`.

The file-reference fixtures live under `tests/fixtures/blackbox/file_references/`. SDK file-part rejection fixtures live under `tests/fixtures/blackbox/file_parts/` for `a2a-sdk 1.1.0` and `a2a-sdk 1.0.3`. These fixtures document safe metadata and rejection behavior only; they are not a claim of full file-part support.

## Tool Reference

Registered tools:

- `a2a_discover_agent`
- `a2a_doctor_peer`
- `a2a_send_message`
- `a2a_get_task`
- `a2a_list_tasks`
- `a2a_cancel_task`
- `a2a_registry_add`
- `a2a_registry_list`
- `a2a_registry_remove`

Behavior:

- Every tool returns a JSON string.
- Successful tool responses use `success: true`.
- Failed tool responses use `success: false` and redact bearer tokens.
- Registry list responses report `hasToken` only.
- `a2a_doctor_peer` accepts a registry name or URL and returns the same best-effort Agent Card compatibility diagnostic as `hermes a2a doctor`. By default it is metadata-only. `live_probe: true` sends one diagnostic `message:send`; `live_probe: true, stream_probe: true` sends one diagnostic `message:stream` and reads a bounded SSE response. `stream_probe: true` without `live_probe: true` is reported as skipped and never streams.
- Remote send/get/cancel responses include `task` and `resultText` when available. Structured artifacts are returned unchanged.
- `a2a_send_message` accepts the existing string `message` argument, an optional structured `data` object or array argument, and optional `file_ids: ["file_..."]` stored file ID references.
- Tool `file_ids` accepts stored Hermes file IDs only. It does not accept paths, URLs, URI references, inline bytes, raw file contents, or automatic local file staging. The target server must enable both stored-ID gates.
- Live SSE is not exposed as a Hermes tool. Hermes tool handlers return one final JSON string, so streaming remains available through the CLI and Python client without pretending tool calls can deliver live events.

## Security Model

- The server binds to `127.0.0.1` by default.
- Non-local binds are refused unless `server.allow_remote_hosts: true` is explicitly set.
- `/health` and `/.well-known/agent-card.json` are public; task endpoints require bearer auth by default.
- Tokens are never included in the Agent Card, registry list output, default CLI token rotation output, tool errors, or server errors.
- `text/plain` text parts and structured JSON data parts are accepted. File parts, binary blobs, URI file references, image/audio/video parts, push notifications, OAuth, and public tunneling are intentionally out of scope.
- Incoming file parts are supported only for pre-staged local stored file ID references when both stored-ID gates are enabled. Defaults remain closed. CLI `send --file-id`, CLI `stream --file-id`, and tool `file_ids` only construct that stored-ID reference shape. Remote URL file parts, inline bytes, arbitrary paths, CLI `send --file`, CLI `stream --file`, and tool path/URL/bytes inputs remain disabled. The v0.4 safety boundary is documented in `docs/FILE_PARTS_DESIGN.md`, the inbound design is documented in `docs/INBOUND_FILE_PARTS_DESIGN.md`, and the current audit is in `docs/FILE_BOUNDARY_STATUS.md`.
- The executor runs argv directly without a shell.
- Remote Agent Cards and responses should be treated as untrusted input.

## Interoperability Status

This release builds on the black-box A2A 1.0 interoperability foundation and adds bounded structured JSON data parts and structured artifacts. It remains a deliberately bounded subset and is not a full A2A conformance implementation.

### Black-box SDK status

Version 0.2.7 added the final tightening pass against the official Python SDK, using `a2a-sdk 1.0.3` and `a2a-sdk 1.1.0` installed in isolated temporary environments rather than as runtime dependencies. Version 0.3.0 added local structured data-part support. Version 0.3.1 validates that data shape against SDK 1.1.0 and 1.0.3 local harness paths and emits SDK-compatible data parts without `kind`/`type` discriminators. Version 0.3.2 adds internal file-attachment groundwork while keeping runtime file support disabled. Version 0.3.3 adds local CLI file staging and metadata management. Version 0.3.4 adds authenticated local file metadata and byte routes for already staged files only. Version 0.3.5 adds stored Hermes-owned file artifact references and SSE replay for staged files. Version 0.3.6 adds local metadata-only remote URL references. Version 0.3.7 adds SDK file-part rejection fixtures, metadata-only file-reference fixtures, and old nullable-column migration coverage. Version 0.3.8 adds file lifecycle maintenance, integrity verification, orphan cleanup, and conservative repair tooling. Version 0.3.9 refreshes optional SDK black-box verification and documents the route/CLI/config/Agent Card file boundary. Version 0.3.10 adds external HTTP+JSON 1.0 peer discovery notes plus a test-only raw capture harness. Version 0.3.11 adds deterministic local HTTP+JSON 1.0 compatibility-peer fixtures for discovery, send, stream, task lookup, structured errors, and file-part rejection. Version 0.3.12 adds the gated inbound stored-file-ID design and closed-by-default config placeholders. Version 0.4.0 implements gated inbound stored local file ID references while keeping defaults closed and remote URL, inline byte, path, and CLI file-send support disabled. Version 0.4.1 adds CLI `send --file-id` and `stream --file-id` stored-ID request construction only. Version 0.4.2 adds Hermes `a2a_send_message` tool `file_ids` stored-ID request construction only. Version 0.4.3 adds local open-gate stored-ID end-to-end verification and sanitized fixtures; it does not add path, URL, inline byte, upload, auto-staging, or public peer file conformance support. Version 0.4.4 adds gated limited Agent Card metadata for stored-ID references only; defaults remain quiet and this is not broad file-part conformance. Version 0.4.5 refreshes official SDK capability and public-peer search evidence and confirms SDK stored-file-ID interop is unsupported with the probed SDK models. Version 0.4.6 adds release-candidate package, docs, CLI, tool-schema, fixture-safety, config, and smoke audit tests without changing runtime behavior. The detailed deviation ledger is in `docs/INTEROP.md`.

Observed with official SDK 1.1.0:

- Hermes client can discover, send to, stream from, and look up tasks on an SDK-backed local HTTP+JSON server.
- Official SDK client can discover Hermes, send text messages, stream task updates, look up tasks, cancel a submitted task, and receive structured rejection for unsupported file parts.
- Hermes now returns the A2A 1.0 `{ "task": ... }` send envelope when the caller sends `A2A-Version: 1.0`; callers that omit that header keep receiving the legacy raw Task response.
- Hermes now returns `google.rpc.Status`-style errors for version-negotiated 1.0 unsupported-message paths while preserving legacy bridge errors for older callers.
- Hermes client can send data-only and mixed text plus data messages to an SDK-backed local harness, and parse SDK-shaped data artifacts and streaming data artifact updates.
- Official SDK client can send data-only and mixed text plus data messages to Hermes, and receive Hermes data artifacts when executor output is JSON.
- Official SDK `Part` data shape is `{"data": ...}`. Hermes accepts older local `kind: data` / `type: data` inbound forms but emits the SDK-compatible no-discriminator shape.

Observed with official SDK 1.0.3:

- Hermes client can discover, send to, stream from, and look up tasks on an SDK-backed local HTTP+JSON server.
- Official SDK client can discover Hermes, send text messages, stream task updates, look up tasks, cancel a submitted task, and receive structured rejection for unsupported file parts.
- The observed HTTP+JSON route, send envelope, data-only SSE behavior, and error envelopes matched the 1.1.0 black-box path for the 0.2.7 text-only subset.

Official `a2a-samples` status:

- The official `a2aproject/a2a-samples` `samples/python/agents/helloworld` server was cloned and started locally with `uv run .`.
- Discovery passed and the sample Agent Card was captured.
- Send, stream, and task operations were not run because the sample advertises `protocolVersion: 0.3`, `preferredTransport: JSONRPC`, and only a JSON-RPC interface. Hermes A2A Bridge does not implement JSON-RPC, `/v1` REST, or 0.3 send envelopes.

If a discovered peer only advertises unsupported 0.3 behavior, discovery still returns the card, but send/stream/task route selection now fails locally with `unsupported_protocol_version` instead of guessing a `/v1` route.

Optional SDK integration tests are skipped by default. To run them:

```bash
python -m venv %TEMP%\hermes-a2a-sdk-blackbox-1.1.0
%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe -m pip install "a2a-sdk[http-server]==1.1.0" uvicorn==0.38.0
set A2A_SDK_PYTHON=%TEMP%\hermes-a2a-sdk-blackbox-1.1.0\Scripts\python.exe
python -m pytest tests/test_official_sdk_integration.py
```

Known black-box deviations:

- Hermes does not expose the SDK's 0.3 REST compatibility routes under `/v1`.
- Hermes does not accept the 0.3 `request/content` send envelope.
- Hermes does not implement JSON-RPC runtime support, including official samples that only advertise JSON-RPC.
- Hermes still rejects file parts and multimodal parts. Structured JSON data parts are supported in the local documented subset.
- SDK protobuf models accept scalar `data` values, but Hermes intentionally keeps runtime data parts bounded to JSON objects and arrays.
- Hermes still does not implement push notification, OAuth, JSON-RPC, gRPC, or Agent Card signature support.

What works:

- Discovery from base URLs and direct `/.well-known/agent-card.json` URLs.
- Legacy Agent Cards with top-level `url` and A2A 1.0 cards with `supportedInterfaces`.
- A hybrid local Agent Card that keeps legacy `url`/`additionalInterfaces` fields and also declares an HTTP+JSON 1.0 `supportedInterfaces` entry.
- Text requests using either legacy `user`/`agent` roles or 1.0 `ROLE_USER`/`ROLE_AGENT` roles. Outbound requests include a generated `messageId`, `A2A-Version: 1.0`, and `application/a2a+json`.
- Request metadata is preserved for text messages when supplied through the Python client or SDK-compatible request body.
- External send responses returned as either a raw Task or the standard `{\"task\": {...}}` wrapper.
- JSON responses served with `application/json`, `application/a2a+json`, or other content types when the body is valid JSON.
- SSE comments, blank or whitespace-only delimiters, multiline `data`, unknown fields, unknown event names, and optional numeric event IDs.
- Nested `google.rpc.Status`-style errors, bridge errors, and non-JSON error bodies. Client errors retain status and safe structured payloads where available.

Intentional boundaries:

- Text and structured JSON object/array data parts are accepted. Stored local file ID references are accepted only when both stored-ID gates are enabled. File (`raw`, `url`, or `filename`), binary/blob data, scalar data values, image/audio/video parts, arbitrary paths, remote URL file parts, and unknown part shapes are rejected with explicit structured errors.
- Push notifications, OAuth, signed Agent Cards, JSON-RPC, gRPC, and public tunneling remain deferred.
- The server preserves its pre-1.0 raw Task response for `/message:send` rather than switching existing consumers to the 1.0 `{\"task\": ...}` response wrapper. The client accepts both shapes.
- Durable event IDs, `Last-Event-ID`, replay-gap errors, and terminal replay are bridge extensions. They are useful locally but are not proof of A2A conformance.
- The local Agent Card is hybrid for compatibility, not a schema-pure 1.0 card. Local HTTP is appropriate for the localhost default; production A2A deployments require HTTPS and a broader security review.

The deterministic external interoperability harness is in `tests/fake_external_a2a.py`, with fixtures in `tests/fixtures/a2a`. Run it with the rest of the suite:

```bash
python -m pytest tests/test_external_interop.py
python -m pytest
```

To exercise another HTTP+JSON server without saving credentials in output:

```bash
hermes a2a discover https://agent.example/.well-known/agent-card.json --json
hermes a2a doctor https://agent.example --json
hermes a2a doctor https://agent.example --live-probe --json
hermes a2a doctor https://agent.example --live-probe --stream-probe --json
hermes a2a send https://agent.example "Hello" --token "$A2A_TOKEN" --json
hermes a2a stream https://agent.example "Hello" --token "$A2A_TOKEN" --json
```

Treat a successful Doctor result or exchange as implementation-specific interoperability evidence, not a full conformance result. Peer Doctor is best-effort metadata analysis by default; it checks selected interface, protocol version, likely authentication requirements, streaming metadata, task-route assumptions, and Hermes-specific stored-file-ID metadata without probing runtime operations. With `--live-probe`, it sends one tiny diagnostic message and may confirm task lookup when a task ID is returned. With `--live-probe --stream-probe`, it also sends one tiny streaming diagnostic text message and reads a bounded parseable SSE response. These probes do not validate cancellation, subscribe, file references, OAuth, JSON-RPC, `/v1`, or full A2A conformance.

Phase 10 local HTTP+JSON peer fixtures live under `tests/fixtures/blackbox/local_http_json_peer/`. They are deterministic test-only compatibility captures, not public real-world peer evidence.

## Protocol Support Matrix

| Capability | Status |
|---|---|
| Agent Card | Yes |
| HTTP+JSON `/message:send` | Yes |
| HTTP+JSON `/message:stream` | Yes, SSE |
| Text parts | Yes |
| Data parts | Yes, JSON object/array with size limits |
| Structured artifacts | Yes, text or JSON data parts |
| Streaming data artifact updates | Yes |
| `GET /tasks/{id}` | Yes |
| `GET /tasks` | Yes |
| Cancel task | Yes; terminates its active subprocess when locally owned |
| Subscribe task | Yes |
| Durable replay | Yes, local SQLite |
| `Last-Event-ID` resume | Yes |
| Replay-gap detection | Yes |
| Multi-process live notification | Basic SQLite polling |
| Retention controls | Yes |
| Stale task recovery | Yes |
| Ownership leases | Yes |
| Executor heartbeats | Yes |
| Cooperative cancellation requests | Yes, SQLite-coordinated |
| Cross-process PID killing | No |
| SQLite retry/backoff | Basic, bounded retries for transient lock errors |
| Production clustering | No |
| Push notifications | No |
| Local file staging CLI | Yes, local metadata/storage only |
| Authenticated local file routes | Yes, staged files only; not file parts |
| Stored file artifact references | Yes, Hermes-owned staged files only |
| Metadata-only remote URL references | Yes, local CLI/storage metadata only; no fetch or byte serving |
| File-reference fixtures | Yes, black-box metadata and rejection fixtures only |
| File parts | Stored local file IDs only, gated off by default |
| Multimodal parts | No |
| OAuth | No |
| JSON-RPC | No |
| gRPC | No |
| Signed Agent Cards | No |
| External interop harness | Yes, deterministic local fake peer |
| Public tunnel setup | No |
| Full A2A compliance claim | No |

## Known Limitations

- This subset supports text parts and bounded structured JSON data parts.
- Streaming uses Server-Sent Events over local HTTP+JSON.
- Every task event is assigned a local SQLite event ID before it is emitted. SSE frames use `id`, `event: message`, and `data` fields.
- Durable replay survives server restarts when the same local database is used. `Last-Event-ID` resumes with events newer than the supplied ID.
- Pruning can invalidate old resume cursors. A cursor older than the retained task history returns `409 replay_gap`; clients must fetch current task state and begin a fresh subscription rather than assuming complete replay.
- Live fan-out buffers remain bounded and process-local. Subscriptions also poll SQLite for events written by another process; this is not a distributed message bus or production-grade clustering.
- Local file staging and metadata-only remote URL references are available through `hermes a2a files`, authenticated local file metadata/byte routes exist for staged local bytes, and stored file artifact references can be attached to local tasks. Runtime inbound file parts are limited to gated pre-staged local file ID references; the file-reference fixtures document this boundary.
- Queued tasks can be canceled before execution starts.
- Cancellation terminates, then kills after `executor.cancel_grace_seconds`, only the subprocess owned by that task in the current server process.
- A different server process cannot terminate work it did not start. For an unexpired lease, it records a cooperative request and returns `409 cancellation_requested` without marking the task canceled or claiming a process was killed.
- The owner polls cancellation requests alongside its heartbeat, acknowledges the request, terminates only its own local subprocess, persists the canceled status, completes the request, and releases its lease.
- Cancellation requests expire after `cancellation.request_ttl_seconds` when the owner does not acknowledge them. Recovery also expires requests addressed to an owner whose lease expired.
- An expired lease can be taken over for recovery or cancellation. A process never kills a PID or subprocess across an ownership boundary.
- Registry tokens are stored in local SQLite, not an encrypted secret store.

## Operations and Maintenance

Event history is retained independently from task and registry rows. By default, startup keeps the newest 500 events per task and removes events older than 30 days. Pruning never deletes tasks or registry entries.

Before execution, a server instance acquires a SQLite ownership lease for the task. While the subprocess runs, the server refreshes that lease every `ownership.heartbeat_interval_seconds`; completion, failure, and owner cancellation release it. A second instance cannot acquire an unexpired lease.

Lease diagnostics report lease age, heartbeat age, seconds until expiry, owner instance/PID, task state, and `lease_expiring_soon`, `heartbeat_stale`, and `expired` flags. The warning threshold is controlled by `observability.lease_warning_seconds`.

When a non-owner receives a cancellation call, it writes a request addressed to the current lease owner. The owner checks at `cancellation.poll_interval_seconds`. This is cooperative SQLite coordination: it never sends OS signals to another instance's PID and does not turn the bridge into a distributed executor.

On startup, expired leases are recovered first and their non-terminal tasks are marked failed with a persisted status event. Disable this with `ownership.recover_expired_leases_on_startup: false`. The older timeout-based recovery remains as a fallback and skips tasks protected by a live lease; disable it with `recovery.recover_on_startup: false`.

```bash
hermes a2a maintenance stats
hermes a2a maintenance prune-events --json
hermes a2a maintenance recover-stale --json
hermes a2a maintenance leases --json
hermes a2a maintenance cancellations --json
hermes a2a maintenance recover-leases --json
```

`stats` reports task, event, registry, lease, and file-attachment metadata counts; active, expired, and stale-heartbeat lease totals; pending and expired cancellation totals; event-ID bounds; database path and size; journal mode; busy timeout; SQLite warning count; and retry counters. It also includes the active retention, recovery, ownership, cancellation, file storage quota, observability, fault, and SQLite summaries. The storage root appears only in local maintenance output. `recover-stale` reports expired-lease and time-based recovery separately. `leases` emits diagnostic ownership rows, and `cancellations` lists request state without reason text or secrets.

SQLite connections request `busy_timeout`, WAL journaling, and NORMAL synchronous mode by default. Event writes, lease writes, cancellation writes, and task-state updates retry only transient SQLite busy/locked failures using the bounded `faults.sqlite_retry_*` settings. Permanent SQL errors are not swallowed; exhausted lock retries become a controlled database-busy error without exposing SQL, tokens, or tracebacks. Unsupported PRAGMA changes are retained as maintenance warnings rather than crashing otherwise usable database operations. Set `sqlite.maintenance_vacuum: true` only when explicit pruning should also run `VACUUM`.

Event IDs belong to one SQLite database. No external broker is required: active subscriptions poll that database at `streaming.poll_interval_seconds`, deduplicate against local live notifications, and cap each replay query at `streaming.max_replay_events`. WAL, retries, leases, and cooperative requests improve local multi-process behavior but are not production clustering.

## Roadmap

- Implement the bounded v0.4 file-part design in `docs/FILE_PARTS_DESIGN.md` without upload/download shortcuts that weaken localhost or auth defaults.
- More operational polish around local observability and packaging examples.

## Troubleshooting

- `hermes plugins enable a2a-bridge` says the plugin is missing:
  Add `a2a-bridge` to `plugins.enabled` in `~/.hermes/config.yaml` on Hermes Agent v0.17.0.
- `hermes a2a serve` refuses a bind host:
  Keep `127.0.0.1` or set `server.allow_remote_hosts: true` explicitly.
- Remote calls fail with auth errors:
  Check that the target bearer token was provided explicitly or saved in the registry entry.
- Tasks fail immediately with executor configuration errors:
  Set `executor.command` to an argv list containing `{prompt}`.
- `python -m pip` works but `pip` does not:
  Use `python -m pip` in environments where `pip` is not on `PATH`.

## Development

CI currently tests Python 3.11, 3.12, and 3.13 on Ubuntu, plus Python 3.11 on Windows. Optional official SDK tests require an explicitly configured isolated `A2A_SDK_PYTHON` interpreter and skip during normal runs when it is not set.

```bash
python -m pip install -e ".[test]"
python -m pytest
python -m compileall -q hermes_a2a_bridge tests
python -m build
```

Do not broaden file support or make full A2A conformance claims without a design pass, tests, and docs.
