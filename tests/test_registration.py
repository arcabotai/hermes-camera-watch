"""Registration contract: the manifest matches what register() actually registers."""
import inspect
import socket
from pathlib import Path

import yaml

import hermes_camera_watch
from hermes_camera_watch import config, schemas

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = yaml.safe_load((ROOT / "plugin.yaml").read_text())


class FakeState:
    def __init__(self, path):
        self.data_dir = path


class FakeCtx:
    def __init__(self, tmp_path, cfg=None):
        self.tools, self.hooks, self.commands, self.skills, self.unload = {}, {}, {}, {}, []
        self.state = FakeState(tmp_path)
        self._cfg = cfg or {}
        self.injected = []

    def get_config(self, key, default=None):
        return self._cfg.get(key, default)

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_hook(self, name, fn, **kw):
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, handler, **kw):
        self.commands[name] = (handler, kw)

    def register_skill(self, name, path, description=""):
        self.skills[name] = Path(path)

    def on_unload(self, fn):
        self.unload.append(fn)

    def inject_message(self, content, role="user", **kwargs):
        self.injected.append(content)
        return True


def test_registered_tools_match_manifest(tmp_path):
    ctx = FakeCtx(tmp_path)
    hermes_camera_watch.register(ctx)
    assert list(ctx.tools) == MANIFEST["provides_tools"]
    assert len(ctx.tools) == 7


def test_every_tool_has_schema_handler_and_kwargs(tmp_path):
    ctx = FakeCtx(tmp_path)
    hermes_camera_watch.register(ctx)
    for name, tool in ctx.tools.items():
        assert tool["toolset"] == "camera_watch" and callable(tool["handler"])
        schema = tool["schema"]
        assert schema["name"] == name and len(schema["description"]) > 40
        assert schema["parameters"]["type"] == "object"
        signature = inspect.signature(tool["handler"])
        assert any(param.kind is param.VAR_KEYWORD for param in signature.parameters.values())


def test_required_params_exist_in_properties():
    for schema in schemas.ALL:
        props = schema["parameters"].get("properties", {})
        for required in schema["parameters"].get("required", []):
            assert required in props


def test_hook_ignores_other_tools_and_blocks_a_foreign_host(tmp_path):
    ctx = FakeCtx(tmp_path)
    hermes_camera_watch.register(ctx)
    assert MANIFEST["provides_hooks"] == ["pre_tool_call"]
    hook = ctx.hooks["pre_tool_call"][0]
    assert any(param.kind is param.VAR_KEYWORD for param in inspect.signature(hook).parameters.values())
    assert hook(tool_name="web_search", args={"query": "porch"}, task_id="t") is None
    blocked = hook(tool_name="camera_snapshot", args={"camera": "front-door", "url": "http://camera.example.com/a.jpg"})
    assert blocked["action"] == "block" and "unexpected_argument" in blocked["message"]


def test_skill_and_command_registered(tmp_path):
    ctx = FakeCtx(tmp_path)
    hermes_camera_watch.register(ctx)
    assert ctx.skills["camera-watch"].read_text().startswith("---")
    assert "cameras" in ctx.commands
    text = ctx.commands["cameras"][0]("status")
    assert "front-door" in text and "Mock mode" in text


def test_register_opens_no_connections(tmp_path, monkeypatch):
    def no_net(*_args, **_kwargs):
        raise AssertionError("register() must not touch the network")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    hermes_camera_watch.register(FakeCtx(tmp_path, {
        "backend": "live",
        "cameras": [{"name": "door", "transport": "snapshot", "url": "http://camera.example.com/snap.jpg"}],
    }))


def test_manifest_basics():
    assert MANIFEST["name"] == "hermes-camera-watch" and MANIFEST["manifest_version"] == 2
    assert MANIFEST["license"] == "MIT" and (ROOT / "LICENSE").exists()
    assert str(MANIFEST["requires_hermes"]).startswith(">=")
    for dep in MANIFEST.get("python_dependencies") or []:
        assert "<" in dep


def test_config_schema_defaults_equal_runtime_defaults():
    schema = MANIFEST["config_schema"]
    assert set(schema) == set(config.DEFAULTS)
    for key, spec in schema.items():
        assert spec["default"] == config.DEFAULTS[key], key


def test_user_config_merges_and_bad_config_falls_back(tmp_path):
    from hermes_camera_watch.runtime import Runtime
    rt = Runtime(lambda key, default=None: {"watch": {"min_interval_s": 3}}.get(key, default), tmp_path)
    snap = rt.settings.snapshot()
    assert snap["watch"]["min_interval_s"] == 3 and snap["watch"]["interval_s"] == 5.0
    assert snap["backend"] == "mock"
    broken = Runtime(lambda key, default=None: (_ for _ in ()).throw(RuntimeError("bad")), tmp_path)
    assert broken.settings.snapshot()["backend"] == "mock"


def test_readme_disclosures_and_no_agent_harness_name():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "terminal" in text.lower()
    assert "third-party" in text.lower()
    assert "Grok" not in text
    assert "felirami" in text
    assert text.rstrip().endswith("https://oss.arcabot.ai")
    demo = (ROOT / "demo-script.md").read_text(encoding="utf-8")
    assert "Grok" not in demo
    assert "package" in demo.lower()


def test_runtime_source_does_not_disable_tls_or_add_hermes_env():
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "tests"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        assert "CERT_NONE" not in source
        assert "shell=True" not in source
        assert "_create_unverified_context" not in source
        assert "HERMES_CAMERA" not in source
        if path.name != "plugin_api.py":
            assert "os.environ" not in source
        if path.name != "backend_live.py":
            assert "subprocess" not in source


def test_dashboard_manifest_points_at_real_files():
    import json
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text())
    for key in ("entry", "css", "api"):
        assert (ROOT / "dashboard" / manifest[key]).is_file()
    assert manifest["name"] == "hermes-camera-watch" and manifest["tab"]["path"].startswith("/")


def test_slash_command_reports_a_refusal(tmp_path):
    ctx = FakeCtx(tmp_path)
    hermes_camera_watch.register(ctx)
    handler, _meta = ctx.commands["cameras"]
    refused = handler("snap http://camera.example.com/x")
    assert "BLOCKED" in refused
    assert "camera_not_allowlisted" in handler("log")
    assert "unknown subcommand" in handler("frobnicate")
