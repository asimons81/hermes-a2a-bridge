# Inbound File Parts Design

This document defines the implemented v0.4 inbound A2A file-part subset. Stored file ID references are implemented behind explicit closed-by-default gates. Runtime remote URL file parts, inline bytes, arbitrary local paths, upload flows, and broad file support remain disabled, and the Agent Card must not claim broad file support. Version 0.4.1 adds CLI `send --file-id` and `stream --file-id` convenience flags that construct stored file ID references only. Version 0.4.2 adds Hermes tool `file_ids` convenience for the same stored-ID-only shape. Version 0.4.3 adds local open-gate end-to-end verification fixtures for client, CLI, and tool stored-ID paths without expanding accepted file shapes. Version 0.4.4 implements gated limited Agent Card metadata for stored-ID references only. Version 0.4.5 confirms the probed official SDK models cannot emit the nested stored `fileId` shape. Version 0.4.6 adds release-candidate audit coverage without expanding accepted file shapes.

Implementation status: Stored file ID references are implemented for `/message:send` and `/message:stream` only when both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true` are set. All other file-like inbound shapes remain rejected.

## Scope

The first safe inbound file-part shape is a reference to a file already staged into Hermes-controlled local storage:

```json
{
  "file": {
    "fileId": "file_abc123"
  }
}
```

The reference is metadata-only at the protocol boundary. It points at an existing `file_attachments` row and never carries bytes, storage paths, local paths, executor arguments, or credentials in request, task, event, or artifact JSON.

## Non-Goals

- Do not enable inbound file parts by default.
- Do not accept inline bytes, base64, `raw`, blobs, or arbitrary binary JSON payloads.
- Do not accept arbitrary local paths or request-controlled filesystem locations.
- Do not fetch, HEAD, download, cache, or background-fetch remote URLs.
- Do not add upload routes, `/v1` routes, JSON-RPC runtime support, OAuth, signed Cards, public tunneling, or gRPC.
- Do not add CLI `send --file` or `stream --file`.
- Do not automatically create output artifacts from inbound references.
- Do not claim full file-part or full A2A conformance.

## Implemented First Shape

Support only stored file ID references:

```json
{
  "message": {
    "role": "ROLE_USER",
    "parts": [
      {
        "file": {
          "fileId": "file_abc123"
        }
      }
    ]
  }
}
```

Tradeoffs:

- Stored file IDs are the narrowest useful shape because Hermes already owns the metadata row, controlled storage root, size, SHA-256, MIME type, and byte-route auth boundary.
- Metadata-only remote URL references are useful later, but accepting them as inbound message parts risks peers treating them as fetchable or locally available. They should require a separate gate and must remain inert.
- Inline bytes/base64 should remain rejected. They create JSON body size, memory, decoding, MIME, and audit problems and bypass the existing controlled staging boundary.
- Arbitrary local paths must remain rejected because a remote peer must never cause Hermes to read local files.

## Config Gate

Recommended defaults:

```yaml
parts:
  allow_file_parts: false
  allow_file_id_references: false
  allow_remote_url_file_references: false
  allow_inline_file_bytes: false
files:
  auto_fetch_remote_urls: false
  allow_inline_bytes: false
```

Rules:

- Defaults keep all inbound file parts rejected.
- `parts.allow_file_parts: true` alone is not enough.
- Stored file ID references require both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`.
- Metadata-only remote URL references require a separate future gate, `parts.allow_remote_url_file_references: true`, and must still not fetch bytes.
- Inline bytes remain unsupported even if `parts.allow_inline_file_bytes` or `files.allow_inline_bytes` exists as a placeholder.
- Config backfill must preserve user-provided values and add only missing defaults.

Suggested disabled-gate response codes:

- `file_reference_disabled`
- `unsupported_file_reference`
- `unsupported_inline_file_bytes`
- `unsupported_remote_file_url`
- `invalid_file_reference`

## Auth And Ownership Semantics

Inbound stored file ID references are accepted only after the existing bearer auth check passes. If `server.require_auth` is true, unauthenticated callers cannot probe file IDs through message routes.

Validation requirements:

- The `fileId` must match the existing opaque file ID format.
- The attachment row must already exist in `file_attachments`.
- Public metadata must be safe to expose through the same shaping used by file metadata routes.
- First implementation accepts only `source: local` or the local staged-file source values already used for controlled bytes.
- Metadata-only remote URL records are rejected.
- Bytes must be available locally.
- The resolved storage path must remain under the configured storage root and must not be a symlink, junction, or reparse-point escape.
- Size and SHA-256 should be verified, or the request should perform a lightweight equivalent check before prompt rendering.
- Request JSON, task JSON, event JSON, artifact JSON, and errors must not include `storage_path`, raw bytes, local absolute paths, bearer tokens, or request-controlled executor arguments.

Storage behavior:

- Persist safe inbound file reference metadata in task metadata.
- Render safe metadata into the executor prompt.
- Do not automatically create task artifacts from inbound references. Artifacts remain outputs or explicit local attachments.

## Request Validation

When `parts.allow_file_parts` is false, `/message:send` and `/message:stream` preserve the closed-boundary `unsupported_part_type` behavior. When `parts.allow_file_parts` is true but `parts.allow_file_id_references` is false, stored-ID references fail with `file_reference_disabled`.

When both stored-ID gates are open, the accepted flow is implemented as:

1. Validate bearer auth.
2. Validate `parts.allow_file_parts` and `parts.allow_file_id_references`.
3. Validate the file ID format.
4. Look up attachment metadata.
5. Reject unknown file IDs with `file_not_found`.
6. Reject metadata-only remote URL records with `unsupported_remote_file_url`.
7. Reject records without local bytes with `file_bytes_unavailable`.
8. Reject unsafe storage paths with a safe error that does not include the path.
9. Reject checksum or size mismatch with `file_integrity_failed` if verification fails.
10. Render safe file metadata into the executor prompt.
11. Persist safe file reference metadata under task `metadata.inputFileReferences`.
12. Persist events without bytes, storage paths, source local paths, or tokens.

Unsupported shapes:

- `{ "file": { "uri": "https://example.com/report.pdf" } }` is rejected in the first implementation.
- `{ "file": { "bytes": "..." } }`, `raw`, `blob`, or base64 fields are rejected.
- `{ "file": { "path": "C:\\..." } }`, `file://`, UNC paths, Windows drive paths, and bare local paths are rejected.
- Unknown file fields are rejected unless a future implementation stores a tiny sanitized subset in metadata and proves it contains no bytes, tokens, or paths.

## Executor Boundary

The executor prompt may include metadata only:

```text
File attachment 1:
- fileId: file_abc123
- name: report.pdf
- mimeType: application/pdf
- sizeBytes: 12345
- sha256: ...
- bytesAvailable: true
```

Rules:

- Do not pass local storage paths to the executor by default.
- Keep `executor.expose_local_file_paths_to_executor: false` by default if such a setting is added later.
- If path exposure is ever added, expose only validated controlled-storage paths, never arbitrary request paths.
- Do not read file content into the prompt in the first implementation.
- Do not execute files.
- Do not let file names, IDs, URLs, metadata, or request fields influence executor argv.

## Task, Event, And Artifact Metadata

Task metadata should include a small safe section such as:

```json
{
  "inboundFileReferences": [
    {
      "fileId": "file_abc123",
      "name": "report.pdf",
      "mimeType": "application/pdf",
      "sizeBytes": 12345,
      "sha256": "...",
      "bytesAvailable": true,
      "source": "local"
    }
  ]
}
```

Events may include the task snapshot with this metadata, but must not include raw bytes, storage paths, source paths, credentials, or request-controlled command data. Inbound references should not automatically become output artifacts; explicit artifact attachment remains a separate local operation.

## Agent Card Behavior

Default Agent Card behavior remains no file support advertised.

When both stored-ID gates are explicitly enabled, the Agent Card may describe a limited input subset, but it must not imply upload support, inline byte support, remote fetch, remote URL acceptance, arbitrary local path access, or broad file-part support.

Implementation status: v0.4.4 adds top-level `metadata.hermesA2ABridge.fileReferences` only when both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true` are set. Half-open gates stay quiet. The metadata uses `scope: pre_staged_local_file_id_references_only`, shows only the accepted `{ "file": { "fileId": "file_..." } }` shape, marks auth and both config gates as required, and lists inline bytes, URI file references, remote URL fetch, arbitrary local paths, and uploads as unsupported.

Acceptable wording:

- `supports pre-staged local file ID references only`
- `does not fetch remote URLs`
- `does not accept inline bytes`

The card should keep text and JSON modes as the default modes unless the implementation adds a narrowly named extension that SDK clients will not misread as full file support.

## SDK Interop Plan

Expected SDK/client behavior:

- SDK client sends `{ "file": { "fileId": "file_..." } }`.
  - Gates closed: reject.
  - Stored-ID gates open: accept only if the file row is local, bytes-backed, and integrity-valid.
- SDK client sends `{ "file": { "uri": "https://..." } }`.
  - Gates closed: reject.
  - Stored-ID gates open: still reject.
  - Later remote-URL gate: may accept as inert metadata only after a separate design/test pass.
- SDK client sends inline bytes, `raw`, blob, or base64.
  - Always reject in the first implementation.
- SDK client sends an unsupported file shape.
  - Reject with a structured code and no token/path leakage.

Implemented fixture coverage:

- Stored-ID request rejected with gates closed.
- Stored-ID request accepted with gates open and a pre-staged local row.
- URI, inline bytes, unknown IDs, remote URL records, missing bytes, and integrity failures rejected even with stored-ID gates open.
- Fixtures live under `tests/fixtures/blackbox/inbound_file_id_references/`.
- Local open-gate end-to-end fixtures live under `tests/fixtures/blackbox/stored_file_id_e2e/` and cover staged local bytes-backed IDs through client, CLI, tool, stream, task lookup, replay, closed-gate rejection, remote URL row rejection, and inline byte rejection.

Optional SDK tests should remain optional and isolated from runtime dependencies.

## CLI And Tools

CLI stored-ID convenience is implemented in v0.4.1. Hermes tool stored-ID convenience is implemented in v0.4.2.

Implemented CLI options:

```text
hermes a2a send AGENT_OR_URL MESSAGE --file-id file_...
hermes a2a stream AGENT_OR_URL MESSAGE --file-id file_...
```

Implemented tool argument:

```json
{
  "agent_url": "http://127.0.0.1:8765",
  "message": "analyze this",
  "file_ids": ["file_abcdefghijklmnopqrstuv"]
}
```

Rules:

- `--file-id` appends one stored file ID reference part per flag.
- Tool `file_ids` appends one stored file ID reference part per ID.
- The generated request part is exactly `{ "file": { "fileId": "file_..." } }`.
- The CLI and tool validate opaque ID shape locally but do not look up local storage.
- The server remains authoritative for gates, existence, source type, bytes availability, and integrity.
- `--file PATH` remains unsupported.
- Tools do not accept arbitrary paths, URLs, URI fields, inline bytes, or file contents.
- Tools must never return raw file bytes.
- CLI and tools must preserve token redaction and must not print storage paths.
- Remote URL and inline byte inbound support remain deferred.

## Security Checklist

- Config gate closed by default.
- Bearer auth required before file ID lookup.
- File ID format validation.
- Attachment lookup.
- Source type validation.
- Bytes availability validation.
- Integrity check.
- No storage path exposure.
- No raw bytes in task/event JSON.
- No auto-fetch.
- No inline bytes.
- No local path reads.
- No executor argv influence.
- No token leakage.
- No Agent Card overclaiming.
- SDK rejection fixtures.
- Local peer fixture updates.
- Migration/backward compatibility.
- Route list audit.
- CLI command audit.

## Implementation Test Plan

1. Defaults include `parts.allow_file_parts: false`, `parts.allow_file_id_references: false`, `parts.allow_remote_url_file_references: false`, `parts.allow_inline_file_bytes: false`, `files.auto_fetch_remote_urls: false`, and `files.allow_inline_bytes: false`.
2. Config backfill preserves existing user values for all new gates.
3. Gates closed: `/message:send` rejects SDK-style file parts with the current documented error.
4. Gates closed: `/message:stream` rejects SDK-style file parts with the current documented error.
5. `parts.allow_file_parts: true` alone still rejects stored file ID references.
6. Stored-ID gates open: valid local bytes-backed `fileId` is accepted.
7. Stored-ID gates open: unknown `fileId` is rejected.
8. Stored-ID gates open: invalid `fileId` is rejected before lookup.
9. Stored-ID gates open: metadata-only remote URL record is rejected.
10. Stored-ID gates open: missing local bytes are rejected.
11. Stored-ID gates open: unsafe path is rejected without exposing the path.
12. Stored-ID gates open: checksum and size mismatch are rejected.
13. Accepted file metadata is rendered into the executor prompt without paths or bytes.
14. Accepted file metadata is persisted in task metadata without paths or bytes.
15. No output artifact is created automatically from inbound references.
16. Agent Card advertises no file support while gates are closed.
17. Agent Card limited wording appears only when relevant gates are open.
18. CLI `send` and `stream` have `--file-id` only; `--file` remains unsupported and option abbreviation must not make it work.
19. SDK and local peer rejection fixtures remain stable for URI, inline, raw, URL, and multimodal shapes.
20. Route list still has no upload, `/v1`, JSON-RPC, OAuth, tunnel, or gRPC runtime surfaces.
