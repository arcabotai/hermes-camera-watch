"""Live HTTP snapshots against a local server. The allowlist is the only host that is dialed."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_camera_watch import imaging, scenes
from hermes_camera_watch.backend_live import build_ssl_context, rtsp_command

from conftest import Clock


def _png(package):
    rgb = scenes.porch(package)
    return imaging.encode_png(scenes.WIDTH, scenes.HEIGHT, rgb)


class _Server:
    def __init__(self):
        clear, package = _png(False), _png(True)
        state = {"hits": 0, "mode": "png", "n": 0, "clear": clear, "package": package}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                state["hits"] += 1
                path = self.path.split("?", 1)[0]
                if path == "/redir":
                    body = b""
                    code, ctype = 302, "text/plain"
                    extra = {"Location": "http://127.0.0.1:9/nope"}
                elif path == "/html":
                    body, code, ctype, extra = b"<html>no</html>", 200, "text/html", {}
                elif path == "/big":
                    body, code, ctype, extra = b"x" * 150_000, 200, "application/octet-stream", {}
                else:
                    frame = state["clear"] if state["n"] == 0 else state["package"]
                    state["n"] += 1
                    body, code, ctype, extra = frame, 200, "image/png", {}
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        self.state = state
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = _Server()
    try:
        yield srv
    finally:
        srv.close()


def _live(server, path, **watch):
    settings = {
        "backend": "live",
        "cameras": [{"name": "door", "transport": "snapshot", "url": server.url(path)}],
        "watch": {"min_interval_s": 1.0, "cooldown_s": 0, "motion_threshold": 0.05, **watch},
    }
    return settings


def test_snapshot_and_scene_change_against_a_real_http_server(make, server):
    clock = Clock()
    bot = make(_live(server, "/snap.png"), clock=clock)
    baseline = bot.call("camera_watch_poll", {"camera": "door"})
    assert baseline["ok"] and baseline["baseline"] is True and baseline["network"] is True
    assert baseline["transport_warning"] and "unencrypted" in baseline["transport_warning"].lower()
    clock.advance(1.1)
    alert = bot.call("camera_watch_poll", {"camera": "door"})
    assert alert["reason"] == "scene_change"
    assert alert["event"]["caption_source"] == "local_diff"
    assert alert["event"]["model_invoked"] is False
    assert "frame changed" in alert["event"]["caption"]
    assert server.state["hits"] == 2
    events = bot.call("camera_events")
    assert len(events["events"]) == 1


def test_rate_limit_does_not_open_a_second_connection(make, server):
    clock = Clock()
    bot = make(_live(server, "/snap.png"), clock=clock)
    assert bot.call("camera_snapshot", {"camera": "door"})["ok"]
    limited = bot.call("camera_snapshot", {"camera": "door"})
    assert limited["ok"] is False and limited["code"] == "rate_limited"
    assert server.state["hits"] == 1
    clock.advance(1.1)
    assert bot.call("camera_snapshot", {"camera": "door"})["ok"]
    assert server.state["hits"] == 2


def test_redirect_html_and_oversize_are_refused(make, server):
    clock = Clock()
    redir = make(_live(server, "/redir"), clock=clock)
    refused = redir.call("camera_snapshot", {"camera": "door"})
    assert refused["code"] == "redirect_refused"
    assert server.state["hits"] == 1

    clock2 = Clock()
    html = make(_live(server, "/html"), clock=clock2)
    assert html.call("camera_snapshot", {"camera": "door"})["code"] == "not_an_image"

    clock3 = Clock()
    big = make({**_live(server, "/big"), "fetch": {"max_bytes": 100000}}, clock=clock3)
    assert big.call("camera_snapshot", {"camera": "door"})["code"] == "too_large"


def test_undeclared_host_and_bypass_do_not_reach_the_server(make, server):
    bot = make(_live(server, "/snap.png"))
    blocked = bot.call("camera_snapshot", {"camera": "door", "host": "camera.example.com"})
    assert blocked["blocked"] and server.state["hits"] == 0
    insecure = make({
        "backend": "live",
        "cameras": [{
            "name": "door", "transport": "snapshot", "url": server.url("/snap.png"), "insecure": True,
        }],
    })
    refused = insecure.call("camera_snapshot", {"camera": "door"})
    assert refused["blocked"] and "tls_bypass_refused" in refused["message"]
    assert server.state["hits"] == 0


def test_rtsp_command_forces_tls_verification_and_limits_protocols():
    import ssl
    context = build_ssl_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    plain = rtsp_command("ffmpeg", "rtsp://doorbell.example.com/door", "/tmp/x.png", 8, None, "rtsp")
    secure = rtsp_command("ffmpeg", "rtsps://doorbell.example.com/door", "/tmp/x.png", 8, "/tmp/ca.pem", "rtsps")
    assert plain[plain.index("-i") + 1] == "rtsp://doorbell.example.com/door"
    assert "file" not in plain[plain.index("-protocol_whitelist") + 1].split(",")
    assert "-tls_verify" not in plain
    assert secure[secure.index("-tls_verify") + 1] == "1"
    assert secure[secure.index("-ca_file") + 1] == "/tmp/ca.pem"
    joined = " ".join(secure)
    assert "tls_verify 0" not in joined and "shell" not in joined


def test_jpeg_gray_uses_ffmpeg_when_present(tmp_path):
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    png = tmp_path / "frame.png"
    jpg = tmp_path / "frame.jpg"
    png.write_bytes(_png(True))
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(png), str(jpg)], check=True, capture_output=True)
    from hermes_camera_watch.backend_live import _jpeg_gray
    gray, note = _jpeg_gray(jpg.read_bytes(), "")
    assert note is None and len(gray) == imaging.GRAY_W * imaging.GRAY_H
