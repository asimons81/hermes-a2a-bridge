# Public And Official Peer Search Refresh

Date: 2026-06-26.

No public no-auth HTTP+JSON A2A 1.0 endpoint was captured. No private credential or local auth credential was sent to a public service.

| Candidate | URL | Advertised protocol | Transport | No-auth | Locally runnable | Credentials or cloud needed | Exercises text/data/file artifacts | Exercises stored fileId | Decision |
|---|---|---|---|---|---|---|---|---|---|
| Official samples repository | https://github.com/a2aproject/a2a-samples | Mixed; older captured helloworld advertises 0.3 | JSON-RPC for older helloworld; REST exists in newer samples | Local only | Some samples runnable | Some Python agents require Google GenAI or Vertex setup | Text examples exist; file behavior not a stored-ID peer | No | Kept as a search target, but no no-credential HTTP+JSON stored-ID peer was captured. |
| Python `dice_agent_rest` sample | https://github.com/a2aproject/a2a-samples | A2A 1.0 REST sample path | HTTP+JSON/REST | Local only | Not used here | Requires Google GenAI or Vertex credentials in the official sample configuration | Text/tool behavior; not a stored-ID file peer | No | Rejected for this pass because private cloud credentials were not supplied. |
| A2A ITK v10 examples | https://github.com/a2aproject/a2a-samples | 1.0 and 0.3 oriented integration examples | HTTP+JSON, JSON-RPC, gRPC | Local integration setup | Heavy setup | Development SDK and integration-stack setup | Potentially broad integration behavior | No captured evidence | Rejected as too heavy for a no-credential peer refresh. |
| .NET BasicA2ADemo | https://github.com/a2aproject/a2a-dotnet | A2A 1.0 in SDK samples | HTTP+JSON/REST available | Local sample | Blocked here | No cloud credential identified, but local .NET SDK is required | Text sample candidate | No captured evidence | Rejected because `dotnet` was not installed in this environment. |
| Java SDK examples | https://github.com/a2aproject/a2a-java | A2A SDK supports REST transport | HTTP+JSON/REST | Local sample | Not attempted | Java stack setup is heavier than this pass | Potential text sample candidate | No captured evidence | Rejected because a smaller official SDK path already covered the local verification need. |
| Kagent A2A docs | https://kagent.dev/docs/kagent/examples/a2a-agents | A2A exposure for created agents | A2A client invocation | Not a simple public peer | Requires Kagent/Kubernetes | Requires platform setup | Agent interaction examples | No captured evidence | Rejected because it is not a small no-credential HTTP+JSON sample server. |

Result: public peer capture remains absent. The current evidence is official SDK local interop plus deterministic local Hermes/open-gate fixtures, not public stored-ID interoperability.
