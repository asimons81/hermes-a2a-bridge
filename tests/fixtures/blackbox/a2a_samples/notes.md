# Official a2a-samples probe

- Repository: `https://github.com/a2aproject/a2a-samples`
- Sample: `samples/python/agents/helloworld`
- Date: 2026-06-24
- Runtime command: `uv run .`
- Discovery result: passed; the public Agent Card is captured in `agent_card.json`.
- Operation result: skipped before send/stream/task lookup because the sample advertises `protocolVersion: 0.3`, `preferredTransport: JSONRPC`, and only a JSON-RPC interface. Hermes A2A Bridge does not implement JSON-RPC or 0.3 REST runtime support.
