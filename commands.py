"""``/cameras`` slash command. Slash commands are typed by a person, not the model."""
from __future__ import annotations

import json
from typing import Callable

from .runtime import Runtime
from .tools import make_handlers

HELP = """hermes-camera-watch — cameras you configured
  /cameras status              allowlist, watches, disclosures
  /cameras snap <name>         grab one still
  /cameras watch <name>        start a watch (first check is a baseline)
  /cameras poll <name>         check once now
  /cameras stop [name]         stop one watch, or all of them
  /cameras events [name]       recent alerts
  /cameras log [n]             recent safety refusals
Stopping a watch does not open a camera. Adding a camera is a settings change, not a command."""


def make_command(rt: Runtime) -> Callable[[str], str]:
    handlers = make_handlers(rt)

    def call(tool: str, args: dict) -> dict:
        directive = rt.interlock.pre_tool_call(tool_name=tool, args=args)
        if directive and directive.get("action") == "block":
            return {"ok": False, "blocked": True, "error": directive["message"]}
        if directive and directive.get("action") == "modify":
            args.update(directive["args"])
        raw = handlers[tool](args)
        try:
            return json.loads(raw)
        except ValueError:
            return {"ok": False, "error": "unreadable tool result"}

    def handle(raw_args: str = "") -> str:
        parts = (raw_args or "").strip().split()
        sub = parts[0].lower() if parts else "status"
        rest = parts[1:]
        if sub in ("help", "-h", "--help"):
            return HELP
        if sub == "status":
            res = call("camera_status", {})
            if not res.get("ok"):
                return res.get("error", "status failed")
            lines = [f"backend: {res['backend']}", f"watching: {[w['camera'] for w in res['watches']] or 'none'}"]
            for camera in res["cameras"]:
                lines.append(
                    f"  {camera['name']}  mode={camera['mode']}  scene={camera.get('scene') or '-'}  "
                    f"watching={camera.get('watching')}  url={camera.get('url') or '-'}"
                )
            if res["rejected"]:
                lines.append("rejected: " + "; ".join(f"{row['name'] or '?'}: {row['reason']}" for row in res["rejected"]))
            lines.extend(res["warnings"])
            lines.append(res["disclosures"][-1])
            return "\n".join(lines)
        if sub == "snap" and rest:
            res = call("camera_snapshot", {"camera": rest[0]})
            if not res.get("ok"):
                return res.get("error", "snapshot failed")
            return f"{res.get('caption')}\n{res.get('media_tag', '')}\n{res.get('disclosure', '')}"
        if sub == "watch" and rest:
            res = call("camera_watch_start", {"camera": rest[0]})
            if not res.get("ok", True) and res.get("error"):
                return res["error"]
            if res.get("blocked"):
                return res.get("error", "blocked")
            return f"watching {rest[0]} (baseline recorded={bool(res.get('baseline'))}). {res.get('caption', '')}"
        if sub == "poll" and rest:
            res = call("camera_watch_poll", {"camera": rest[0]})
            if not res.get("ok"):
                return res.get("error", "poll failed")
            if res.get("event"):
                return f"{res['event']['caption']}\n{res['event']['media_tag']}\n{res['disclosure']}"
            return f"{res.get('reason')}: {res.get('caption') or 'no change'}"
        if sub == "stop":
            args = {"camera": rest[0]} if rest else {}
            res = call("camera_watch_stop", args)
            if not res.get("ok"):
                return res.get("error", "stop failed")
            return f"stopped {res.get('stopped')}. still watching: {res.get('watching') or 'none'}"
        if sub == "events":
            args = {"camera": rest[0]} if rest else {}
            res = call("camera_events", args)
            if not res.get("ok"):
                return res.get("error", "events failed")
            if not res["events"]:
                return "No alerts yet. The first check of a camera only records a baseline."
            return "\n".join(f"{row['iso']}  {row['camera']}  {row['caption']}" for row in res["events"])
        if sub == "log":
            try:
                limit = int(rest[0]) if rest else 10
            except ValueError:
                limit = 10
            rows = rt.interlock.recent_blocked(limit)
            if not rows:
                return "No blocked calls recorded this session."
            return "\n".join(f"{row['iso']}  {row['tool']}  [{row['rule']}]  {row['reason']}" for row in rows)
        return f"unknown subcommand {sub!r}\n{HELP}"

    return handle
