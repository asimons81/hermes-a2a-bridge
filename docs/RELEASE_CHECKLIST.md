# Release Checklist

This checklist is for the v0.4.6 release artifact verification pass. It is packaging guidance only and does not expand runtime protocol support.

GitHub Actions now mirrors the local verification paths:

- `CI` runs tests, editable install verification, and compile checks on pull requests, pushes to `main`, and manual dispatch.
- `Package` builds the wheel and sdist on pull requests, pushes to `main`, and manual dispatch.
- `Release Check` is manual-only and rebuilds artifacts, installs the wheel into a temporary virtual environment, and runs import, version, module, and entry-point smoke checks without publishing.

## Baseline

- Confirm the project directory is not a Git repository unless the user explicitly requests initialization.
- Run `python -m pytest`.
- Run `python -m pip install -e .`.
- Run `python -m compileall -q hermes_a2a_bridge tests`.
- Confirm `python -c "import hermes_a2a_bridge; print(hermes_a2a_bridge.__version__)"` prints `0.4.6`.
- Confirm package metadata requires Python `>=3.11,<4.0`, and classifiers match the CI-tested Python 3.11 and 3.12 versions.

## Build Artifacts

- Prefer `python -m build`.
- If `build` is unavailable, install it only into a temporary build environment.
- Expected artifact names for v0.4.6:
  - `dist/hermes_a2a_bridge-0.4.6-py3-none-any.whl`
  - `dist/hermes_a2a_bridge-0.4.6.tar.gz`
- Do not upload artifacts to PyPI during verification.

## Artifact Inspection

- Confirm package modules are present in the wheel and sdist.
- Confirm `hermes_a2a_bridge/skills/a2a-bridge/SKILL.md` is present.
- Confirm runtime dependencies do not include `a2a-sdk`.
- Confirm no `.db`, SQLite, temp, cache, local absolute path, bearer token, raw stored bytes, or unexpected large files are present.
- Confirm version metadata remains `0.4.6`.
- Treat docs and tests as source-tree verification assets; do not assume they are installed from the wheel unless packaging policy changes.

## Wheel Install Smoke

- Create a clean temporary virtual environment.
- Install the built wheel into that environment.
- Run import and metadata checks:
  - `python -c "import hermes_a2a_bridge; print(hermes_a2a_bridge.__version__)"`
  - `python -c "import importlib.metadata as m; print(m.version('hermes-a2a-bridge'))"`
- Inspect `hermes_agent.plugins` entry points and confirm `a2a-bridge`.
- Import key modules: `config`, `server`, `client`, and `tools`.
- Confirm bundled skill package data can be read from the installed wheel.
- Do not expect `python -m hermes_a2a_bridge` to work; the CLI is registered through the Hermes plugin.

## Optional SDK Checks

- Optional SDK tests require `A2A_SDK_PYTHON`.
- Run the test file once with an isolated `a2a-sdk==1.1.0` interpreter and once with an isolated `a2a-sdk==1.0.3` interpreter when available.
- Do not add the official SDK as a runtime dependency.
- If SDK interpreters are unavailable, verify `tests/test_official_sdk_integration.py` skips cleanly.

## File Boundary

- Defaults must remain closed:
  - `parts.allow_file_parts: false`
  - `parts.allow_file_id_references: false`
  - `parts.allow_remote_url_file_references: false`
  - `parts.allow_inline_file_bytes: false`
  - `files.auto_fetch_remote_urls: false`
- Do not add `send --file`, `stream --file`, upload routes, remote URL inbound support, inline bytes, arbitrary path support, `/v1`, JSON-RPC runtime support, OAuth, signed Cards, public tunneling, or gRPC.
- Do not claim full A2A conformance or public stored-ID peer compatibility.

## Remaining Risks

- Public no-auth stored-ID peer capture is still absent.
- Official SDK 1.1.0 and 1.0.3 request models cannot emit the nested Hermes stored `fileId` shape.
- Stored file ID support remains a gated local subset, not broad file-part conformance.
