# External Real Peer Phase 9 Notes

Real-peer run result: skipped.

Phase 9 searched for a public or safely runnable no-auth HTTP+JSON 1.0 A2A peer. The search found active A2A 1.0 specifications and SDK/sample repositories, including official Python, .NET, and Java SDK work, but no public no-auth HTTP+JSON 1.0 endpoint suitable for sending live requests from this local Hermes bridge.

Candidate blockers:

- `a2aproject/a2a-samples` has Python REST candidates, including `samples/python/agents/dice_agent_rest`, but that sample requires Google GenAI credentials before startup.
- `a2aproject/a2a-samples` has an ITK v10 REST agent, but it depends on a development branch of the Python SDK and is an integration test cluster rather than a small standalone peer.
- `a2aproject/a2a-dotnet` and the .NET samples advertise HTTP+JSON/REST support, but the local environment did not have `dotnet` installed.
- `a2aproject/a2a-java` advertises REST transport support, but running the Java sample stack would add a heavier local toolchain path than this verification pass needs.
- Kagent exposes agents through A2A, but it requires a Kubernetes/controller setup rather than a simple no-auth local peer.

No sanitized real-peer exchange fixtures are present in this directory because no safe real-peer exchange was completed. The reusable raw capture harness added in `tests/raw_capture_harness.py` is the deterministic fixture path for future interop runs.
