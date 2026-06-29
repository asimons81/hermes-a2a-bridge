# Agent Guide

## What this project is

`hermes-a2a-bridge` is a thin, local-first bridge that lets Hermes Agent call other HTTP+JSON agents and expose itself as an A2A-shaped agent. It supports text and structured JSON data parts with SSE streaming, durable replay, and task management. It does **not** claim full A2A conformance.

## What to read first

| Task | Read |
|------|------|
| Install and enable | [README.md](README.md) (first ~50 lines) |
| Run tests, build, develop | [README.md](README.md) § Development + [CONTRIBUTING.md](CONTRIBUTING.md) |
| Understand A2A compatibility limits | [docs/INTEROP.md](docs/INTEROP.md) |
| Understand file safety boundaries | [docs/FILE_BOUNDARY_STATUS.md](docs/FILE_BOUNDARY_STATUS.md) |
| Prepare a release | [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) |
| Understand Hermes tool surface | [hermes_a2a_bridge/skills/a2a-bridge/SKILL.md](hermes_a2a_bridge/skills/a2a-bridge/SKILL.md) |
| External interop search history | [docs/EXTERNAL_INTEROP.md](docs/EXTERNAL_INTEROP.md) |
| Design history (file parts) | [docs/FILE_PARTS_DESIGN.md](docs/FILE_PARTS_DESIGN.md) + [docs/INBOUND_FILE_PARTS_DESIGN.md](docs/INBOUND_FILE_PARTS_DESIGN.md) |

## Hard boundaries

**Never add or claim:**

- Full A2A conformance
- `/v1` routes
- JSON-RPC runtime
- OAuth, signed Cards, gRPC, public tunneling
- Inline bytes / base64 in message parts
- Remote URL fetching or inbound URL file parts
- Arbitrary local path reads from inbound requests
- CLI `--file PATH` (only `--file-id` with stored IDs)
- File gates enabled by default
- Upload routes
- `a2a-sdk` as a runtime dependency

**Never claim:**

- Hermes Agent upstream PR #54150 is merged (it is not; manual `plugins.enabled` config is the supported path for v0.17.0)
- Public stored-ID peer capture exists (it does not)
- SDK-to-Hermes stored fileId interop works (probed SDK models reject nested `file` objects)

## Common tasks

```bash
# Run tests
python -m pytest

# Editable install
python -m pip install -e ".[test]"

# Compile check
python -m compileall -q hermes_a2a_bridge tests

# Build package (wheel + sdist)
python -m build

# Check version
python -c "import hermes_a2a_bridge; print(hermes_a2a_bridge.__version__)"

# Read-only install and Hermes activation diagnostics
hermes-a2a-bridge doctor-install --json
python -m hermes_a2a_bridge doctor-install --json
```

Optional SDK integration tests require an isolated `A2A_SDK_PYTHON` interpreter pointing at `a2a-sdk[http-server]`. They skip cleanly when unset.

## Do not load everything

The README and docs/INTEROP.md contain extensive version history. An agent performing a focused task (e.g., "fix a test" or "update the config schema") should read only the relevant doc, not ingest the entire repo. Point the agent at the docs map above.

If you need to understand which file-boundary assertions exist, read `docs/FILE_BOUNDARY_STATUS.md` (216 lines). If you need the full design rationale, read `docs/FILE_PARTS_DESIGN.md` + `docs/INBOUND_FILE_PARTS_DESIGN.md`. Do not load all three for a simple config change.

## Package metadata

- Version: `0.4.7` (in `pyproject.toml`, `plugin.yaml`, and runtime `__version__`)
- Python: `>=3.11,<4.0`
- Entry point: `hermes_agent.plugins` → `a2a-bridge = hermes_a2a_bridge`
- Standalone helper: `hermes-a2a-bridge doctor-install` and `python -m hermes_a2a_bridge doctor-install`
- Bundled skill: `hermes_a2a_bridge/skills/a2a-bridge/SKILL.md`
- CI: Python 3.11/3.12/3.13 on Ubuntu, 3.11 on Windows
