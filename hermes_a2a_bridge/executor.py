"""Safe subprocess adapter for Hermes' verified one-shot CLI."""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from .auth import redact_secrets
from .errors import ExecutorCanceled, ExecutorError


VERIFIED_HERMES_COMMAND = ["hermes", "chat", "-q", "{prompt}"]


class ExecutorManager:
    """Owns only subprocesses started for tasks in this server process."""

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancel_requested: set[str] = set()
        self._lock = asyncio.Lock()

    async def register(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        async with self._lock:
            canceled = task_id in self._cancel_requested
            if not canceled:
                self._processes[task_id] = process
        if canceled:
            await self._stop_process(process, 0)
            raise ExecutorCanceled("Task canceled before executor startup completed")

    async def unregister(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        async with self._lock:
            if self._processes.get(task_id) is process:
                self._processes.pop(task_id, None)
            self._cancel_requested.discard(task_id)

    async def cancel(self, task_id: str, grace_seconds: float = 3) -> bool:
        async with self._lock:
            self._cancel_requested.add(task_id)
            process = self._processes.get(task_id)
        if process is None or process.returncode is not None:
            return False
        await self._stop_process(process, grace_seconds)
        return True

    async def has_process(self, task_id: str) -> bool:
        async with self._lock:
            process = self._processes.get(task_id)
            return process is not None and process.returncode is None

    async def is_cancel_requested(self, task_id: str) -> bool:
        async with self._lock:
            return task_id in self._cancel_requested

    async def forget(self, task_id: str) -> None:
        async with self._lock:
            if task_id not in self._processes:
                self._cancel_requested.discard(task_id)

    async def cancel_all(self, grace_seconds: float = 0) -> None:
        async with self._lock:
            task_ids = tuple(self._processes)
        await asyncio.gather(
            *(self.cancel(task_id, grace_seconds) for task_id in task_ids),
            return_exceptions=True,
        )

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process, grace_seconds: float) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=max(0, grace_seconds))
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()


def command_argv(config: dict[str, Any], prompt: str) -> list[str]:
    configured = config.get("executor", {}).get("command")
    if configured is None:
        raise ExecutorError(
            "No Hermes executor command configured. Set executor.command in ~/.hermes/a2a/config.yaml"
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


async def execute(
    prompt: str,
    config: dict[str, Any],
    task_id: str | None = None,
    manager: ExecutorManager | None = None,
) -> str:
    limits = config.get("limits", {})
    executor = config.get("executor", {})
    max_chars = int(min(limits.get("max_prompt_chars", 20000), executor.get("max_prompt_chars", 20000)))
    if len(prompt) > max_chars:
        raise ExecutorError(f"Prompt exceeds the {max_chars} character limit")
    argv = command_argv(config, prompt)
    timeout = int(executor.get("timeout_seconds", limits.get("task_timeout_seconds", 300)))
    process = None
    canceled_by_manager = False
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if task_id is not None and manager is not None:
            await manager.register(task_id, process)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if task_id is not None and manager is not None:
            canceled_by_manager = await manager.is_cancel_requested(task_id)
    except FileNotFoundError as exc:
        raise ExecutorError(f"Hermes executor was not found: {argv[0]}") from exc
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ExecutorError(f"Hermes executor timed out after {timeout} seconds") from exc
    finally:
        if process is not None and task_id is not None and manager is not None:
            await manager.unregister(task_id, process)
    if process.returncode != 0:
        if canceled_by_manager:
            raise ExecutorCanceled("Task canceled while executor process was running")
        detail = redact_secrets(stderr.decode("utf-8", errors="replace")).strip()
        if detail:
            raise ExecutorError(
                f"Hermes executor failed with exit code {process.returncode}. stderr: {detail[:240]}"
            )
        raise ExecutorError(f"Hermes executor failed with exit code {process.returncode}")
    result = stdout.decode("utf-8", errors="replace").strip()
    if not result:
        raise ExecutorError("Hermes executor returned an empty response")
    return result
