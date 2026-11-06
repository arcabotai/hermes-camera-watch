"""Allowlist, argument refusals, TLS, and redaction. No camera is contacted."""
import json
import socket

SECRET = "example-secret-value"


def _no_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("a refused call still opened a socket")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def test_unknown_camera_and_url_arguments_are_blocked(cam, monkeypatch, tmp_path):
    _no_network(monkeypatch)
    missing = cam.call("camera_snapshot", {"camera": "garage"})
    assert missing["blocked"] and "camera_not_allowlisted" in missing["message"]
    foreign = cam.call("camera_snapshot", {
        "camera": "front-door",
        "url": f"http://user:{SECRET}@camera.example.com/snap.jpg",
    })
    assert foreign["blocked"] and "unexpected_argument" in foreign["message"]
    assert SECRET not in foreign["message"]
    scan = cam.call("camera_list", {"scan": True})
    assert scan["blocked"] and "unexpected_argument" in scan["message"]
    hostish = cam.call("camera_watch_start", {"camera": "http://camera.example.com/live"})
    assert hostish["blocked"] and "camera_not_allowlisted" in hostish["message"]
    log = (tmp_path / "blocked.jsonl").read_text(encoding="utf-8")
    assert SECRET not in log
    assert "unexpected_argument" in log


def test_interval_below_the_floor_is_blocked(cam):
    refused = cam.call("camera_watch_start", {"camera": "front-door", "interval_s": 0.1})
    assert refused["blocked"] and "interval_too_fast" in refused["message"]


def test_forged_interlock_token_is_blocked(cam):
    refused = cam.call("camera_snapshot", {"camera": "front-door", "_interlock": {"nonce": "nope"}})
    assert refused["blocked"] and "forged_interlock" in refused["message"]


def test_tls_bypass_is_refused_and_not_silently_ignored(make, monkeypatch):
    _no_network(monkeypatch)
    bot = make({"tls": {"ssl_verify": False}})
    refused = bot.call("camera_snapshot", {"camera": "front-door"})
    assert refused["blocked"] and "tls_bypass_refused" in refused["message"]
    status = bot.call("camera_status")
    assert any("TLS" in line or "tls" in line.lower() for line in status["warnings"])


def test_missing_ca_file_refuses_instead_of_skipping_verification(make, monkeypatch, tmp_path):
    _no_network(monkeypatch)
    bot = make({"tls": {"ca_file": str(tmp_path / "missing-ca.pem")}})
    refused = bot.call("camera_watch_poll", {"camera": "front-door"})
    assert refused["blocked"] and "tls_ca_missing" in refused["message"]


def test_camera_url_with_userinfo_is_redacted_in_status(make, tmp_path):
    bot = make({
        "backend": "live",
        "cameras": [{
            "name": "door",
            "transport": "snapshot",
            "url": f"https://user:{SECRET}@doorbell.example.com/snap.jpg",
        }],
    })
    listed = bot.call("camera_list")
    assert listed["ok"] and listed["cameras"][0]["url"] == "https://redacted@doorbell.example.com/snap.jpg"
    blob = json.dumps(listed) + (tmp_path / "status.json").read_text(encoding="utf-8")
    assert SECRET not in blob
    assert "user" not in listed["cameras"][0]["url"]


def test_over_cap_entries_are_not_accepted(make):
    cameras = [{"name": f"cam{i}", "transport": "mock", "scene": "quiet"} for i in range(10)]
    bot = make({"limits": {"max_cameras": 2}, "cameras": cameras})
    listed = bot.call("camera_list")
    assert [row["name"] for row in listed["cameras"]] == ["cam0", "cam1"]
    assert any(row["rule"] == "over_camera_cap" for row in listed["rejected"])
    refused = bot.call("camera_snapshot", {"camera": "cam2"})
    assert refused["blocked"] and "over_camera_cap" in refused["message"]


def test_unknown_backend_refuses_to_connect(make, monkeypatch):
    _no_network(monkeypatch)
    bot = make({"backend": "discover"})
    refused = bot.call("camera_snapshot", {"camera": "front-door"})
    assert refused["blocked"]
    assert "unknown_backend" in refused["message"] or "not on the allowlist" in refused["message"]


def test_bad_live_urls_are_rejected_without_connecting(make, monkeypatch):
    _no_network(monkeypatch)
    bot = make({
        "backend": "live",
        "cameras": [
            {"name": "door", "transport": "snapshot", "url": "rtsp://doorbell.example.com/live"},
            {"name": "filecam", "transport": "snapshot", "url": "file:///etc/passwd"},
            {"name": "wild", "transport": "snapshot", "url": "http://*.example.com/a.jpg"},
            {"name": "ok", "transport": "snapshot", "url": "https://doorbell.example.com/snap.jpg"},
        ],
    })
    listed = bot.call("camera_list")
    names = [row["name"] for row in listed["cameras"]]
    assert names == ["ok"]
    assert listed["cameras"][0]["encrypted"] is True
    refused = bot.call("camera_snapshot", {"camera": "door"})
    assert refused["blocked"]
