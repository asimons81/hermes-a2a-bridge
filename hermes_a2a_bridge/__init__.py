"""Hermes A2A Bridge plugin registration."""

from pathlib import Path

from .cli import a2a_command, register_cli
from .schemas import TOOL_SCHEMAS
from .tools import HANDLERS
from ._version import __version__


def register(ctx) -> None:
    for name, schema in TOOL_SCHEMAS.items():
        ctx.register_tool(
            name=name, toolset="a2a_bridge", schema=schema, handler=HANDLERS[name],
            is_async=True, description=schema["description"],
        )
    ctx.register_cli_command(
        name="a2a", help="Agent-to-Agent bridge", setup_fn=register_cli,
        handler_fn=a2a_command,
        description="Call remote A2A agents or expose Hermes on localhost.",
    )
    ctx.register_skill(
        "a2a-bridge", Path(__file__).parent / "skills" / "a2a-bridge" / "SKILL.md",
        "Use the local A2A bridge safely.",
    )
