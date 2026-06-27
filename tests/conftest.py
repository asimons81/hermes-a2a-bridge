from __future__ import annotations

import pytest

from hermes_a2a_bridge.config import default_config


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    value = default_config()
    value["server"]["auth_token"] = "test-secret-token"
    return value

