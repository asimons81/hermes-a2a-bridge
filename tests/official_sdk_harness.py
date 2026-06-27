"""Optional black-box harness. Run this file with an interpreter containing a2a-sdk."""

from __future__ import annotations

import argparse
import asyncio
import json
import os


def _parse(data, cls):
    from google.protobuf.json_format import ParseDict

    return ParseDict(data, cls)


def _task(state="TASK_STATE_COMPLETED", metadata=None, data_artifact=None):
    from a2a.types import a2a_pb2 as p

    data = {
        "id": "sdk-task-1",
        "contextId": "sdk-context-1",
        "status": {"state": state},
        "metadata": metadata or {"source": "official-sdk-harness"},
    }
    if state == "TASK_STATE_COMPLETED":
        if data_artifact is None:
            parts = [{"text": "sdk result", "mediaType": "text/plain"}]
        else:
            parts = [{"data": data_artifact, "metadata": {"source": "official-sdk-harness"}}]
        data["artifacts"] = [{"artifactId": "sdk-artifact-1", "parts": parts}]
    return _parse(data, p.Task())


def run_capability_probe() -> None:
    import importlib.metadata

    from google.protobuf.json_format import MessageToDict, ParseDict
    from a2a.types import a2a_pb2 as p

    shapes = {
        "fileId": {"file": {"fileId": "file_abcdefghijklmnopqrstuv"}},
        "uri": {"file": {"uri": "https://example.com/report.pdf"}},
        "bytes": {"file": {"bytes": "[OMITTED_BASE64_BYTES]"}},
        "sdk_url": {
            "url": "https://example.com/report.pdf",
            "filename": "report.pdf",
            "mediaType": "application/pdf",
        },
        "sdk_raw": {
            "raw": "[OMITTED_BASE64_BYTES]",
            "filename": "report.txt",
            "mediaType": "text/plain",
        },
    }
    results = {}
    for name, part in shapes.items():
        request = {
            "message": {
                "messageId": f"probe-{name}",
                "role": "ROLE_USER",
                "parts": [part],
            }
        }
        try:
            parsed = ParseDict(request, p.SendMessageRequest())
            wire = MessageToDict(parsed)
            for parsed_part in wire.get("message", {}).get("parts", []):
                if "raw" in parsed_part:
                    parsed_part["raw"] = "[OMITTED_BASE64_BYTES]"
            results[name] = {"accepted": True, "wire": wire}
        except Exception as exc:
            results[name] = {
                "accepted": False,
                "exceptionType": type(exc).__name__,
                "exception": str(exc).split("\n")[0],
            }
    print(json.dumps({
        "sdkPackage": "a2a-sdk",
        "sdkVersion": importlib.metadata.version("a2a-sdk"),
        "partFields": [field.name for field in p.Part.DESCRIPTOR.fields],
        "results": results,
    }))


def run_server(port: int) -> None:
    import uvicorn
    from google.protobuf.json_format import MessageToDict
    from starlette.applications import Starlette

    from a2a.server.request_handlers.request_handler import RequestHandler
    from a2a.server.routes.agent_card_routes import create_agent_card_routes
    from a2a.server.routes.rest_routes import create_rest_routes
    from a2a.types import a2a_pb2 as p
    from a2a.utils.errors import ContentTypeNotSupportedError, UnsupportedOperationError

    class Handler(RequestHandler):
        def __init__(self):
            self.current = _task()

        async def on_get_task(self, params, context):
            return self.current if params.id == "sdk-task-1" else None

        async def on_list_tasks(self, params, context):
            return p.ListTasksResponse(tasks=[self.current])

        async def on_cancel_task(self, params, context):
            if params.id != "sdk-task-1":
                return None
            self.current = _task("TASK_STATE_CANCELED")
            return self.current

        async def on_message_send(self, params, context):
            from google.protobuf.json_format import MessageToDict

            request_message = MessageToDict(params.message)
            first_part = request_message["parts"][0]
            text = first_part.get("text", "")
            if text == "fail":
                raise ContentTypeNotSupportedError("SDK harness rejected the request")
            metadata = {"source": "official-sdk-harness"}
            if params.message.HasField("metadata"):
                metadata["requestMetadata"] = MessageToDict(params.message.metadata)
            data_parts = [part["data"] for part in request_message["parts"] if "data" in part]
            if data_parts:
                metadata["requestDataPartCount"] = len(data_parts)
            data_artifact = {"acceptedDataParts": data_parts, "sdkVersion": "fixture"} if data_parts else None
            self.current = _task(metadata=metadata, data_artifact=data_artifact)
            return self.current

        async def on_message_send_stream(self, params, context):
            from google.protobuf.json_format import MessageToDict

            request_message = MessageToDict(params.message)
            data_parts = [part["data"] for part in request_message["parts"] if "data" in part]
            yield _task("TASK_STATE_SUBMITTED")
            artifact_parts = [{"text": "sdk streamed result", "mediaType": "text/plain"}]
            if data_parts:
                artifact_parts = [{"data": {"streamedDataParts": data_parts, "ok": True}}]
            yield _parse({
                "taskId": "sdk-task-1", "contextId": "sdk-context-1",
                "artifact": {
                    "artifactId": "sdk-artifact-1",
                    "parts": artifact_parts,
                },
                "lastChunk": True,
            }, p.TaskArtifactUpdateEvent())
            yield _parse({
                "taskId": "sdk-task-1", "contextId": "sdk-context-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }, p.TaskStatusUpdateEvent())

        async def on_subscribe_to_task(self, params, context):
            raise UnsupportedOperationError()
            yield

        async def on_create_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

        async def on_get_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

        async def on_list_task_push_notification_configs(self, params, context):
            raise UnsupportedOperationError()

        async def on_delete_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

        async def on_get_extended_agent_card(self, params, context):
            raise UnsupportedOperationError()

    url = f"http://127.0.0.1:{port}"
    card = _parse({
        "name": "Official SDK Integration Agent",
        "description": "Optional local black-box server.",
        "version": "1.1.0-integration",
        "supportedInterfaces": [{
            "url": url, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0",
        }],
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "echo", "name": "Echo", "description": "Deterministic text.",
            "tags": ["text"],
        }],
    }, p.AgentCard())
    app = Starlette(routes=create_agent_card_routes(card) + create_rest_routes(Handler()))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


async def run_client(base_url: str) -> None:
    import httpx
    from google.protobuf.json_format import MessageToDict, ParseDict

    from a2a.client.card_resolver import A2ACardResolver
    from a2a.client.client import ClientConfig
    from a2a.client.client_factory import ClientFactory
    from a2a.types import a2a_pb2 as p

    headers = {}
    token = os.environ.get("HERMES_A2A_TEST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def request(message_id: str, text: str, data=None):
        parts = [{"text": text, "mediaType": "text/plain"}] if text else []
        if data is not None:
            parts.append({"data": data, "metadata": {"source": "official-sdk-harness"}})
        return ParseDict({
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": parts,
                "metadata": {"probe": "official-sdk-harness"},
            }
        }, p.SendMessageRequest())

    http = httpx.AsyncClient(headers=headers)
    card = await A2ACardResolver(http, base_url).get_agent_card()
    client = ClientFactory(ClientConfig(
        streaming=False, httpx_client=http, supported_protocol_bindings=["HTTP+JSON"],
    )).create(card)
    sent = [MessageToDict(item) async for item in client.send_message(request("sdk-send", "hello"))]
    data_sent = [
        MessageToDict(item)
        async for item in client.send_message(request("sdk-data-send", "", {"sdkClient": True, "items": [1, 2]}))
    ]
    mixed_sent = [
        MessageToDict(item)
        async for item in client.send_message(
            request("sdk-mixed-send", "mixed", [{"name": "Ada"}, {"name": "Grace"}])
        )
    ]
    await client.close()

    http_stream = httpx.AsyncClient(headers=headers)
    stream_card = await A2ACardResolver(http_stream, base_url).get_agent_card()
    stream_client = ClientFactory(ClientConfig(
        streaming=True, httpx_client=http_stream, supported_protocol_bindings=["HTTP+JSON"],
    )).create(stream_card)
    streamed = [
        MessageToDict(item)
        async for item in stream_client.send_message(request("sdk-stream", "stream"))
    ]
    data_streamed = [
        MessageToDict(item)
        async for item in stream_client.send_message(request("sdk-data-stream", "", {"stream": True}))
    ]
    await stream_client.close()
    print(json.dumps({
        "card": MessageToDict(card),
        "send": sent,
        "dataSend": data_sent,
        "mixedSend": mixed_sent,
        "stream": streamed,
        "dataStream": data_streamed,
    }))


async def run_file_client(base_url: str) -> None:
    import httpx
    from google.protobuf.json_format import MessageToDict, ParseDict

    from a2a.client.card_resolver import A2ACardResolver
    from a2a.client.client import ClientConfig
    from a2a.client.client_factory import ClientFactory
    from a2a.types import a2a_pb2 as p

    headers = {}
    token = os.environ.get("HERMES_A2A_TEST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def request(message_id: str, part: dict):
        return ParseDict({
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": [part],
                "metadata": {"probe": "official-sdk-file-rejection"},
            }
        }, p.SendMessageRequest())

    async def capture_send(part: dict) -> dict:
        http = httpx.AsyncClient(headers=headers)
        card = await A2ACardResolver(http, base_url).get_agent_card()
        client = ClientFactory(ClientConfig(
            streaming=False, httpx_client=http, supported_protocol_bindings=["HTTP+JSON"],
        )).create(card)
        req = request("sdk-file-send", part)
        result = {"request": MessageToDict(req)}
        try:
            result["response"] = [MessageToDict(item) async for item in client.send_message(req)]
            result["raised"] = False
        except Exception as exc:
            text = str(exc)
            if token:
                text = text.replace(token, "[REDACTED]")
            result["raised"] = True
            result["exceptionType"] = type(exc).__name__
            result["exception"] = text
        finally:
            await client.close()
        return result

    async def capture_stream(part: dict) -> dict:
        http = httpx.AsyncClient(headers=headers)
        card = await A2ACardResolver(http, base_url).get_agent_card()
        client = ClientFactory(ClientConfig(
            streaming=True, httpx_client=http, supported_protocol_bindings=["HTTP+JSON"],
        )).create(card)
        req = request("sdk-file-stream", part)
        result = {"request": MessageToDict(req)}
        try:
            result["response"] = [MessageToDict(item) async for item in client.send_message(req)]
            result["raised"] = False
        except Exception as exc:
            text = str(exc)
            if token:
                text = text.replace(token, "[REDACTED]")
            result["raised"] = True
            result["exceptionType"] = type(exc).__name__
            result["exception"] = text
        finally:
            await client.close()
        return result

    print(json.dumps({
        "rawFileSend": await capture_send({
            "raw": "aGVsbG8=",
            "filename": "report.txt",
            "mediaType": "text/plain",
        }),
        "imageFileSend": await capture_send({
            "raw": "iVBORw0KGgo=",
            "filename": "image.png",
            "mediaType": "image/png",
        }),
        "urlFileStream": await capture_stream({
            "url": "https://example.test/report.txt",
            "filename": "report.txt",
            "mediaType": "text/plain",
        }),
    }))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("server")
    server.add_argument("--port", type=int, required=True)
    client = sub.add_parser("client")
    client.add_argument("--url", required=True)
    file_client = sub.add_parser("file-client")
    file_client.add_argument("--url", required=True)
    sub.add_parser("capability-probe")
    args = parser.parse_args()
    if args.command == "server":
        run_server(args.port)
    elif args.command == "file-client":
        asyncio.run(run_file_client(args.url))
    elif args.command == "capability-probe":
        run_capability_probe()
    else:
        asyncio.run(run_client(args.url))


if __name__ == "__main__":
    main()
