"""Mock mode: scripted package arrival, a quiet room, and no sockets."""
import socket
import subprocess
import sys
from pathlib import Path

from hermes_camera_watch import scenes

ROOT = Path(__file__).resolve().parents[1]

PACKAGE = scenes.PACKAGE_EVENT


def test_package_scene_alerts_on_the_second_look(cam):
    first = cam.call("camera_snapshot", {"camera": "front-door"})
    assert first["ok"] and first["blocked"] is False
    assert first["network"] is False
    assert first["caption"] == scenes.CLEAR_CAPTION
    assert first["model_invoked"] is False
    assert first["media_tag"].startswith("MEDIA:")
    assert "vision API" in first["disclosure"]

    baseline = cam.call("camera_watch_poll", {"camera": "front-door"})
    assert baseline["baseline"] is True and baseline["event"] is None
    assert baseline["caption"] == scenes.CLEAR_CAPTION

    alert = cam.call("camera_watch_poll", {"camera": "front-door"})
    assert alert["reason"] == "scene_change"
    assert alert["event"]["caption"] == PACKAGE
    assert alert["event"]["caption_source"] == "mock_script"
    assert alert["event"]["model_invoked"] is False
    assert alert["event"]["media_tag"].startswith("MEDIA:")
    assert alert["score"] > 0.08

    quiet = cam.call("camera_watch_poll", {"camera": "front-door"})
    assert quiet["event"] is None and quiet["reason"] == "unchanged"

    events = cam.call("camera_events")
    assert len(events["events"]) == 1
    assert events["events"][0]["caption"] == PACKAGE


def test_quiet_room_never_alerts(cam):
    assert cam.call("camera_watch_poll", {"camera": "quiet-room"})["baseline"] is True
    second = cam.call("camera_watch_poll", {"camera": "quiet-room"})
    assert second["event"] is None and second["reason"] == "unchanged"
    assert second["caption"] == scenes.QUIET_CAPTION
    assert cam.call("camera_events", {"camera": "quiet-room"})["events"] == []


def test_snapshot_does_not_peek_ahead_of_the_watch(cam):
    cam.call("camera_watch_poll", {"camera": "front-door"})
    still = cam.call("camera_snapshot", {"camera": "front-door"})
    assert still["caption"] == scenes.CLEAR_CAPTION
    alert = cam.call("camera_watch_poll", {"camera": "front-door"})
    assert alert["event"]["caption"] == PACKAGE


def test_mock_mode_does_not_open_sockets_even_if_a_url_is_configured(make, monkeypatch):
    def no_net(*_args, **_kwargs):
        raise AssertionError("mock mode contacted the network")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    bot = make({
        "backend": "mock",
        "cameras": [{
            "name": "front-door",
            "transport": "rtsp",
            "scene": "package",
            "url": "rtsp://camera.example.com/door",
        }],
    })
    alert = None
    bot.call("camera_watch_poll", {"camera": "front-door"})
    alert = bot.call("camera_watch_poll", {"camera": "front-door"})
    assert alert["event"]["caption"] == PACKAGE
    assert alert["network"] is False
    listed = bot.call("camera_list")
    assert listed["cameras"][0]["contacted"] is False
    assert "not contacted" in " ".join(listed["warnings"]).lower() or "Mock mode" in " ".join(listed["warnings"])


def test_notify_fires_only_for_an_alert(make):
    notes = []
    bot = make(notify=notes.append)
    bot.call("camera_watch_poll", {"camera": "front-door"})
    assert notes == []
    bot.call("camera_watch_poll", {"camera": "front-door"})
    assert len(notes) == 1
    assert PACKAGE in notes[0] and "MEDIA:" in notes[0] and "vision API" in notes[0]


def test_handler_without_the_hook_refuses(cam):
    refused = cam.raw("camera_snapshot", {"camera": "front-door"})
    assert refused["ok"] is False and "interlock_not_applied" in refused["error"]


def test_background_watch_delivers_the_package(make):
    import time
    bot = make({"watch": {"min_interval_s": 1.0, "cooldown_s": 0}})
    try:
        started = bot.call("camera_watch_start", {"camera": "front-door", "interval_s": 1})
        assert started["baseline"] is True and started["watching"] is True
        found = None
        deadline = time.time() + 4
        while time.time() < deadline:
            rows = bot.call("camera_events")["events"]
            if rows:
                found = rows[0]
                break
            time.sleep(0.1)
        assert found is not None and found["caption"] == PACKAGE
    finally:
        bot.call("camera_watch_stop")


def test_offline_demo_prints_the_package_and_a_refusal(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "demo" / "mock_demo.py"), "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "A package has appeared at the door." in proc.stdout
    assert "BLOCKED" in proc.stdout
    assert list(tmp_path.glob("cam-*.png"))


def test_watch_start_stop_and_second_start(cam):
    # Keep the background poll from racing the explicit checks: it waits out the interval.
    started = cam.call("camera_watch_start", {"camera": "quiet-room"})
    assert started["ok"] and started["watching"] is True and started["baseline"] is True
    again = cam.call("camera_watch_start", {"camera": "quiet-room"})
    assert again["already_watching"] is True
    stopped = cam.call("camera_watch_stop", {"camera": "quiet-room"})
    assert stopped["ok"] and stopped["watching"] == []
    assert cam.call("camera_watch_stop")["ok"]
