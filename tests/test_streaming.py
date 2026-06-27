import pytest
from pydantic import ValidationError

from hermes_a2a_bridge.models import (
    Artifact,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)
from hermes_a2a_bridge.streaming import EventBroker


def test_stream_models_use_camel_case_and_exactly_one_payload():
    task = Task(id="t1", contextId="c1", status=TaskStatus(state=TaskState.SUBMITTED))
    artifact = Artifact(artifactId="a1", parts=[{"text": "hello"}])
    event = StreamResponse(artifactUpdate=TaskArtifactUpdateEvent(
        taskId="t1", contextId="c1", artifact=artifact, lastChunk=True,
    )).model_dump(by_alias=True, exclude_none=True, mode="json")
    assert set(event) == {"artifactUpdate"}
    assert event["artifactUpdate"]["taskId"] == "t1"
    assert event["artifactUpdate"]["lastChunk"] is True

    with pytest.raises(ValidationError, match="exactly one"):
        StreamResponse(task=task, message=Message(role="agent", parts=[{"text": "no"}]))


def test_event_broker_bounds_buffer_and_cleans_up_terminal_channels():
    broker = EventBroker(max_events=2)
    queue = broker.subscribe("t1")
    broker.publish("t1", {"n": 1})
    broker.publish("t1", {"n": 2})
    broker.publish("t1", {"n": 3})
    assert broker.buffered("t1") == [{"n": 2}, {"n": 3}]
    assert queue.qsize() == 2
    broker.publish("t1", {"done": True}, terminal=True)
    broker.unsubscribe("t1", queue)
    assert broker.buffered("t1") == []
