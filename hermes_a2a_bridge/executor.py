"""Safe subprocess adapter for Hermes' verified one-shot CLI.

Uses synchronous subprocess.Popen in a thread pool executor to avoid the
nested asyncio event loop that triggers C-extension SIGABRT (-6) during
interpreter teardown.  See issue #3 for full diagnosis.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Any

from .auth import redact_secrets
from .errors import ExecutorCanceled, ExecutorError


VERIFIED_HERMES_COMMAND = ["hermes", "chat", "-q", "{prompt}"]

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_BOX_LINE_RE = re.compile(r"^[\s┌┐└┘├┤┬┴┼─━│┃╭╮╰╯═]+$")
_NOISE_PREFIXES = (
    "session_id:",
    "Hermes Agent v",
    "Install directory:",
    "Install method:",
    "Python:",
    "OpenAI SDK:",
)


def clean_result_text(raw: str) -> str:
    """Return the final machine-consumable answer from Hermes CLI stdout.

    Prefer explicit JSON fields when a custom executor emits JSON. For the
    verified Hermes CLI, remove ANSI/control presentation, reasoning panels,
    session metadata, and banners while preserving the final answer verbatim.
    """
    text = _ANSI_ESCAPE_RE.sub("", raw).replace("\r", "")
    stripped = text.strip()
    if not stripped:
        return ""

    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("resultText", "result_text", "final", "answer", "response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    lines = text.splitlines()
    session_indexes = [i for i, line in enumerate(lines) if line.strip().lower().startswith("session_id:")]
    if session_indexes:
        tail = [line for line in lines[session_indexes[-1] + 1 :] if line.strip()]
        if tail:
            return "\n".join(tail).strip()

    # Rich/TTY output does not always emit session_id. In that form, the final
    # answer sits after initialization and before the resume/session footer.
    footer_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("Resume this session with:")),
        None,
    )
    if footer_index is not None:
        lines = lines[:footer_index]
    init_indexes = [i for i, line in enumerate(lines) if "Initializing agent..." in line]
    if init_indexes:
        lines = lines[init_indexes[-1] + 1 :]

    cleaned: list[str] = []
    in_reasoning = False
    for line in lines:
        value = line.strip()
        if value.startswith("┌─ Reasoning") or value.startswith("╭─ Reasoning"):
            in_reasoning = True
            continue
        if in_reasoning:
            if _BOX_LINE_RE.fullmatch(line) or value.startswith(("└", "╰")):
                in_reasoning = False
            continue
        if not value or _BOX_LINE_RE.fullmatch(line):
            continue
        if "⚕ Hermes" in value:
            continue
        if value.startswith(_NOISE_PREFIXES):
            continue
        cleaned.append(line[4:] if line.startswith("    ") else line.rstrip())
    return "\n".join(cleaned).strip()


class ExecutorManager:
    """Tracks cancellation state and Popen handles for executor tasks.

    Subprocesses run synchronously in a thread pool.  The manager
    coordinates cancellation via ``threading.Event`` signals *and*
    direct ``Popen.kill()`` calls through a thread-safe handle store,
    so that a cancel request terminates the subprocess immediately
    without waiting for the thread's polling loop.
    """

    def __init__(self):
        self._cancel_requested: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = asyncio.Lock()
        self._proc_lock = threading.Lock()

    # --- Called from async context (by execute() and server.py) ---

    async def register(self, task_id: str, event: threading.Event) -> None:
        """Register a cancel event for a task.

        If the task was already marked for cancellation the event is
        set immediately so the executor thread aborts right away.
        """
        async with self._lock:
            if task_id in self._cancel_requested:
                event.set()
            self._cancel_events[task_id] = event

    async def unregister(self, task_id: str) -> None:
        """Remove cancellation tracking and the Popen handle."""
        async with self._lock:
            self._cancel_events.pop(task_id, None)
            self._cancel_requested.discard(task_id)
        with self._proc_lock:
            self._processes.pop(task_id, None)

    async def is_cancel_requested(self, task_id: str) -> bool:
        async with self._lock:
            return task_id in self._cancel_requested

    async def has_process(self, task_id: str) -> bool:
        """Return True if a Popen handle is registered for *task_id*."""
        with self._proc_lock:
            return task_id in self._processes

    async def cancel(self, task_id: str, grace_seconds: float = 3) -> bool:
        """Mark *task_id* as canceled and kill the subprocess immediately.

        *grace_seconds* is accepted for backward compatibility with
        server.py callers; the thread-based subprocess is killed right
        away rather than waiting for a grace window.
        """
        async with self._lock:
            self._cancel_requested.add(task_id)
        # Kill the Popen handle directly (thread-safe).
        with self._proc_lock:
            proc = self._processes.get(task_id)
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # Signal the cancel event so the thread exits the polling
            # loop cleanly with ExecutorCanceled.
            async with self._lock:
                event = self._cancel_events.get(task_id)
            if event is not None:
                event.set()
            return True
        # Fallback: signal the cancel event for the thread polling loop.
        async with self._lock:
            event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()
            return True
        return False

    async def cancel_all(self, grace_seconds: float = 0) -> None:
        """Cancel every tracked task (used on server shutdown).

        Kills all registered Popen handles and signals all cancel
        events so executor threads abort promptly.
        """
        async with self._lock:
            events = list(self._cancel_events.values())
            for task_id in list(self._cancel_events):
                self._cancel_requested.add(task_id)
        with self._proc_lock:
            for proc in self._processes.values():
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        for event in events:
            event.set()

    # --- Called from executor thread (by _run_subprocess_sync) ---

    def register_process(self, task_id: str, proc: subprocess.Popen) -> None:
        """Stash a Popen handle for direct cancellation.

        This is NOT async -- it is called from the executor thread.
        """
        with self._proc_lock:
            self._processes[task_id] = proc


def command_argv(config: dict[str, Any], prompt: str) -> list[str]:
    configured = config.get("executor", {}).get("command")
    if configured is None:
        raise ExecutorError(
            "No Hermes executor command configured. "
            "Set executor.command in ~/.hermes/a2a/config.yaml"
        )
    elif isinstance(configured, str):
        template = shlex.split(configured, posix=os.name != "nt")
    elif isinstance(configured, list) and all(isinstance(v, str) for v in configured):
        template = configured
    else:
        raise ExecutorError("executor.command must be null, a string, or a list of strings")
    if not template or not any("{prompt}" in part for part in template):
        raise ExecutorError("executor.command must contain a {prompt} placeholder")
    return [part.replace("{prompt}", prompt) for part in template]


def _run_subprocess_sync(
    argv: list[str],
    timeout: int,
    cancel_event: threading.Event | None,
    manager: ExecutorManager | None,
    task_id: str | None,
) -> tuple[bytes, bytes, int]:
    """Run a subprocess synchronously in a worker thread.

    Uses ``subprocess.Popen`` with a 1-second polling loop so that
    cancellation (via ``cancel_event`` or direct ``Popen.kill()``
    through the manager) and timeouts are responsive.

    This completely avoids ``asyncio.create_subprocess_exec``, which
    triggers a C-extension SIGABRT when the Hermes subprocess shuts
    down inside a parent that already has a busy asyncio event loop.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if manager is not None and task_id is not None:
        manager.register_process(task_id, proc)

    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            proc.communicate()
            raise ExecutorError(f"Hermes executor timed out after {timeout} seconds")

        # Check cancellation (event signal from async context, or
        # direct kill via manager.cancel()).
        if cancel_event is not None and cancel_event.is_set():
            proc.kill()
            proc.communicate()
            raise ExecutorCanceled("Task canceled while executor process was running")

        try:
            stdout, stderr = proc.communicate(timeout=min(1.0, remaining))
            return stdout, stderr, proc.returncode
        except subprocess.TimeoutExpired:
            continue


async def execute(
    prompt: str,
    config: dict[str, Any],
    task_id: str | None = None,
    manager: ExecutorManager | None = None,
) -> str:
    """Execute a Hermes prompt in a subprocess via a thread pool.

    The subprocess runs synchronously in an executor thread so that the
    bridge's asyncio event loop is never involved in subprocess creation
    or teardown.  This sidesteps the nested-event-loop C-extension
    SIGABRT described in issue #3.
    """
    limits = config.get("limits", {})
    executor_cfg = config.get("executor", {})
    max_chars = int(
        min(
            limits.get("max_prompt_chars", 20000),
            executor_cfg.get("max_prompt_chars", 20000),
        )
    )
    if len(prompt) > max_chars:
        raise ExecutorError(f"Prompt exceeds the {max_chars} character limit")

    argv = command_argv(config, prompt)
    timeout = int(
        executor_cfg.get("timeout_seconds", limits.get("task_timeout_seconds", 300))
    )

    loop = asyncio.get_running_loop()
    cancel_event: threading.Event | None = None
    canceled_by_manager = False

    if task_id is not None and manager is not None:
        cancel_event = threading.Event()
        await manager.register(task_id, cancel_event)

    try:
        stdout, stderr, returncode = await loop.run_in_executor(
            None,
            _run_subprocess_sync,
            argv,
            timeout,
            cancel_event,
            manager,
            task_id,
        )
        # Capture cancellation flag before cleanup removes it.
        if task_id is not None and manager is not None:
            canceled_by_manager = await manager.is_cancel_requested(task_id)
    except FileNotFoundError as exc:
        raise ExecutorError(f"Hermes executor was not found: {argv[0]}") from exc
    except (ExecutorError, ExecutorCanceled):
        raise
    except Exception as exc:
        raise ExecutorError(f"Hermes executor subprocess failed: {exc}") from exc
    finally:
        if task_id is not None and manager is not None:
            await manager.unregister(task_id)

    if returncode != 0:
        if canceled_by_manager:
            raise ExecutorCanceled("Task canceled while executor process was running")
        detail = redact_secrets(stderr.decode("utf-8", errors="replace")).strip()
        if detail:
            raise ExecutorError(
                f"Hermes executor failed with exit code {returncode}. "
                f"stderr: {detail[:240]}"
            )
        raise ExecutorError(f"Hermes executor failed with exit code {returncode}")

    result = clean_result_text(stdout.decode("utf-8", errors="replace"))
    if not result:
        raise ExecutorError("Hermes executor returned an empty response")
    return result
