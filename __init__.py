"""hermes-camera-watch: watch cameras you own and alert with a still when the scene changes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import schemas
from .commands import make_command
from .runtime import Runtime
from .tools import make_handlers

logger = logging.getLogger(__name__)

_RUNTIME: Optional[Runtime] = None


def get_runtime() -> Optional[Runtime]:
    """The live runtime, for tests. The dashboard process does not share it; it reads files."""
    return _RUNTIME


def register(ctx) -> None:
    """Register tools, the safety hook, /cameras, and the bundled skill. Opens no cameras."""
    global _RUNTIME
    try:
        data_dir = Path(ctx.state.data_dir)
    except Exception:
        data_dir = None

    def notify(text: str) -> None:
        fn = getattr(ctx, "inject_message", None)
        if not callable(fn):
            return
        try:
            fn(text)
        except Exception:
            logger.debug("camera alert stored; no session accepted it", exc_info=True)

    rt = Runtime(get_config=ctx.get_config, data_dir=data_dir, notify=notify)
    _RUNTIME = rt
    handlers = make_handlers(rt)
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="camera_watch",
            schema=schema,
            handler=handlers[schema["name"]],
            emoji="📷",
            description=schema["description"].split(". ")[0],
        )
    ctx.register_hook("pre_tool_call", rt.interlock.pre_tool_call)
    ctx.register_command(
        "cameras",
        make_command(rt),
        description="Cameras you configured: status, snap, watch, poll, stop, events",
        args_hint="[status|snap|watch|poll|stop|events|log]",
    )
    skill = Path(__file__).parent / "skills" / "camera-watch" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("camera-watch", skill)
    ctx.on_unload(rt.shutdown)
