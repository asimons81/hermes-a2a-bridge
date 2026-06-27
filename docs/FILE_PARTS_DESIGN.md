# v0.4 File-Part Design

This document defines the file-part support boundary for Hermes A2A Bridge v0.4. v0.4.0 accepts only pre-staged local stored file ID references when both stored-ID gates are enabled. Version 0.4.1 adds CLI convenience flags that build stored file ID reference parts only. Version 0.4.2 adds Hermes tool `file_ids` convenience for that same stored-ID-only shape. Version 0.4.3 adds local end-to-end verification fixtures for that open-gate stored-ID path. Version 0.4.4 adds gated limited Agent Card metadata for stored-ID references only. Version 0.4.5 refreshes official SDK and public-peer evidence without changing runtime acceptance. Version 0.4.6 adds release-candidate hardening tests and docs updates without changing runtime acceptance. It still rejects blob/binary parts, URI file references, remote URL inbound records, inline bytes, image/audio/video parts, arbitrary local paths, and unknown multimodal part shapes.

Phase 1 is implemented in v0.3.2 for configuration defaults, the SQLite `file_attachments` metadata table, safe local storage helpers, public metadata shaping, and local maintenance stats. Phase 2 is implemented in v0.3.3 for explicit local CLI file ingest plus metadata list/show/delete/stats commands. Phase 3 is implemented in v0.3.4 for authenticated metadata and byte routes for files already staged through the local CLI. Phase 4 is implemented in v0.3.5 for stored file references in Hermes-owned task artifacts and `artifactUpdate` SSE replay. Phase 5 is implemented in v0.3.6 for local CLI metadata-only remote URL references. Phase 6 is implemented in v0.3.7 for SDK file-part rejection fixtures, metadata-only file-reference fixtures, fixture safety validation, and old nullable-column migration coverage. Phase 7 is implemented in v0.3.8 for lifecycle maintenance, integrity verification, orphan-byte cleanup, missing-byte metadata repair, and safe reporting of broken artifact references. Phase 8 is implemented in v0.3.9 for optional SDK verification refresh, fixture boundary audit, route/CLI/config/Agent Card audit tests, and `docs/FILE_BOUNDARY_STATUS.md`. Phase 9 is implemented in v0.3.10 for external HTTP+JSON 1.0 discovery notes, skipped real-peer documentation, and a test-only raw capture harness. Phase 10 is implemented in v0.3.11 for deterministic local HTTP+JSON compatibility-peer capture fixtures, including unsupported file-part rejection. Version 0.3.12 added the inbound stored-file-ID design. Version 0.4.0 implements that design for stored local file IDs only. Version 0.4.1 implements CLI `send --file-id` and `stream --file-id` request construction only. Version 0.4.2 implements tool `file_ids` request construction only. Version 0.4.4 implements gated limited Agent Card metadata only when both stored-ID gates are enabled. Version 0.4.5 refreshes official SDK capability evidence and public/official peer search notes. Version 0.4.6 adds release-candidate package/docs/CLI/tool/fixture/config/smoke audit coverage. Runtime inbound file parts remain closed by default: no CLI `send --file`, no CLI `stream --file`, no tool path/URL/bytes inputs, no remote URL fetching, no inline bytes, no path reads, and `parts.allow_file_parts` remains false by default.

## Scope

v0.4 should add controlled file references without turning the bridge into an arbitrary filesystem reader or unauthenticated file server. The supported model is attachment metadata plus controlled storage under the Hermes A2A home directory. File bytes live outside SQLite, and durable task/event replay stores metadata references only.

## Non-Goals

- No arbitrary remote-request-driven local path reads.
- No automatic remote URL fetching by default.
- No unauthenticated downloads.
- No public tunnel support.
- No OAuth, signed Cards, JSON-RPC runtime, or gRPC.
- No large binary payloads in SQLite.
- No broad full-conformance claim.

## Current Rejection Audit

File-like inputs are rejected before Pydantic message parsing in `server._validate_raw_part`. The guard rejects:

- `kind` or `type` values of `file`, `image`, `audio`, or `video`.
- SDK-style file fields: `raw`, `url`, `filename`.
- Blob-like fields: `blob`.
- Unknown part kinds and unknown part shapes.

The server returns HTTP 400 with `code: unsupported_part_type` for those part classes. If the caller sends `A2A-Version: 1.0`, the response uses the A2A 1.0 `google.rpc.Status`-style error envelope with `ErrorInfo.metadata.bridgeCode: unsupported_part_type`; older callers receive the legacy bridge JSON error:

```json
{
  "success": false,
  "error": "Unsupported message: File parts are not supported yet. Hermes A2A Bridge currently supports text and structured JSON data parts.",
  "code": "unsupported_part_type"
}
```

The error path runs before executor prompt rendering, so unsupported file inputs cannot alter executor arguments. The server redacts unexpected exception text with the local auth token. Known data-part oversize tests also assert bearer-token redaction. Client error parsing redacts the caller's explicit bearer token from remote payloads before raising `ClientError`. Tool wrappers redact explicit tokens and saved registry tokens from tool error strings. CLI and tool send paths construct text, structured JSON data parts, and stored-ID-only file references; they do not provide local file path, URL, URI, or byte entrypoints.

SDK fixture behavior must be preserved: current black-box fixtures document clear unsupported file-part rejection for SDK clients, while SDK-compatible data parts continue to omit `kind` and `type` in outbound Hermes messages.

## Support Matrix

| File-part form | v0.4 decision | Reason |
|---|---|---|
| Inline bytes/base64 via `raw` or binary/blob fields | Reject by default | Avoid memory abuse, huge JSON bodies, and ambiguous binary decoding. A later limited mode could require `allow_inline_bytes: true` plus a very small `max_inline_bytes`. |
| Arbitrary local file path in remote request | Reject | A remote peer must never cause Hermes to read arbitrary local files. |
| Explicit local CLI ingest PATH | Supported for staging only | The CLI caller is the local user. The file is copied into controlled storage and recorded as safe metadata; it is not sent to peers yet. |
| Remote URL reference | Supported as local CLI metadata only | Accepting a URL does not imply fetching. No GET, HEAD, byte download, or background fetch job is created. `auto_fetch_remote_urls` stays false by default. |
| Stored local attachment reference | Support behind explicit gates | Preferred v0.4 model. References use opaque file IDs under controlled storage and require both `parts.allow_file_parts` and `parts.allow_file_id_references`. |
| Artifact file reference | Support | Task artifacts and SSE `artifactUpdate` events may reference stored files by ID and metadata. Replay stores metadata only. |
| Download/read route | Support with auth | File bytes are served only from controlled storage, only by opaque ID, and only when bearer auth passes. |

## Configuration

Recommended defaults:

```yaml
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
executor:
  include_file_metadata_in_prompt: true
  expose_local_file_paths_to_executor: false
```

`parts.allow_file_parts` remains false by default. Stored inbound file ID references require both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`; when either gate is closed, the runtime remains closed.

## Storage Design

Use a configurable storage root, defaulting to `~/.hermes/a2a/files`. Resolve and create the directory during startup or first ingest. Every stored file receives an opaque ID such as `file_` plus at least 128 bits of random URL-safe entropy. Do not derive IDs from filenames, task IDs, URLs, or hashes.

Suggested layout:

```text
~/.hermes/a2a/files/
  ab/
    file_abcd.../
      content
      metadata.json
```

The sharded prefix keeps directories small. The physical path is derived only from the generated file ID, never from user-controlled names. Filenames are retained as metadata for display and `Content-Disposition`, with a separate `safe_filename` produced by sanitization.

Add a `file_attachments` table:

```sql
CREATE TABLE file_attachments (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  artifact_id TEXT,
  filename TEXT,
  safe_filename TEXT,
  mime_type TEXT,
  declared_mime_type TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  storage_path TEXT,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

Do not store raw bytes in SQLite. `storage_path` is internal and must never appear in public task, artifact, tool, CLI JSON, or error payloads.

MIME handling:

- Prefer a small local sniffing helper based on magic bytes for the allowlist, not a large dependency.
- Compare sniffed MIME with declared MIME. If they disagree, use the sniffed value for serving and record the declared value.
- Reject unknown MIME when `reject_unknown_mime: true`.
- Allow extension-based hints only after path and filename sanitization; never trust extensions for enforcement.

Write behavior:

- Stream/copy into a temporary file inside the target storage root.
- Count bytes while writing and abort once `max_file_bytes` is exceeded.
- Hash with SHA-256 during write.
- Check total storage quota before final commit and again immediately before atomic rename.
- Use atomic rename from temporary path to final `content`.
- Remove temporary files on failure.
- Reject symlinks, junction escapes, hardlink surprises, and final resolved paths outside the storage root.

Retention:

- Task/event retention should not delete files automatically in v0.4 unless `cleanup_deleted_task_files` is enabled.
- Maintenance commands should report orphaned attachments and total storage bytes.
- A later pruning command can delete attachments not referenced by live tasks/artifacts.

## Server/API Route Design

Implemented in v0.3.4:

- `GET /files/{file_id}` returns bytes for a stored attachment.
- `GET /files/{file_id}/metadata` returns public metadata only.

Still deferred:

- `DELETE /files/{file_id}` is deferred or maintenance-only; do not expose broad remote deletion in the first pass.

Rules:

- Require bearer auth on every file route.
- Never serve files from raw paths or URL path fragments.
- Validate `file_id` against a strict opaque-ID pattern before lookup.
- Never expose `storage_path`.
- Return structured JSON errors; no tracebacks.
- Use validated `mime_type` for `Content-Type`.
- Use safe `Content-Disposition: attachment` with sanitized filename and an RFC 5987 encoded `filename*` when needed.
- Include `Content-Length` from metadata.
- Keep localhost-first defaults; no public signed URLs in v0.4.

Artifact URLs:

- If `server.public_url` is local and auth requirements are clear, artifacts may include `uri: "{public_url}/files/{file_id}"`.
- If the bridge is not prepared to communicate auth expectations to a peer, emit `fileId` plus metadata and omit `uri`.
- Never emit filesystem paths.

## File Model Shapes

Deferred inbound remote URL reference shape, not accepted in v0.4.0:

```json
{
  "file": {
    "name": "report.pdf",
    "mimeType": "application/pdf",
    "uri": "https://peer.example/files/report.pdf",
    "sizeBytes": 12345
  },
  "metadata": {}
}
```

Hermes should accept this only in a later runtime file-part phase after a separate feature flag, rejection-fixture pass, and interop review. v0.4.0 supports remote URL references only through the local `files add-url` metadata command. For SDK compatibility, outbound Hermes parts should omit `kind` and `type` unless a specific local-only compatibility path needs them.

Stored outbound artifact reference:

```json
{
  "artifactId": "artifact-123",
  "name": "result-file",
  "parts": [
    {
      "file": {
        "fileId": "file_abcd1234",
        "name": "result.json",
        "mimeType": "application/json",
        "sizeBytes": 1234,
        "sha256": "hex-encoded-sha256",
        "uri": "http://127.0.0.1:8765/files/file_abcd1234"
      },
      "metadata": {}
    }
  ]
}
```

Unknown file fields should be preserved under `metadata.originalFileFields` only if they are small, JSON-safe, and do not include raw bytes, tokens, or paths. Otherwise reject with `unsupported_part_type`.

## Executor Boundary

Remote file references are not fetched automatically. Stored attachment references are summarized to the executor as metadata only when `executor.include_file_metadata_in_prompt: true`, for example:

```text
File attachment 1:
- id: file_abcd1234
- name: report.pdf
- mimeType: application/pdf
- sizeBytes: 12345
- sha256: ...
- source: remote_url_reference
```

The executor must not receive arbitrary filesystem paths. If a later configuration exposes paths, it must be local-only, opt-in, and restricted to controlled storage files:

- `executor.expose_local_file_paths_to_executor: false` by default.
- Only paths under the resolved storage root may be exposed.
- Remote request fields can never alter executor argv.
- No file execution or shell interpretation.

## CLI and Tool Design

Implemented local staging and metadata commands:

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
```

`ingest PATH` is an explicit local-user action. The CLI ingests the file into controlled storage, records metadata, and returns safe public metadata. It must not send the original local path to a remote peer. `add-url URL` is also an explicit local-user action. It stores an HTTP(S) URL as metadata only, validates optional declared MIME, size, and SHA-256, strips credentials/query/fragment from public output, and never fetches, HEADs, downloads, or schedules work for the URL. It rejects non-HTTP(S) schemes, Windows drive paths, UNC paths, and bare local paths. Localhost and private-network HTTP(S) URLs are allowed only as inert metadata because Hermes never dereferences them in this phase. `attach-artifact` references an existing staged file or metadata-only URL reference from an existing local task artifact, persists a safe `artifactUpdate` event, and stores metadata only in task/event JSON. Runtime `send --file-id` and `stream --file-id` construct stored file ID reference parts only, validate ID shape locally, and leave gate/existence/integrity checks to the server. Runtime `send --file` and `stream --file` remain unsupported.

For metadata-only URL references, `GET /files/{file_id}/metadata` returns safe metadata. `GET /files/{file_id}` returns `file_bytes_unavailable` because Hermes has no local bytes for the record. Artifact file parts may include safe `sourceUrl` but must not include a local file route `uri`, storage path, or bytes for those records.

`fetch-metadata` and `download` call the authenticated HTTP routes and require an explicit `--agent` base URL or registry entry. `download` writes only to the explicit output path provided by the local caller; it does not infer a filename from remote metadata.

`verify FILE_ID` is a local maintenance command for already recorded attachment metadata. Local records validate storage-root containment, byte presence, metadata size, and SHA-256. Metadata-only remote URL records return `metadata_only`, `bytesAvailable: false`, and do not perform network requests or checksum verification.

`scan` reports storage health without modification. It can report local metadata rows with missing bytes, unsafe paths, checksum mismatches, or size mismatches; byte files under the controlled storage root with no metadata row; remote URL rows that unexpectedly contain local storage paths; and task/event artifact references whose file IDs no longer have metadata. It does not recursively scan outside the configured storage root, does not follow symlinks or reparse points, and does not delete anything.

`cleanup-orphans` deletes orphaned stored bytes only under the controlled storage root and only when `--confirm` is provided. The default is dry-run. `repair` removes metadata rows only for local-byte records whose bytes are missing and only when `--confirm` is provided. It does not remove metadata-only remote URL rows, does not rewrite task artifacts or events, and does not delete tasks or registry rows. If `--dry-run` and `--confirm` are both supplied, the CLI returns a structured usage error.

Hermes tools should remain JSON-only:

- Do not accept arbitrary local file paths by default.
- Consider `a2a_list_files` and `a2a_get_file_metadata` after storage is stable.
- Do not return raw file bytes through tool responses.
- Preserve token redaction in all tool errors.

## Security Checklist

- Reject path traversal in IDs and filenames.
- Sanitize filenames for display and `Content-Disposition`.
- Resolve paths and ensure every final path stays under the storage root.
- Reject symlink, junction, and reparse-point storage escapes.
- Enforce per-file size limits while streaming bytes.
- Enforce total storage quota.
- Enforce MIME allowlist and unknown-MIME policy.
- Require auth for all file byte and metadata routes.
- Never expose bearer tokens in errors, logs, fixtures, CLI output, or tool output.
- Never expose raw storage paths.
- Use safe `Content-Disposition`.
- Never read arbitrary local paths from remote requests.
- Do not auto-fetch remote URLs by default.
- Do not execute files.
- Do not pass file names, URLs, or paths through a shell.
- Do not return raw tracebacks.
- Clean up partial writes.
- Store replay metadata only, not file bytes.
- Track orphaned files and retention behavior.
- Test Windows separators, drive letters, UNC prefixes, reserved names, ADS suffixes, and case-insensitive root checks.

## Implementation Test Plan

Required v0.4 tests:

1. Config backfills file settings safely.
2. File parts remain rejected when file support is disabled.
3. Path traversal filenames are rejected or sanitized.
4. Symlink or junction storage escape is rejected.
5. Oversized file is rejected.
6. Disallowed MIME is rejected.
7. Unknown MIME is rejected when configured.
8. Stored attachment metadata round-trips.
9. File bytes are stored outside SQLite.
10. File download requires auth.
11. File download never exposes storage path.
12. `Content-Disposition` is safe.
13. Artifact file references replay through SQLite events.
14. Subscribe replays file artifact metadata.
15. CLI `files ingest PATH` ingests a local file only when explicitly invoked.
16. Remote request cannot read arbitrary local path.
17. Remote URL reference is preserved but not fetched by default.
18. Tools do not return raw file bytes.
19. No token leakage in file errors.
20. Windows path separators cannot escape storage root.

## Phased Implementation

1. Implemented in v0.3.2: add config defaults, metadata table, storage helpers, public metadata shaping, maintenance stats, and safety tests while keeping `parts.allow_file_parts: false`.
2. Implemented in v0.3.3: add local CLI ingest into controlled storage plus metadata list/show/delete/stats commands without remote download routes.
3. Implemented in v0.3.4: add authenticated metadata and byte routes for locally staged files.
4. Implemented in v0.3.5: enable stored file references in artifacts and SSE replay.
5. Implemented in v0.3.6: accept remote URL references as local CLI metadata only when enabled; no fetch, no HEAD, no bytes, and artifact attachment is metadata only.
6. Implemented in v0.3.7: re-run/extend SDK black-box rejection fixtures, add Hermes-owned and metadata-only remote URL file-reference fixtures, validate fixture safety, and cover old 0.3.5-style nullable-column migration risk without claiming full conformance.
7. Implemented in v0.3.8: add file lifecycle maintenance, integrity checks, orphaned byte detection/cleanup, conservative missing-byte metadata repair, stats integration, and broken file-artifact reference reporting while preserving runtime file-part rejection.
8. Implemented in v0.3.9: refresh optional SDK black-box verification for SDK 1.1.0 and 1.0.3, confirm fixtures remain stable, add explicit route/CLI/config/Agent Card boundary tests, and document readiness in `docs/FILE_BOUNDARY_STATUS.md`; runtime inbound file parts remain disabled.
9. Implemented in v0.3.10: search for public or safely runnable HTTP+JSON 1.0 peers, document candidate blockers in `docs/EXTERNAL_INTEROP.md`, add a sanitized raw capture harness under `tests/`, and keep runtime inbound file parts disabled.
10. Implemented in v0.3.11: add a deterministic test-only local HTTP+JSON compatibility peer, capture sanitized fixtures under `tests/fixtures/blackbox/local_http_json_peer/`, and preserve unsupported inbound file-part behavior.
11. Implemented in v0.3.12: add `docs/INBOUND_FILE_PARTS_DESIGN.md` for the gated stored-file-ID inbound design, add closed-by-default config placeholders, and preserve unsupported inbound file-part behavior.
12. Implemented in v0.4.0: accept pre-staged local stored file ID references on `/message:send` and `/message:stream` only when both stored-ID gates are enabled; persist safe metadata under `metadata.inputFileReferences`; render safe metadata into executor prompts; keep remote URL, inline bytes, arbitrary paths, upload routes, and CLI file send flags deferred.
13. Implemented in v0.4.1: add CLI `send --file-id` and `stream --file-id` for stored file ID reference request construction only; keep `--file`, remote URL inbound references, inline bytes, path reads, auto-staging, and broad file support unsupported.
14. Implemented in v0.4.2: add Hermes `a2a_send_message` tool `file_ids` for stored file ID reference request construction only; keep tool path, URI, URL, bytes, auto-staging, and broad file support unsupported.
15. Implemented in v0.4.3: add a deterministic local open-gate runtime harness and sanitized fixtures for staged stored file IDs through client, CLI, and tool paths; keep defaults closed and keep remote URL inbound records, inline bytes, URI-only parts, path-like parts, auto-staging, and public peer conformance unsupported.
16. Implemented in v0.4.4: add gated Agent Card metadata for `metadata.hermesA2ABridge.fileReferences` only when both stored-ID gates are enabled; keep default and half-open cards quiet and avoid upload, inline bytes, URI, remote fetch, arbitrary path, and broad file-part claims.
17. Implemented in v0.4.5: refresh official SDK capability and public/official peer evidence; document that probed SDK 1.1.0 and 1.0.3 request models cannot emit the nested stored `fileId` shape; keep runtime file acceptance unchanged.
18. Implemented in v0.4.6: add release-candidate package metadata, fixture safety, and smoke audit tests; update docs to record current boundaries; keep runtime file acceptance unchanged.
