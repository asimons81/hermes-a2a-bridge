# Hermes A2A Bridge

[![CI](https://github.com/asimons81/hermes-a2a-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/asimons81/hermes-a2a-bridge/actions/workflows/ci.yml)

> MCP lets agents use tools. A2A lets agents call other agents. Hermes A2A Bridge gives Hermes the second half.

`hermes-a2a-bridge` is a thin, local-first bridge for HTTP+JSON agent calls with text parts and bounded structured JSON data parts. It lets Hermes discover named remote agents and exposes Hermes itself through a deliberately small A2A-shaped surface. It does not claim full A2A compliance.

![Hermes A2A Bridge v0.4.7 hero showing the cyberpunk-styled Hermes figure, Peer Doctor diagnostics badge, Agent network, and A2A bridge imagery.](docs/assets/hermes-a2a-bridge-hero.png)

Current version: **v0.4.7**.

## Install

Python 3.11 or newer is required.

```bash
python -m pip install -e .
```

The package exposes the `hermes_agent.plugins` entry point `a2a-bridge = hermes_a2a_bridge`. Enable that plugin in Hermes before running `hermes a2a init`.

Check the package entry point and Hermes activation status with the read-only install doctor:

```bash
hermes-a2a-bridge doctor-install
hermes-a2a-bridge doctor-install --json
python -m hermes_a2a_bridge doctor-install --json
```

The helper does not edit user config. It reports whether the package imports, whether the `hermes_agent.plugins` entry point is installed, whether a `hermes` executable is on `PATH`, and whether a readable Hermes config already lists `a2a-bridge` under `plugins.enabled`.

## Enable Plugin

Hermes Agent v0.17.0 has a host-side discovery gap for pip entry-point plugins: the runtime loader can load `hermes_agent.plugins` entry points, but `hermes plugins list` and `hermes plugins enable a2a-bridge` only discover directory-based bundled/user plugins. In that host version, enable this package by adding the entry-point name to `plugins.enabled` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - a2a-bridge
```

After the next Hermes process starts, the host should mount `hermes a2a ...`, register the `a2a_bridge` toolset, and load the bundled `a2a-bridge` skill from the installed package. If `hermes a2a --help` still says `a2a` is an invalid command, verify that the same Python environment running `hermes` can see the package entry point:

```bash
python -c "import importlib.metadata as m; print([(e.name, e.value) for e in m.entry_points().select(group='hermes_agent.plugins')])"
```

The upstream Hermes Agent `hermes plugins list/enable` discovery improvement for pip entry-point plugins is pending separately; this bridge release does not depend on that upstream change. Manual `plugins.enabled` configuration is the documented and supported activation path.

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

## Peer Doctor Quick Checks

Before sending messages or streaming to a remote agent, check compatibility:

```bash
# Metadata-only: reads the remote Agent Card, classifies compatibility
hermes a2a doctor https://agent.example --json

# Opt-in live probe: sends one diagnostic text message via message:send
hermes a2a doctor https://agent.example --live-probe --json

# Opt-in stream probe: also sends one diagnostic via message:stream, reads bounded SSE
hermes a2a doctor https://agent.example --live-probe --stream-probe --json
```

- **Metadata-only** is the default. It fetches the peer Agent Card only and reports whether the advertised metadata looks compatible with Hermes' HTTP+JSON 1.x subset. No messages are sent.
- **Live probe** (`--live-probe`) sends one small diagnostic text message and attempts `GET /tasks/{task_id}` if the send response includes a task ID. Requires explicit opt-in.
- **Stream probe** (`--stream-probe`) requires `--live-probe`. Sends one diagnostic text message through `message:stream`, reads a bounded SSE response (default 10s timeout, 20 events max). Requires explicit opt-in. `--stream-probe` without `--live-probe` is skipped.
- These probes do **not** send files, fetch files, subscribe, cancel, mutate registry state, or prove full A2A conformance.

The same checks are available as a Hermes tool (`a2a_doctor_peer`) with `live_probe` and `stream_probe` boolean arguments.

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
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Hello","mediaType":"text/plain"}]}}'
curl -X POST http://127.0.0.1:8765/message:send \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"data":{"topic":"status","count":2}}]}}'
curl -X POST http://127.0.0.1:8765/message:send \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Summarize this","mediaType":"text/plain"},{"data":[{"name":"Ada"},{"name":"Grace"}]}]}}'
curl -N -X POST http://127.0.0.1:8765/message:stream \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{"message":{"messageId":"replace-with-a-uuid","role":"ROLE_USER","parts":[{"text":"Hello","mediaType":"text/plain"}]}}'
curl -H "Authorization: Bearer *** \
  http://127.0.0.1:8765/tasks
```

## CLI Reference

```text
hermes-a2a-bridge doctor-install [--config PATH] [--json]
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
- `hermes-a2a-bridge doctor-install` is a standalone package helper; it is available before the Hermes host mounts `hermes a2a ...`.
- `registry list --json` reports `hasToken` and never prints token values.
- `--file-id` appends stored file ID reference parts only, shaped as `{ "file": { "fileId": "file_..." } }`. The target server must explicitly enable both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`.
- `send --file` and `stream --file` are **not** supported. The CLI does not read local files, stage files automatically, fetch remote URLs, or embed file bytes for send/stream requests.
- `token rotate` does not print the new token unless `--show-token` is used.

## Local File Staging and Retrieval

Hermes can stage local files and record metadata-only remote URL references through explicit CLI commands. These are local operations only; inbound A2A file parts remain closed by default.

```bash
# Stage a local file into controlled storage
hermes a2a files ingest ./report.txt --json

# Record a metadata-only remote URL reference (no fetch, no HEAD, no download)
hermes a2a files add-url https://example.com/report.pdf --name report.pdf --mime-type application/pdf --json

# Attach an already-staged file to a task artifact
hermes a2a files attach-artifact FILE_ID TASK_ID --json

# List, inspect, verify, scan, and maintain staged files
hermes a2a files list --json
hermes a2a files show FILE_ID --json
hermes a2a files verify FILE_ID --json
hermes a2a files scan --json
hermes a2a files stats --json

# Authenticated local file routes (require bearer token)
curl -H "Authorization: Bearer *** \
  http://127.0.0.1:8765/files/FILE_ID/metadata
curl -H "Authorization: Bearer *** \
  -OJ http://127.0.0.1:8765/files/FILE_ID

# Clean up orphaned bytes or repair missing-byte metadata (dry-run by default)
hermes a2a files cleanup-orphans --dry-run
hermes a2a files repair --dry-run
```

**Current file boundaries:**

- Inbound file parts are **closed by default**. `parts.allow_file_parts: false` and `parts.allow_file_id_references: false`.
- When both gates are enabled, only pre-staged local stored file ID references (`{ "file": { "fileId": "file_..." } }`) are accepted on `/message:send` and `/message:stream`.
- No inline bytes, no arbitrary local paths, no remote URL inbound references, no uploads, no auto-fetching.
- CLI `send --file-id` and `stream --file-id` construct stored-ID reference parts only. They do not read files, fetch URLs, or embed bytes.
- `files.auto_fetch_remote_urls` remains `false` by default. Metadata-only URL references are inert records.
- The Agent Card defaults to quiet (text/JSON modes only) and only advertises file references when both stored-ID gates are enabled — and even then, only for pre-staged local IDs.

Full details: [docs/FILE_BOUNDARY_STATUS.md](docs/FILE_BOUNDARY_STATUS.md). Design history: [docs/FILE_PARTS_DESIGN.md](docs/FILE_PARTS_DESIGN.md), [docs/INBOUND_FILE_PARTS_DESIGN.md](docs/INBOUND_FILE_PARTS_DESIGN.md).

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
- `a2a_doctor_peer` accepts a registry name or URL. By default it is metadata-only. `live_probe: true` sends one diagnostic `message:send`; `live_probe: true, stream_probe: true` sends one diagnostic `message:stream` and reads a bounded SSE response. `stream_probe: true` without `live_probe: true` is skipped.
- Remote send/get/cancel responses include `task` and `resultText` when available. Structured artifacts are returned unchanged.
- `a2a_send_message` accepts a string `message`, optional structured `data` (object/array), and optional `file_ids: ["file_..."]` for stored file ID references only. No paths, URLs, URIs, inline bytes, or raw content.
- Live SSE is not exposed as a Hermes tool. Streaming remains available through the CLI and Python client.

## Security Model

- The server binds to `127.0.0.1` by default.
- Non-local binds are refused unless `server.allow_remote_hosts: true` is explicitly set.
- `/health` and `/.well-known/agent-card.json` are public; task endpoints require bearer auth by default.
- Tokens are never included in the Agent Card, registry list output, default CLI token rotation output, tool errors, or server errors.
- `text/plain` text parts and structured JSON data parts are accepted. File parts, binary blobs, URI file references, image/audio/video parts, push notifications, OAuth, and public tunneling are intentionally out of scope.
- Incoming file parts are supported only for pre-staged local stored file ID references when both stored-ID gates are enabled. Defaults remain closed.
- The executor runs argv directly without a shell.
- Remote Agent Cards and responses should be treated as untrusted input.

## Interoperability Status

This bridge is a deliberately bounded HTTP+JSON 1.0 subset. It is **not** full A2A conformance.

**What works:**
- Discovery, text and structured JSON data send/stream, task lookup, cancel, subscribe, durable replay
- Black-box interop with official `a2a-sdk` 1.0.3 and 1.1.0 (isolated temp interpreters, not runtime deps)
- Structured JSON data parts and artifacts compatible with official SDK `Part` shape
- Gated stored local file ID references (Hermes-specific, closed by default)
- Peer Doctor diagnostics (metadata-only by default; live/stream probes are explicit opt-in)

**What is intentionally unsupported:**
- `/v1` routes, JSON-RPC runtime, gRPC
- OAuth, signed Agent Cards, public tunneling
- Inline bytes, arbitrary local path reads, remote URL fetching, upload routes
- CLI `send --file`, `stream --file`
- Full A2A conformance claims

Detailed version history, black-box evidence, and SDK deviation ledger: [docs/INTEROP.md](docs/INTEROP.md). External peer search notes: [docs/EXTERNAL_INTEROP.md](docs/EXTERNAL_INTEROP.md).

## Protocol Support Matrix

| Capability | Status |
|---|---|
| Agent Card | Yes |
| HTTP+JSON `/message:send` | Yes |
| HTTP+JSON `/message:stream` | Yes, SSE |
| Text parts | Yes |
| Data parts | Yes, JSON object/array with size limits |
| Structured artifacts | Yes |
| Streaming data artifact updates | Yes |
| `GET /tasks/{id}` | Yes |
| `GET /tasks` | Yes |
| Cancel task | Yes (local subprocess only) |
| Subscribe task | Yes |
| Durable replay | Yes, local SQLite |
| `Last-Event-ID` resume | Yes |
| Replay-gap detection | Yes |
| Retention controls | Yes |
| Stale task recovery | Yes |
| Ownership leases | Yes |
| Cooperative cancellation | Yes, SQLite-coordinated |
| Local file staging CLI | Yes, local metadata/storage only |
| Authenticated local file routes | Yes, staged files only |
| Stored file artifact references | Yes, Hermes-owned staged files only |
| Metadata-only remote URL references | Yes, local CLI only; no fetch |
| File parts | Stored local file IDs only, **gated off by default** |
| Multimodal parts | No |
| OAuth | No |
| JSON-RPC | No |
| gRPC | No |
| Signed Agent Cards | No |
| Public tunnel setup | No |
| Full A2A compliance claim | No |

## Known Limitations

- Text parts and bounded structured JSON data parts are supported. File parts are gated and closed by default.
- Streaming uses Server-Sent Events over local HTTP+JSON. Every event gets a local SQLite event ID before emission.
- Durable replay survives server restarts with the same database. Pruning can invalidate old resume cursors (returns `409 replay_gap`).
- Live fan-out buffers are bounded and process-local. Cross-process updates use SQLite polling, not a distributed message bus.
- Queued tasks can be canceled before execution. Cancellation terminates the subprocess owned by the current server process; a different server process writes a cooperative request but does not kill PIDs across ownership boundaries.
- Registry tokens are stored in local SQLite, not an encrypted secret store.
- Stored file artifact references are local-first evidence, not broad external file interoperability. Official SDK 1.1.0/1.0.3 stored-ID send is unsupported because those models reject nested `file` objects.
- Public no-auth stored-ID peer capture is still absent.

## Docs Map

| What you need | Read |
|---|---|
| Install, enable, quickstart, examples | This README |
| Agent-friendly orientation, boundaries, common tasks | [AGENTS.md](AGENTS.md) |
| A2A compatibility ledger, SDK evidence, deviations | [docs/INTEROP.md](docs/INTEROP.md) |
| External peer search history, raw capture harness | [docs/EXTERNAL_INTEROP.md](docs/EXTERNAL_INTEROP.md) |
| File boundary audit (routes, config, Agent Card, what's closed) | [docs/FILE_BOUNDARY_STATUS.md](docs/FILE_BOUNDARY_STATUS.md) |
| File-part design history (phases 1-10, storage design) | [docs/FILE_PARTS_DESIGN.md](docs/FILE_PARTS_DESIGN.md) |
| Inbound file-part design (gates, shapes, rejection audit) | [docs/INBOUND_FILE_PARTS_DESIGN.md](docs/INBOUND_FILE_PARTS_DESIGN.md) |
| Release validation checklist | [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) |
| Hermes tool usage guidance | [hermes_a2a_bridge/skills/a2a-bridge/SKILL.md](hermes_a2a_bridge/skills/a2a-bridge/SKILL.md) |
| Contributing rules and boundaries | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Operations and Maintenance

Event history is retained independently from task and registry rows. By default, startup keeps the newest 500 events per task and removes events older than 30 days. Pruning never deletes tasks or registry entries.

Before execution, a server instance acquires a SQLite ownership lease for the task. While the subprocess runs, the server refreshes that lease every `ownership.heartbeat_interval_seconds`. A second instance cannot acquire an unexpired lease.

Lease diagnostics report lease age, heartbeat age, seconds until expiry, owner instance/PID, task state, and warning flags. The warning threshold is controlled by `observability.lease_warning_seconds`.

On startup, expired leases are recovered and their non-terminal tasks are marked failed. Disable with `ownership.recover_expired_leases_on_startup: false`.

```bash
hermes a2a maintenance stats
hermes a2a maintenance prune-events --json
hermes a2a maintenance recover-stale --json
hermes a2a maintenance leases --json
hermes a2a maintenance cancellations --json
hermes a2a maintenance recover-leases --json
```

`stats` reports task, event, registry, lease, and file-attachment metadata counts; active, expired, and stale-heartbeat lease totals; pending and expired cancellation totals; event-ID bounds; database path and size; journal mode; busy timeout; SQLite warning count; and retry counters.

SQLite connections request `busy_timeout`, WAL journaling, and NORMAL synchronous mode by default. Event writes, lease writes, cancellation writes, and task-state updates retry transient busy/locked failures using bounded `faults.sqlite_retry_*` settings. Permanent SQL errors are not swallowed.

Event IDs belong to one SQLite database. No external broker is required: active subscriptions poll that database, deduplicate against local live notifications, and cap replay queries. WAL, retries, leases, and cooperative requests improve local multi-process behavior but are not production clustering.

## Troubleshooting

- `hermes plugins enable a2a-bridge` says the plugin is missing:
  Run `hermes-a2a-bridge doctor-install` to verify the package entry point, then add `a2a-bridge` to `plugins.enabled` in `~/.hermes/config.yaml` on Hermes Agent v0.17.0. This is a host plugin-manager discovery limitation for pip entry points, not a package entry-point registration failure.
- `hermes a2a --help` says `a2a` is an invalid command:
  Confirm `plugins.enabled` includes `a2a-bridge` and start a new Hermes process. `hermes-a2a-bridge doctor-install --json` can report the best-effort activation status for agents. The CLI command is mounted only after the host runtime loader enables the entry-point plugin.
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
