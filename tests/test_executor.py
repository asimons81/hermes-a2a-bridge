import asyncio
import sys

import pytest

from hermes_a2a_bridge.executor import ExecutorManager, command_argv, execute
from hermes_a2a_bridge.errors import ExecutorCanceled, ExecutorError


def test_null_executor_command_is_clear_error(config):
    config["executor"]["command"] = None
    with pytest.raises(ExecutorError, match="No Hermes executor command configured"):
        command_argv(config, "hello")


async def test_execute_enforces_prompt_limit(config):
    config["limits"]["max_prompt_chars"] = 3
    with pytest.raises(ExecutorError, match="character limit"):
        await execute("hello", config)


async def test_execute_returns_stdout(config):
    config["executor"]["command"] = [
        sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}",
    ]
    result = await execute("hello", config)
    assert result == "hello"


async def test_executor_cleans_up_state_after_normal_completion(config):
    config["executor"]["command"] = [
        sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}",
    ]
    manager = ExecutorManager()
    assert await execute("hello", config, task_id="t1", manager=manager) == "hello"
    # After normal completion the manager should have no cancellation state
    assert not await manager.is_cancel_requested("t1")


async def test_executor_respects_preemptive_cancellation(config):
    """Cancel a task before execute starts -- should raise immediately."""
    config["executor"]["command"] = [
        sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}",
    ]
    manager = ExecutorManager()
    await manager.cancel("t1")
    with pytest.raises(ExecutorCanceled, match="canceled"):
        await execute("hello", config, task_id="t1", manager=manager)
    # Cancellation state should be cleaned up
    assert not await manager.is_cancel_requested("t1")


async def test_executor_respects_runtime_cancellation(config):
    """Cancel a long-running task mid-execution -- should abort the subprocess."""
    config["executor"]["command"] = [
        sys.executable, "-c",
        "import time, sys; time.sleep(5); print(sys.argv[1])",
        "{prompt}",
    ]
    manager = ExecutorManager()

    # Fire off execute in a background task
    execute_task = asyncio.create_task(
        execute("hello", config, task_id="t1", manager=manager)
    )

    # Give the subprocess a moment to start
    await asyncio.sleep(0.3)

    # Cancel it
    await manager.cancel("t1")

    with pytest.raises(ExecutorCanceled, match="canceled"):
        await execute_task

    assert not await manager.is_cancel_requested("t1")


async def test_executor_times_out(config):
    """A subprocess that exceeds the timeout should raise ExecutorError."""
    config["executor"]["command"] = [
        sys.executable, "-c",
        "import time; time.sleep(10); print('done')",
        "{prompt}",
    ]
    config["executor"]["timeout_seconds"] = 1
    with pytest.raises(ExecutorError, match="timed out"):
        await execute("hello", config)


async def test_executor_fast_exit_nonzero(config):
    """A subprocess that exits with nonzero code raises ExecutorError."""
    config["executor"]["command"] = [
        sys.executable, "-c",
        "import sys; sys.exit(42)",
        "{prompt}",
    ]
    with pytest.raises(ExecutorError, match="exit code 42"):
        await execute("hello", config)


async def test_executor_empty_output_raises(config):
    """A subprocess that prints nothing should raise ExecutorError."""
    config["executor"]["command"] = [
        sys.executable, "-c",
        "",
        "{prompt}",
    ]
    with pytest.raises(ExecutorError, match="empty response"):
        await execute("hello", config)
