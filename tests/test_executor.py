import pytest
import sys

from hermes_a2a_bridge.executor import ExecutorManager, command_argv, execute
from hermes_a2a_bridge.errors import ExecutorError


def test_null_executor_command_is_clear_error(config):
    config["executor"]["command"] = None
    with pytest.raises(ExecutorError, match="No Hermes executor command configured"):
        command_argv(config, "hello")


async def test_execute_enforces_prompt_limit(config):
    config["limits"]["max_prompt_chars"] = 3
    with pytest.raises(ExecutorError, match="character limit"):
        await execute("hello", config)


async def test_process_registry_unregisters_after_normal_completion(config):
    config["executor"]["command"] = [
        sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}",
    ]
    manager = ExecutorManager()
    assert await execute("hello", config, task_id="t1", manager=manager) == "hello"
    assert not await manager.has_process("t1")
