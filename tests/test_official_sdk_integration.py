from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge import client
from hermes_a2a_bridge.server import create_app
from hermes_a2a_bridge.store import Store


HARNESS = Path(__file__).with_name("official_sdk_harness.py")


def _sdk_python() -> str:
    value = os.environ.get("A2A_SDK_PYTHON")
    if not value or not Path(value).is_file():
        pytest.skip("set A2A_SDK_PYTHON to an interpreter with a2a-sdk[http-server] 1.1.0")
    return value


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


@pytest.mark.integration
async def test_official_sdk_capability_probe_for_file_shapes():
    sdk_python = _sdk_python()
    process = await asyncio.create_subprocess_exec(
        sdk_python, str(HARNESS), "capability-probe",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 0, stderr.decode(errors="replace")
    payload = json.loads(stdout)
    results = payload["results"]

    assert payload["sdkVersion"] in {"1.0.3", "1.1.0"}
    assert "file" not in payload["partFields"]
    for name in ("fileId", "uri", "bytes"):
        assert results[name]["accepted"] is False
        assert "no field named \"file\"" in results[name]["exception"]
    assert results["sdk_url"]["accepted"] is True
    assert results["sdk_raw"]["accepted"] is True
    assert "aGVsbG8=" not in stdout.decode(errors="replace")


@pytest.mark.integration
async def test_bridge_client_against_official_sdk_server():
    sdk_python = _sdk_python()
    port = _free_port()
    process = await asyncio.create_subprocess_exec(
        sdk_python, str(HARNESS), "server", "--port", str(port),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                card = await client.fetch_agent_card(base)
                break
            except Exception:
                await asyncio.sleep(0.05)
        else:
            stderr = (await process.stderr.read()).decode(errors="replace")
            raise AssertionError(f"official SDK server did not start: {stderr}")
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
        task = await client.send_message(base, "hello", metadata={"probe": "integration"})
        assert task["metadata"]["requestMetadata"] == {"probe": "integration"}
        data_task = await client.send_message(base, data={"alpha": 1, "nested": {"ok": True}})
        assert data_task["metadata"]["requestDataPartCount"] == 1
        assert data_task["artifacts"][0]["parts"][0]["data"]["acceptedDataParts"][0]["nested"]["ok"] is True
        mixed_task = await client.send_message(base, "hello", data=[{"name": "Ada"}, {"name": "Grace"}])
        assert mixed_task["artifacts"][0]["parts"][0]["data"]["acceptedDataParts"][0][1]["name"] == "Grace"
        events = [event async for event in client.stream_message(base, "stream")]
        assert events[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        data_events = [event async for event in client.stream_message(base, data={"stream": True})]
        artifact = next(event["data"]["artifactUpdate"]["artifact"] for event in data_events if "artifactUpdate" in event["data"])
        assert artifact["parts"][0]["data"]["streamedDataParts"][0]["stream"] is True
    finally:
        await _stop(process)


@pytest.mark.integration
async def test_official_sdk_client_against_bridge_server(config, tmp_path):
    sdk_python = _sdk_python()
    token = "integration-only-token"
    config["server"]["auth_token"] = token
    config["executor"]["command"] = [
        sys.executable, "-c", "print('{\"ok\": true, \"source\": \"hermes\"}')", "{prompt}",
    ]
    bridge = TestClient(TestServer(create_app(config, Store(tmp_path / "integration.sqlite3"))))
    await bridge.start_server()
    base = str(bridge.make_url("")).rstrip("/")
    config["server"]["public_url"] = base
    config["server"]["public_url_explicit"] = True
    env = {**os.environ, "HERMES_A2A_TEST_TOKEN": token}
    try:
        process = await asyncio.create_subprocess_exec(
            sdk_python, str(HARNESS), "client", "--url", base,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        payload = json.loads(stdout)
        assert payload["send"][0]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert payload["dataSend"][0]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert payload["dataSend"][0]["task"]["history"][0]["parts"][0]["data"]["sdkClient"] is True
        assert payload["dataSend"][0]["task"]["artifacts"][0]["parts"][0]["data"]["ok"] is True
        assert payload["mixedSend"][0]["task"]["history"][0]["parts"][1]["data"][1]["name"] == "Grace"
        assert payload["stream"][-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert payload["dataStream"][-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        data_artifact = next(item["artifactUpdate"]["artifact"] for item in payload["dataStream"] if "artifactUpdate" in item)
        assert data_artifact["parts"][0]["data"]["ok"] is True
        assert token not in stdout.decode(errors="replace")
        assert token not in stderr.decode(errors="replace")
    finally:
        await bridge.close()


@pytest.mark.integration
async def test_official_sdk_file_parts_are_rejected_by_bridge(config, tmp_path):
    sdk_python = _sdk_python()
    token = "integration-only-token"
    config["server"]["auth_token"] = token
    config["executor"]["command"] = [
        sys.executable, "-c", "print('should not execute')", "{prompt}",
    ]
    bridge = TestClient(TestServer(create_app(config, Store(tmp_path / "file-parts.sqlite3"))))
    await bridge.start_server()
    base = str(bridge.make_url("")).rstrip("/")
    config["server"]["public_url"] = base
    config["server"]["public_url_explicit"] = True
    env = {**os.environ, "HERMES_A2A_TEST_TOKEN": token}
    try:
        process = await asyncio.create_subprocess_exec(
            sdk_python, str(HARNESS), "file-client", "--url", base,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        payload = json.loads(stdout)
        for key in ("rawFileSend", "imageFileSend", "urlFileStream"):
            result = payload[key]
            assert result["request"]["message"]["parts"][0]
            assert result["raised"] is True
            assert "unsupported" in result["exception"].lower()
            assert token not in result["exception"]
        serialized = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        assert token not in serialized
    finally:
        await bridge.close()
