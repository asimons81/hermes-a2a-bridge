# Inbound Stored File ID Fixtures

These fixtures document the v0.4.3 gated inbound stored-file-ID boundary plus CLI and tool stored-ID convenience shapes. Local open-gate end-to-end stored-ID evidence lives separately under `tests/fixtures/blackbox/stored_file_id_e2e/`.

- Gates closed: SDK-style file parts remain rejected with `unsupported_part_type`.
- Gates open: only pre-staged local `fileId` references are accepted.
- CLI convenience: `send --file-id` and `stream --file-id` build only `{ "file": { "fileId": "file_..." } }` message parts.
- Tool convenience: `a2a_send_message` `file_ids` builds only `{ "file": { "fileId": "file_..." } }` message parts.
- Remote URL references, inline bytes, URI-only file parts, missing bytes, and integrity failures remain rejected.
- CLI `--file PATH`, uploads, local path reads, remote URL inbound acceptance, inline bytes, automatic staging, and broad file-part conformance remain unsupported.
- Fixtures contain safe public metadata only. They do not contain internal paths, local paths, file bytes, auth tokens, or credential-bearing URLs.
