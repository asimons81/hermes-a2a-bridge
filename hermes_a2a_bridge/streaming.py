"""Process-local task event buffering and SSE serialization."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from .models import TaskState

TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
    TaskState.REJECTED,
}


@dataclass
class _TaskChannel:
    events: deque[dict[str, Any]]
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    terminal: bool = False


class EventBroker:
    """A bounded live fan-out bus; SQLite is the durable replay authority."""

    def __init__(self, max_events: int = 100):
        self.max_events = max_events
        self._channels: dict[str, _TaskChannel] = {}

    def ensure(self, task_id: str) -> None:
        self._channels.setdefault(task_id, _TaskChannel(deque(maxlen=self.max_events)))

    def subscribe(self, task_id: str) -> asyncio.Queue:
        self.ensure(task_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_events)
        self._channels[task_id].subscribers.add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        channel = self._channels.get(task_id)
        if not channel:
            return
        channel.subscribers.discard(queue)
        if channel.terminal and not channel.subscribers:
            self._channels.pop(task_id, None)

    def publish(self, task_id: str, event: dict[str, Any], *, terminal: bool = False) -> None:
        self.ensure(task_id)
        channel = self._channels[task_id]
        channel.events.append(event)
        channel.terminal = channel.terminal or terminal
        for queue in tuple(channel.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        if channel.terminal and not channel.subscribers:
            self._channels.pop(task_id, None)

    def close(self, task_id: str) -> None:
        """Close live subscribers without inventing an unpersisted wire event."""
        channel = self._channels.get(task_id)
        if not channel:
            return
        channel.terminal = True
        for queue in tuple(channel.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        if not channel.subscribers:
            self._channels.pop(task_id, None)

    def buffered(self, task_id: str) -> list[dict[str, Any]]:
        channel = self._channels.get(task_id)
        return list(channel.events) if channel else []


def sse_response() -> web.StreamResponse:
    return web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def write_sse(response: web.StreamResponse, envelope: dict[str, Any]) -> None:
    payload = json.dumps(envelope["data"], ensure_ascii=False, separators=(",", ":"))
    frame = f"id: {envelope['id']}\nevent: {envelope.get('event', 'message')}\ndata: {payload}\n\n"
    await response.write(frame.encode("utf-8"))
