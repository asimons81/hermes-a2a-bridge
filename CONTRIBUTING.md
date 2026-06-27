# Contributing

Hermes A2A Bridge is a local-first HTTP+JSON subset for Hermes Agent. Contributions should keep release claims narrow and preserve the closed file boundary unless a design pass explicitly changes it.

## Local Checks

Use Python 3.11 or newer. CI currently tests Python 3.11 and 3.12.

```bash
python -m pip install -e ".[test]"
python -m pytest
python -m compileall -q hermes_a2a_bridge tests
python -m build
```

Optional official SDK tests require an explicit isolated `A2A_SDK_PYTHON` interpreter. They should skip cleanly during normal test runs when that variable is unset.

## Boundaries

- Do not broaden file support without a design pass, tests, and docs.
- Do not add arbitrary path, upload, inline byte, remote URL inbound, or remote fetch support accidentally.
- Do not claim full A2A conformance.
- Do not commit generated build artifacts, local databases, caches, env files, tokens, or local absolute paths.

## Pull Requests

Keep changes focused. Runtime protocol changes, file-boundary changes, and public conformance wording need especially clear tests and documentation.
