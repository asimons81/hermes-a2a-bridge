from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge import cli, client
from hermes_a2a_bridge.config import save_config
from hermes_a2a_bridge.errors import ClientError
from hermes_a2a_bridge.models import AgentCard, Message, Task
from fake_external_a2a import create_fake_external_app


FIXTURES = Path(__file__).parent / "fixtures" / "a2a"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def external_server():
    server = TestServer(create_fake_external_app())
    await server.start_server()
    yield server
    await server.close()


def test_minimal_and_rich_external_agent_cards_parse():
    minimal = AgentCard.model_validate(load_fixture("minimal_agent_card.json"))
    rich = AgentCard.model_validate(load_fixture("rich_agent_card.json"))
    assert minimal.capabilities.streaming is True
    assert minimal.capabilities.push_notifications is False
    assert rich.supported_interfaces[0].protocol_version == "1.0"
    assert rich.skills[0].examples == ["Summarize this paragraph"]


def test_external_message_and_task_fixtures_parse():
    message = Message.model_validate(load_fixture("message_send_request.json")["message"])
    completed = Task.model_validate(load_fixture("task_completed.json"))
    failed = Task.model_validate(load_fixture("task_failed.json"))
    assert message.role == "user" and message.message_id == "external-send-message"
    assert completed.status.state.value == "TASK_STATE_COMPLETED"
    assert failed.status.state.value == "TASK_STATE_FAILED"


async def test_direct_agent_card_url_and_fake_send(external_server):
    direct = str(external_server.make_url("/.well-known/agent-card.json"))
    card = await client.fetch_agent_card(direct)
    endpoint = client.agent_endpoint(card)
    task = await client.send_message(endpoint + "/", "hello")
    assert task["id"] == "external-task-1"
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_fake_external_stream_handles_comments_multiline_unknown_fields_and_event(external_server):
    base = str(external_server.make_url("")).rstrip("/")
    events = [event async for event in client.stream_message(base, "hello")]
    assert [event["id"] for event in events] == [41, 42]
    assert events[0]["event"] == "external-task"
    assert events[0]["data"]["task"]["id"] == "external-task-1"


async def test_fake_external_task_lookup_and_cancel(external_server):
    base = str(external_server.make_url("")).rstrip("/")
    task = await client.get_task(base, "external-task-1")
    canceled = await client.cancel_task(base, "external-task-1")
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert canceled["status"]["state"] == "TASK_STATE_CANCELED"


async def test_fake_external_malformed_stream_is_structured(external_server):
    base = str(external_server.make_url("/malformed")).rstrip("/")
    with pytest.raises(ClientError) as caught:
        _ = [event async for event in client.stream_message(base, "hello")]
    assert caught.value.status == 200
    assert caught.value.code == "malformed_sse"
    assert caught.value.payload["success"] is False


async def test_structured_external_failure_and_token_redaction(external_server):
    base = str(external_server.make_url("")).rstrip("/")
    secret = "external-secret-token"
    with pytest.raises(ClientError) as caught:
        await client.send_message(base, "fail", secret)
    assert caught.value.status == 400
    assert caught.value.code == "content_type_not_supported"
    assert secret not in str(caught.value)
    assert secret not in json.dumps(caught.value.payload)


async def test_cli_discover_and_send_against_fake_server(
    external_server, config, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    save_config(config)
    base = str(external_server.make_url("")).rstrip("/")
    discover_args = argparse.Namespace(a2a_command="discover", url=base, json=True)
    assert await asyncio.to_thread(cli.a2a_command, discover_args) == 0
    discovered = json.loads(capsys.readouterr().out)
    assert discovered["agent"]["name"] == "Fake External A2A"

    send_args = argparse.Namespace(
        a2a_command="send", agent=base, message="hello", token=None, json=True,
    )
    assert await asyncio.to_thread(cli.a2a_command, send_args) == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["task"]["id"] == "external-task-1"
