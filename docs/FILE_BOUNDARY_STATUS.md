# File Boundary Status

Current version: 0.4.6.

This document records the v0.4 stored-file-ID inbound boundary. Hermes A2A Bridge has local file-reference groundwork and now accepts pre-staged local stored file ID references only when explicit gates are enabled. It still does not accept broad inbound A2A file parts or advertise broad file support.

Stored file ID inbound support exists only for pre-staged local records and only when both gates are explicitly enabled.

Phase 10 added deterministic local HTTP+JSON compatibility-peer fixtures, including a file-part rejection capture. That fixture is evidence that the boundary remains closed; it does not enable inbound file support.

Version 0.4.0 implements the gated inbound stored-file-ID design in `docs/INBOUND_FILE_PARTS_DESIGN.md`. Version 0.4.1 adds CLI `send --file-id` and `stream --file-id` request construction for stored file IDs only. Version 0.4.2 adds Hermes tool `file_ids` request construction for stored file IDs only. Version 0.4.3 adds local open-gate end-to-end capture and verification for those stored-ID paths. Version 0.4.4 adds gated limited Agent Card metadata for stored-ID references only. Version 0.4.5 refreshes official SDK and public-peer evidence without changing runtime acceptance. Version 0.4.6 completes a release-candidate hardening audit for package metadata, docs wording, CLI/tool surfaces, fixture safety, config defaults, and smoke paths without changing runtime acceptance. Runtime inbound file parts remain closed by default. `/message:send` and `/message:stream` accept only `{ "file": { "fileId": "file_..." } }` when both `parts.allow_file_parts` and `parts.allow_file_id_references` are true.

## Implemented

- Hermes can stage and reference files it owns through explicit local CLI commands.
- Hermes can store metadata-only remote URL references through explicit local CLI commands.
- Hermes can attach already recorded file references to local task artifacts and replay those artifact references through SSE.
- Hermes can serve locally staged bytes through authenticated localhost file routes.
- Hermes can return safe metadata for locally staged files and metadata-only remote URL references.
- Hermes can verify local stored bytes, scan storage health, clean orphaned stored bytes with confirmation, and conservatively repair missing-byte metadata with confirmation.
- Hermes can accept inbound pre-staged local stored file ID references on message send and stream only when both stored-ID gates are explicitly enabled.
- Hermes CLI can construct stored file ID reference parts with repeated `send --file-id` and `stream --file-id` flags.
- Hermes `a2a_send_message` tool can construct stored file ID reference parts with optional `file_ids: ["file_..."]`.
- Accepted inbound file references are rendered into executor prompts as safe metadata and persisted under task `metadata.inputFileReferences`.
- Local open-gate stored-ID end-to-end fixtures cover client send, client stream, task lookup, subscribe replay, CLI send/stream, tool send, and structured rejection paths.
- The Agent Card remains quiet by default and for half-open file gates; when both stored-ID gates are enabled it includes Hermes-specific `metadata.hermesA2ABridge.fileReferences` for pre-staged local stored ID references only.
- Official SDK capability probes for `a2a-sdk` 1.1.0 and 1.0.3 document that those SDK request models cannot emit nested Hermes stored `fileId` parts. SDK-native `url` and `raw` fields remain rejected by Hermes.
- Black-box fixtures cover SDK file-part rejection, stored Hermes-owned file references, metadata-only remote URL references, error envelopes, Agent Card snapshots, and SSE replay.
- Release-candidate audit coverage checks source package metadata, plugin metadata, bundled skill inclusion, runtime version metadata, all black-box fixture safety markers, and import/config/Agent Card/health/CLI help/tool schema smoke paths.

## Intentionally Disabled

- Hermes still does not accept inbound file parts by default.
- Hermes still does not accept inbound file parts unless both stored-ID gates are enabled.
- Hermes still does not accept inbound file shapes other than pre-staged local stored file ID references.
- Hermes still does not fetch remote URLs, issue HEAD requests, download remote bytes, or cache remote URL records.
- Hermes still does not advertise broad file support in the Agent Card.
- Hermes still does not advertise any file support in the default Agent Card or half-open gate Agent Cards.
- Hermes still has no CLI `send --file`.
- Hermes still has no CLI `stream --file`.
- Hermes still does not stage files, read paths, fetch URLs, or embed bytes from `send` or `stream`.
- Hermes still has no tool file path, file URL, URI, inline bytes, or raw byte input.
- Hermes still has no upload route.
- Hermes still has no `/v1` routes.
- Hermes still does not implement JSON-RPC runtime support, OAuth, signed Cards, public tunneling, or gRPC.
- Hermes still has no public no-credential peer capture for inbound stored fileId behavior.

## Enabled Routes

```text
GET  /health
GET  /.well-known/agent-card.json
GET  /files/{file_id}/metadata
GET  /files/{file_id}
POST /message:send
POST /message:stream
GET  /tasks
GET  /tasks/{task_id}
POST /tasks/{task_id}:cancel
POST /tasks/{task_id}:subscribe
```

`aiohttp` also exposes implicit `HEAD` handlers for the `GET` routes. There are no file `POST`, `PUT`, `PATCH`, or `DELETE` routes.

## File CLI Commands

```text
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
hermes a2a send AGENT_OR_URL MESSAGE [--file-id FILE_ID]... [--token TOKEN] [--json]
hermes a2a stream AGENT_OR_URL MESSAGE [--file-id FILE_ID]... [--token TOKEN] [--json]
```

## Tool Surface

```text
a2a_send_message(agent_url, message?, data?, file_ids?, token?, context_id?, timeout_seconds?)
```

`file_ids` is optional and accepts stored Hermes file IDs only. It builds only `{ "file": { "fileId": "file_..." } }` parts, preserves ID order, validates ID shape locally, and leaves gates, existence, source type, byte availability, and integrity to the target server. The tool schema does not expose path, URI, URL, bytes, or file content inputs.

## Unsupported Inbound Shapes

The runtime rejects these request shapes before executor prompt rendering:

```json
{"message":{"role":"ROLE_USER","parts":[{"file":{"name":"report.pdf"}}]}}
```

```json
{"message":{"role":"ROLE_USER","parts":[{"raw":"aGVsbG8=","filename":"report.txt","mediaType":"text/plain"}]}}
```

```json
{"message":{"role":"ROLE_USER","parts":[{"url":"https://example.test/report.txt","filename":"report.txt","mediaType":"text/plain"}]}}
```

```json
{"message":{"role":"ROLE_USER","parts":[{"kind":"image","raw":"iVBORw0KGgo=","mediaType":"image/png"}]}}
```

With the default gates closed, `kind` or `type` values of `file`, `image`, `audio`, or `video`; SDK-style `file`, `raw`, `url`, and `filename` fields; blob-like fields; unknown kinds; and unknown part shapes are rejected with `unsupported_part_type`.

When both stored-ID gates are enabled, the only accepted inbound file shape is:

```json
{"message":{"role":"ROLE_USER","parts":[{"file":{"fileId":"file_abc123..."}}]}}
```

The referenced row must be local, bytes-backed, under the controlled storage root, and pass size plus SHA-256 integrity checks. Remote URL records, inline bytes, URI-only file parts, arbitrary paths, unknown IDs, missing bytes, unsafe paths, and integrity failures are rejected with structured file-reference error codes.

## Agent Card Behavior

With default config, `/.well-known/agent-card.json` advertises text and JSON input/output modes only. It does not include `metadata.hermesA2ABridge.fileReferences`, file/image/audio/video modes, upload support, inline byte support, URI support, remote URL fetch support, storage roots, local paths, tokens, or file IDs.

With only one stored-ID gate enabled, the Agent Card still does not advertise file-reference support.

When both `parts.allow_file_parts` and `parts.allow_file_id_references` are enabled, the Agent Card may include:

```json
{
  "metadata": {
    "hermesA2ABridge": {
      "fileReferences": {
        "supported": true,
        "scope": "pre_staged_local_file_id_references_only",
        "acceptedShapes": [
          {
            "file": {
              "fileId": "file_..."
            }
          }
        ],
        "requiresAuth": true,
        "requiresConfig": [
          "parts.allow_file_parts",
          "parts.allow_file_id_references"
        ],
        "unsupported": [
          "inline_bytes",
          "uri_file_references",
          "remote_url_fetch",
          "arbitrary_local_paths",
          "uploads"
        ]
      }
    }
  }
}
```

This metadata is a Hermes-specific marker for a narrow stored-ID subset. It is not upload support, URI support, remote fetch support, inline byte support, arbitrary path support, multimodal support, or broad file-part conformance.

## Gate-Closing Defaults

```yaml
server:
  host: 127.0.0.1
  require_auth: true
  allow_remote_hosts: false
parts:
  allow_data_parts: true
  allow_file_parts: false
  allow_file_id_references: false
  allow_remote_url_file_references: false
  allow_inline_file_bytes: false
files:
  allow_remote_url_references: true
  auto_fetch_remote_urls: false
  allow_inline_bytes: false
  max_inline_bytes: 0
```

## v0.4.6 Release-Candidate Audit Evidence

- Package metadata remains source-consistent across `pyproject.toml`, `plugin.yaml`, runtime `__version__`, default Agent Card version, plugin entry point, CLI declaration, tool list, runtime dependencies, and bundled `skills/*/SKILL.md` package data.
- Docs continue to describe Hermes A2A Bridge as a local-first HTTP+JSON subset. The stored file ID behavior is Hermes-specific, gated, and not public peer compatibility or full file-part conformance.
- CLI help exposes `send --file-id` and `stream --file-id`; `send --file` and `stream --file` remain absent and rejected.
- Hermes tool schemas expose only `file_ids` for stored Hermes IDs. They do not expose path, URI, URL, inline-byte, or file-content inputs.
- A single black-box fixture scan rejects obvious bearer tokens, credential-bearing URLs, local absolute paths, storage paths, accidental database/temp paths, raw staged content markers, and unnegated overclaim phrases.
- Config defaults and backfill tests preserve closed file gates, `files.auto_fetch_remote_urls: false`, inline bytes disabled, localhost binding, auth required, quiet default Agent Cards, quiet half-open Agent Cards, and limited Hermes-specific open-gate metadata.

## Known Risks

- Stored file artifact references are local-first evidence, not broad external file interoperability evidence.
- CLI `--file-id` coverage now includes a local live-server open-gate harness, but it remains local evidence only.
- Authenticated file byte routes depend on the caller already having a valid local bearer token.
- Metadata-only remote URL records are intentionally inert, so downstream consumers must not treat them as locally available bytes.
- Lifecycle repair removes missing local-byte metadata only when confirmed; it does not rewrite historical task or event artifacts.
- The Agent Card remains a hybrid local compatibility shape, not a full conformance claim.
- Stored file ID inbound support is local-gated interoperability, not upload support, remote fetch support, inline byte support, or full file-part conformance.
- Official SDK 1.1.0 and 1.0.3 stored-ID send is unsupported with the probed request models because they reject nested `file` objects before HTTP emission.
- Public no-auth peer capture for stored-ID inbound behavior is still absent.
- v0.4.6 did not refresh optional SDK environments or discover a new public stored-ID peer; it records release-candidate hardening against the existing v0.4.5 interop evidence.

## Next Gated Design Questions

- Should remote URL references ever be accepted as inert inbound metadata under a separate gate?
- How should auth expectations for `uri` file routes be communicated to non-local peers?
- Should executor prompts ever include controlled storage paths, or remain metadata-only indefinitely?
- What external HTTP+JSON 1.0 peer should be used to validate file artifact interoperability?
- Will a future official SDK expose a nested file object or another stored-ID-compatible model shape?
- What migration and rollback behavior is required before `parts.allow_file_parts` can ever default to true?
