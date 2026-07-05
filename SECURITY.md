# Security Policy

## Supported Versions

Security patches target the latest release only. Older versions are not covered.

## Reporting a Vulnerability

If you find a security issue in Hermes A2A Bridge, **do not open a public issue**. Send details directly to:

- **GitHub Security Advisory:** Use the repo's "Report a vulnerability" tab (preferred)
- **Email:** tony@tonyreviewsthings.com (include `[A2A-BRIDGE-SEC]` in the subject)

We aim to acknowledge within 48 hours and triage within one week.

## What Not to Post Publicly

- Bearer tokens, auth secrets, or registry tokens
- Local file paths from your machine
- Full contents of private `~/.hermes/a2a/config.yaml` output
- Executor command lines containing secrets
- SQLite database dumps

When sharing diagnostics output, redact the `server.auth_token` field and any path segments that reveal your home directory structure.

## Scope

This bridge is a local-first tool that binds to localhost by default. Bearer auth, file gates closed by default, and argv-without-shell execution are intentional safeguards. If you find a way to bypass these defaults without explicit user configuration, that is in scope.
