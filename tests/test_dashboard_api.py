"""Dashboard backend reads what the plugin stored. It does not open a camera."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import ROOT, Harness  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = home / "plugin-data" / "agent-plugin-hermes-camera-watch-abc"
    data.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    spec = importlib.util.spec_from_file_location("hcw_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_hermes_home", lambda: home)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/hermes-camera-watch")
    return TestClient(app), data


def test_empty_state_is_valid(api):
    client, _data = api
    body = client.get("/api/plugins/hermes-camera-watch/state").json()
    assert body["ok"] and body["events"] == [] and body["snapshot"] is None
    assert body["disclosures"] and "terminal" in body["disclosures"][-1].lower()
    assert any("vision" in line.lower() for line in body["disclosures"])


def test_dashboard_shows_the_package_alert(api):
    client, data = api
    bot = Harness(data)
    assert bot.call("camera_snapshot", {"camera": "front-door"})["ok"]
    assert bot.call("camera_watch_poll", {"camera": "front-door"})["baseline"]
    alert = bot.call("camera_watch_poll", {"camera": "front-door"})
    assert alert["event"]["caption"].startswith("A package")
    bot.call("camera_snapshot", {"camera": "front-door", "url": "http://camera.example.com/x"})
    body = client.get("/api/plugins/hermes-camera-watch/state").json()
    assert body["backend"] == "mock"
    assert body["events"][0]["caption"].startswith("A package")
    assert body["events"][0]["model_invoked"] is False
    assert "path" not in body["events"][0]
    assert body["snapshot"]["data_url"].startswith("data:image/png;base64,")
    assert any(row["rule"] == "unexpected_argument" for row in body["blocked"])
    assert any(row["name"] == "front-door" and row["watching"] is False for row in body["cameras"])
    again = client.get("/api/plugins/hermes-camera-watch/state", params={"since": body["snapshot"]["mtime"]}).json()
    assert "data_url" not in again["snapshot"]


def test_garbage_files_do_not_break_the_endpoint(api):
    client, data = api
    (data / "events.jsonl").write_text('not-json\n{"camera": "front-door", "caption": "kept"}\n', encoding="utf-8")
    (data / "blocked.jsonl").write_text("{broken\n", encoding="utf-8")
    (data / "status.json").write_text("[]", encoding="utf-8")
    body = client.get("/api/plugins/hermes-camera-watch/state").json()
    assert body["ok"] and body["events"][0]["caption"] == "kept"


def test_manifest_name_matches_the_route():
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text())
    assert manifest["name"] == "hermes-camera-watch"
    assert (ROOT / "dashboard" / manifest["api"]).is_file()
