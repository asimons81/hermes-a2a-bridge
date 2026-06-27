from __future__ import annotations

import argparse
import json

from aiohttp.test_utils import make_mocked_request

import hermes_a2a_bridge
from hermes_a2a_bridge.cli import register_cli
from hermes_a2a_bridge.config import default_config, load_config
from hermes_a2a_bridge.models import build_agent_card
from hermes_a2a_bridge.schemas import TOOL_SCHEMAS
from hermes_a2a_bridge.server import health, wire


async def test_release_candidate_smoke_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    loaded = load_config(config_path)
    assert loaded["parts"]["allow_file_parts"] is False
    assert loaded["files"]["auto_fetch_remote_urls"] is False

    card = wire(build_agent_card(default_config()))
    assert card["version"] == hermes_a2a_bridge.__version__ == "0.4.6"
    assert card["defaultInputModes"] == ["text/plain", "application/json"]
    assert "metadata" not in card

    response = await health(make_mocked_request("GET", "/health"))
    assert json.loads(response.text) == {"status": "ok", "version": "0.4.6"}

    parser = argparse.ArgumentParser()
    register_cli(parser)
    help_text = parser.format_help()
    assert "send" in help_text
    assert "files" in help_text

    schema = TOOL_SCHEMAS["a2a_send_message"]["parameters"]["properties"]
    assert schema["file_ids"]["items"]["pattern"] == "^file_[A-Za-z0-9_-]{16,}$"
    assert not {"file_path", "file_paths", "path", "uri", "bytes"} & set(schema)
