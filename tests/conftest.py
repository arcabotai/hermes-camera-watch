"""Load the repo root as the importable package ``hermes_camera_watch``."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if "hermes_camera_watch" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "hermes_camera_watch", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hermes_camera_watch"] = mod
    spec.loader.exec_module(mod)

from hermes_camera_watch.runtime import Runtime  # noqa: E402
from hermes_camera_watch.tools import make_handlers  # noqa: E402


class Clock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Harness:
    """Runs a tool the way Hermes does: the pre_tool_call hook, then the handler."""

    def __init__(self, tmp_path, overrides=None, clock=None, notify=None):
        self.rt = Runtime(None, tmp_path, overrides, clock=clock, notify=notify)
        self.handlers = make_handlers(self.rt)

    def call(self, tool, args=None):
        args = dict(args or {})
        directive = self.rt.interlock.pre_tool_call(tool_name=tool, args=args)
        if directive and directive.get("action") == "block":
            return {"blocked": True, "ok": False, "message": directive["message"]}
        if directive and directive.get("action") == "modify":
            args.update(directive["args"])
        res = json.loads(self.handlers[tool](args))
        res.setdefault("blocked", False)
        return res

    def raw(self, tool, args=None):
        return json.loads(self.handlers[tool](dict(args or {})))


@pytest.fixture
def make(tmp_path):
    def _make(overrides=None, clock=None, notify=None):
        return Harness(tmp_path, overrides, clock=clock, notify=notify)
    return _make


@pytest.fixture
def cam(make):
    return make()
