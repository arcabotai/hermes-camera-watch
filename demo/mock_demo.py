#!/usr/bin/env python3
"""Offline demo: no camera, no network, no API key.

Plays the scripted doorway (clear, then a package) through the real tool handlers and the
safety interlock, then shows the interlock refusing a URL and an unknown camera.

    python3 demo/mock_demo.py [--out demo-out]
"""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "hermes_camera_watch", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
)
pkg = importlib.util.module_from_spec(spec)
sys.modules["hermes_camera_watch"] = pkg
spec.loader.exec_module(pkg)

from hermes_camera_watch.runtime import Runtime  # noqa: E402
from hermes_camera_watch.tools import make_handlers  # noqa: E402

BOLD, DIM, RED, GREEN, END = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="demo-out")
    out = Path(parser.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    rt = Runtime(
        None,
        Path(tempfile.mkdtemp(prefix="hermes-camera-watch-")),
        {"storage": {"output_dir": str(out)}, "watch": {"cooldown_s": 0}},
    )
    handlers = make_handlers(rt)
    saw_package = False

    def call(tool, **args):
        shown = ", ".join(f"{key}={value}" for key, value in args.items())
        print(f"{BOLD}> {tool}({shown}){END}")
        directive = rt.interlock.pre_tool_call(tool_name=tool, args=args)
        if directive and directive.get("action") == "block":
            print(f"  {RED}{directive['message']}{END}")
            return None
        if directive and directive.get("action") == "modify":
            args = {**args, **directive["args"]}
        result = json.loads(handlers[tool](args))
        return result

    def say(text):
        print(f"{DIM}{text}{END}")

    say("== hermes-camera-watch mock demo: front-door is a scripted porch; quiet-room never changes ==")
    say("-- status, then a still of the doorway")
    status = call("camera_status")
    print(f"  backend={status['backend']}  cameras={[row['name'] for row in status['cameras']]}")
    still = call("camera_snapshot", camera="front-door")
    print(f"  {GREEN}{still['caption']}{END}  {still['media_tag']}")

    say("-- quiet-room: baseline, then another look. Nothing happens.")
    call("camera_watch_poll", camera="quiet-room")
    quiet = call("camera_watch_poll", camera="quiet-room")
    print(f"  {GREEN}{quiet['reason']}: {quiet['caption']}{END}")

    say("-- front-door: the first check is only a baseline")
    base = call("camera_watch_poll", camera="front-door")
    print(f"  {base['reason']}: {base['caption']}")
    say("-- the next check: a package has arrived")
    alert = call("camera_watch_poll", camera="front-door")
    if alert and alert.get("event"):
        saw_package = alert["event"]["caption"] == "A package has appeared at the door."
        print(f"  {GREEN}{alert['event']['caption']}{END}  {alert['event']['media_tag']}")
        print(f"  caption_source={alert['event']['caption_source']}  model_invoked={alert['event']['model_invoked']}")
    else:
        print(f"  {RED}expected a package alert, got {alert}{END}")

    say("-- the user asks for a camera that is not on the allowlist, then passes a raw URL")
    call("camera_watch_start", camera="http://camera.example.com/live")
    call("camera_snapshot", camera="front-door", url="http://camera.example.com/snap.jpg")
    say("-- stop is always allowed")
    call("camera_watch_stop")
    say("-- blocked-call log (arguments that contained a URL are redacted)")
    for entry in rt.interlock.recent_blocked(10):
        print(f"  {entry['tool']}  [{entry['rule']}]  {entry['reason'][:110]}")
    print(f"\nStills in {out}/")
    return 0 if saw_package else 1


if __name__ == "__main__":
    raise SystemExit(main())
