"""Tool handlers. Every handler returns a JSON string and never raises."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict

from .cameras import CameraError, PolicyError
from .runtime import Runtime
from .safety import Blocked


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, default=str)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, default=str)


def make_handlers(rt: Runtime) -> Dict[str, Callable[..., str]]:
    def guarded(fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Callable[..., str]:
        def handler(args: Dict[str, Any], **kwargs: Any) -> str:
            del kwargs
            args = args if isinstance(args, dict) else {}
            try:
                return json.dumps(fn(args), default=str)
            except Blocked as blocked:
                rt.interlock.record_block(fn.__name__, args, blocked.rule, blocked.reason)
                return _err(f"hermes-camera-watch interlock BLOCKED [{blocked.rule}]: {blocked.reason}",
                            code=blocked.rule, blocked=True)
            except PolicyError as blocked:
                rt.interlock.record_block(fn.__name__, args, blocked.rule, blocked.reason)
                return _err(f"hermes-camera-watch interlock BLOCKED [{blocked.rule}]: {blocked.reason}",
                            code=blocked.rule, blocked=True)
            except CameraError as exc:
                return _err(exc.message, code=exc.code)
            except Exception as exc:
                return _err(f"{type(exc).__name__}: {exc}")
        handler.__name__ = fn.__name__
        return handler

    def camera_list(_args: Dict[str, Any]) -> Dict[str, Any]:
        state = rt.public_state()
        rt._write_status()
        return {
            "ok": True,
            "backend": state["backend"],
            "cameras": state["cameras"],
            "rejected": state["rejected"],
            "warnings": state["warnings"],
        }

    def camera_status(_args: Dict[str, Any]) -> Dict[str, Any]:
        state = rt.public_state()
        rt._write_status()
        state["ok"] = True
        return state

    def camera_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
        notes = rt.interlock.verify(args, args.get("camera"))
        return rt.snapshot(str(args.get("camera")), notes)

    def camera_watch_start(args: Dict[str, Any]) -> Dict[str, Any]:
        notes = rt.interlock.verify(args, args.get("camera"))
        return rt.start_watch(str(args.get("camera")), float(args["interval_s"]), notes)

    def camera_watch_stop(args: Dict[str, Any]) -> Dict[str, Any]:
        name = args.get("camera") or None
        stopped = rt.stop_watch(str(name) if name else None)
        return {"ok": True, **stopped}

    def camera_watch_poll(args: Dict[str, Any]) -> Dict[str, Any]:
        notes = rt.interlock.verify(args, args.get("camera"))
        return rt.poll_camera(str(args.get("camera")), notes)

    def camera_events(args: Dict[str, Any]) -> Dict[str, Any]:
        name = args.get("camera") or None
        limit = int(args.get("limit") or 20)
        return {
            "ok": True,
            "events": rt.recent_events(str(name) if name else None, limit),
            "disclosure": "The still is stored only on this machine. This plugin did not send it to a model or to any vision API.",
        }

    fns = [camera_list, camera_status, camera_snapshot, camera_watch_start, camera_watch_stop, camera_watch_poll, camera_events]
    return {fn.__name__: guarded(fn) for fn in fns}
