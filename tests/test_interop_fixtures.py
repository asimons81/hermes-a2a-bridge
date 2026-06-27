import json
from pathlib import Path

from hermes_a2a_bridge.models import AgentCard, Message, StreamResponse, Task


FIXTURES = Path(__file__).parent / "fixtures" / "a2a"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_interoperability_fixtures_match_the_implemented_subset():
    AgentCard.model_validate(_load("valid_agent_card.json"))
    AgentCard.model_validate(_load("minimal_agent_card.json"))
    AgentCard.model_validate(_load("rich_agent_card.json"))
    Message.model_validate(_load("message_send_request.json")["message"])
    Message.model_validate(_load("message_stream_request.json")["message"])
    Task.model_validate(_load("task_completed.json"))
    Task.model_validate(_load("task_failed.json"))
    for name in (
        "status_update_working.json",
        "artifact_update_text.json",
        "status_update_completed.json",
        "status_update_failed.json",
    ):
        StreamResponse.model_validate(_load(name))

    for name in ("replay_gap_error.json", "no_new_events_error.json"):
        payload = _load(name)
        assert payload["success"] is False
        assert payload["code"]

    assert _load("unsupported_file_part_request.json")["message"]["parts"][0]["url"]
    assert _load("malformed_unknown_part_request.json")["message"]["parts"][0]["kind"] == "mystery"
