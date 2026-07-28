# Hermes A2A Bridge v0.5.0 — Release Readiness

**Decision:** Local GO; publication remains gated on pull-request CI and the manual Release Check workflow.

## Candidate

- Release branch: `feat/operator-ux-phase1`
- Release metadata candidate: `d04cb218d28f6fc5c4f65ef503db286456e11cd3`
- Runtime/release version: `0.5.0`
- Base: `d9077ccfabbd6b0a94357370261b7fed99b8a875` (`origin/main` at branch start)
- Previous released tag: `v0.4.8`
- No `v0.5.0` tag or GitHub release existed at audit time.

The release consists of `e72708a` (operator/reliability implementation and regression tests) followed by `d04cb21` (version and documentation surfaces). `IDEA.md` is intentionally untracked and excluded from the release.

## Local gates

| Gate | Result |
| --- | --- |
| `python -m pytest -q` | **350 passed, 4 skipped** |
| `python -m compileall -q hermes_a2a_bridge tests` | passed |
| `python -m build` | built `hermes_a2a_bridge-0.5.0-py3-none-any.whl` and `hermes_a2a_bridge-0.5.0.tar.gz` |
| `python -m twine check dist/*` | both artifacts passed |
| Clean wheel install | passed: import, `0.5.0` metadata, plugin entry point, bundled skill, key module imports |
| Clean sdist install | passed: import, `0.5.0` metadata, plugin entry point, bundled skill, key module imports |
| Package-data inspection | bundled `hermes_a2a_bridge/skills/a2a-bridge/SKILL.md` verified in wheel and sdist |
| `python -m hermes_a2a_bridge doctor-install --json` | passed: package, entry point, Hermes v0.19.0, and enabled plugin all healthy |
| `hermes a2a --help` | passed: setup, status, maintenance, send, stream, and task commands mounted |
| Fake peer, `127.0.0.1:8876` | metadata, `--live-probe`, and `--stream-probe` passed; terminal SSE replay observed |
| File-boundary defaults | passed: file parts, stored-ID references, remote URL file references, inline bytes, and auto-fetch all remain false |

The fake peer was run on `127.0.0.1:8876`; the occupied local peer on port 8766 was not changed.

## Static analysis

`ruff check .` reports 98 existing diagnostics. A baseline archive of `d9077cc` reports 97 instances across the same pre-existing diagnostic kinds; no new ruff diagnostic kind was introduced by this candidate, and the new cache module and its test pass `ruff check` cleanly. The repository has no ruff configuration or enforced lint workflow.

`mypy hermes_a2a_bridge tests` reports 92 existing errors in 14 files both before and after this candidate (untyped third-party imports, incomplete Pydantic typing, optional SDK imports, and existing nullable-task paths). The repository has no mypy configuration or CI type gate. No new mypy error count was introduced by the two candidate commits.

These are deferred pre-existing quality debts, not release claims of clean lint/type gates.

## Cross-platform CI gate

Required before publication:

- Ubuntu: Python 3.11, 3.12, 3.13
- Windows: Python 3.11
- Package build workflow
- Manual `Release Check` workflow against this branch/candidate

## Known limitations

- This is a local-first HTTP+JSON subset, not full A2A conformance.
- No `/v1`, JSON-RPC runtime, OAuth, signed Agent Cards, gRPC, or public tunneling.
- File boundaries remain closed by default. The only supported inbound file reference is an explicitly enabled, pre-staged local stored file ID; no inline bytes, inbound URLs, arbitrary paths, or upload routes.
- SQLite admission/idempotency protects processes sharing one local database. It is not a distributed broker.
- `send --wait` polls; `send --follow` relies on bounded peer SSE behavior.

## Publication checklist

Do not tag, merge, or publish until the PR head equals the candidate, all required CI checks are green, and Release Check passes. Tag the merged release commit as `v0.5.0`, then create the GitHub release with the exact wheel and sdist produced for that commit.
