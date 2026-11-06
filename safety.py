"""Safety interlock for camera tools, enforced from a ``pre_tool_call`` hook.

The hook runs before the handler. It allows a camera only when that name is on the configured
allowlist, and it refuses arguments that would scan a network, pass a URL, or skip TLS.
Handlers of grabbing tools also require the nonce the hook stamps, so a skipped hook cannot
open a camera. Stopping a watch is always allowed: stopping must not depend on the network.

What this does not do: it does not constrain the terminal, code execution, or edits to
config.yaml. Those can still reach a camera or change these limits.
"""
from __future__ import annotations

import json
import logging
import math
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from . import cameras
from .cameras import PolicyError
from .config import default_interval_s, max_watches, min_interval_s

logger = logging.getLogger("hermes_camera_watch.interlock")

READ_TOOLS = {"camera_list", "camera_status", "camera_events"}
GRAB_TOOLS = {"camera_snapshot", "camera_watch_start", "camera_watch_poll"}
STOP_TOOLS = {"camera_watch_stop"}
ALL_TOOLS = READ_TOOLS | GRAB_TOOLS | STOP_TOOLS

ALLOWED_ARGS = {
    "camera_list": set(),
    "camera_status": set(),
    "camera_snapshot": {"camera"},
    "camera_watch_start": {"camera", "interval_s"},
    "camera_watch_stop": {"camera"},
    "camera_watch_poll": {"camera"},
    "camera_events": {"camera", "limit"},
}


class Blocked(Exception):
    def __init__(self, rule: str, reason: str):
        super().__init__(reason)
        self.rule = rule
        self.reason = reason


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise Blocked("invalid_args", f"{what} must be a number")
    try:
        number = float(value)
    except ValueError:
        raise Blocked("invalid_args", f"{what} must be a number") from None
    if not math.isfinite(number):
        raise Blocked("invalid_args", f"{what} must be finite")
    return number


class Interlock:
    def __init__(self, settings_fn: Callable[[], Dict[str, Any]],
                 data_dir_fn: Callable[[], Optional[Path]] = lambda: None,
                 active_watches_fn: Callable[[], List[str]] = lambda: []):
        self._settings_fn = settings_fn
        self._data_dir_fn = data_dir_fn
        self._active_watches_fn = active_watches_fn
        self._lock = threading.RLock()
        self.nonce = secrets.token_hex(16)
        self.blocked: Deque[Dict[str, Any]] = deque(maxlen=200)
        self.counters = {"allowed": 0, "blocked": 0, "modified": 0}

    def record_block(self, tool: str, args: Dict[str, Any], rule: str, reason: str) -> Dict[str, Any]:
        safe_args = _public_args(args)
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "rule": rule,
            "reason": reason,
            "args": safe_args,
        }
        with self._lock:
            self.blocked.append(entry)
            self.counters["blocked"] += 1
        logger.warning("BLOCKED %s [%s]: %s | args=%s", tool, rule, reason, json.dumps(safe_args, default=str)[:300])
        directory = self._data_dir_fn()
        if directory:
            try:
                path = Path(directory) / "blocked.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry) + "\n")
                if path.stat().st_size > 1_000_000:
                    lines = path.read_text(encoding="utf-8").splitlines()[-300:]
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass
        return entry

    def recent_blocked(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.blocked)[-limit:]

    def evaluate(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        settings = self._settings_fn()
        unexpected = [str(key) for key in args if not str(key).startswith("_") and str(key) not in ALLOWED_ARGS[tool]]
        if any(str(key).startswith("_") for key in args):
            raise Blocked("forged_interlock", "internal arguments are not accepted")
        if unexpected:
            raise Blocked(
                "unexpected_argument",
                f"argument {unexpected[0]!r} is not accepted. Choose a camera by the name in your settings. "
                "This plugin does not take a URL, a host, or a scan option.",
            )
        patch: Dict[str, Any] = {}
        notes: List[str] = []
        camera_name = args.get("camera") if "camera" in ALLOWED_ARGS[tool] else None
        if camera_name is not None and camera_name != "":
            resolved = cameras.resolve(settings, camera_name)
            patch["camera"] = resolved["name"]
        elif tool in GRAB_TOOLS or (tool == "camera_watch_stop" and "camera" in args and args.get("camera") == ""):
            raise Blocked("camera_required", "name the camera from camera_list")
        elif tool in GRAB_TOOLS:
            raise Blocked("camera_required", "name the camera from camera_list")

        if tool == "camera_watch_start":
            name = patch.get("camera")
            active = list(self._active_watches_fn() or [])
            if name not in active and len(active) >= max_watches(settings):
                raise Blocked(
                    "too_many_watches",
                    f"already watching {len(active)} camera(s); the maximum is {max_watches(settings)}. Stop one first.",
                )
            floor = min_interval_s(settings)
            if "interval_s" in args and args.get("interval_s") is not None:
                interval = _number(args.get("interval_s"), "interval_s")
                if interval < floor:
                    raise Blocked("interval_too_fast", f"interval {interval:.1f}s is below the minimum of {floor:.0f}s")
                if interval > 3600:
                    raise Blocked("invalid_args", "interval_s must be at most 3600")
            else:
                interval = default_interval_s(settings)
                notes.append(f"interval defaulted to {interval:.0f}s")
            patch["interval_s"] = interval

        if tool == "camera_events" and args.get("limit") is not None:
            limit = _number(args.get("limit"), "limit")
            if limit < 1 or limit > 100 or int(limit) != limit:
                raise Blocked("invalid_args", "limit must be a whole number from 1 to 100")
            patch["limit"] = int(limit)

        return {"patch": patch, "notes": notes, "camera": patch.get("camera")}

    def pre_tool_call(self, tool_name: str = "", args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Hermes ``pre_tool_call`` callback. Fails closed for camera tools. Other tools are untouched."""
        del kwargs
        if tool_name not in ALL_TOOLS:
            return None
        original = dict(args or {}) if isinstance(args, dict) else {}
        try:
            result = self.evaluate(tool_name, original)
        except Blocked as blocked:
            self.record_block(tool_name, original, blocked.rule, blocked.reason)
            return {"action": "block", "message": _message(tool_name, blocked.rule, blocked.reason)}
        except PolicyError as blocked:
            self.record_block(tool_name, original, blocked.rule, blocked.reason)
            return {"action": "block", "message": _message(tool_name, blocked.rule, blocked.reason)}
        except Exception as exc:
            logger.exception("interlock error")
            self.record_block(tool_name, original, "interlock_error", repr(exc))
            return {"action": "block", "message": _message(tool_name, "interlock_error", f"{exc!r} (failing closed)")}
        stamped = dict(result["patch"])
        if tool_name in GRAB_TOOLS:
            stamped["_interlock"] = {"nonce": self.nonce, "camera": result.get("camera"), "notes": result["notes"]}
        with self._lock:
            self.counters["modified" if result["notes"] or tool_name in GRAB_TOOLS else "allowed"] += 1
        if not stamped:
            return None
        return {"action": "modify", "args": stamped}

    def verify(self, args: Dict[str, Any], camera: Any) -> List[str]:
        token = args.get("_interlock") if isinstance(args, dict) else None
        if not isinstance(token, dict) or token.get("nonce") != self.nonce or token.get("camera") != camera:
            raise Blocked(
                "interlock_not_applied",
                "this call was not vetted by the camera-watch safety interlock (pre_tool_call did not run); refusing to touch a camera",
            )
        return list(token.get("notes") or [])


def _message(tool: str, rule: str, reason: str) -> str:
    return f"hermes-camera-watch interlock BLOCKED {tool} [{rule}]: {reason}"


def _public_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, str) and ("://" in value or "@" in value):
            out[str(key)] = cameras.redact_url(value) if "://" in value else "[redacted]"
        elif isinstance(value, str) and len(value) > 120:
            out[str(key)] = value[:120] + "…"
        else:
            out[str(key)] = value
    return out
