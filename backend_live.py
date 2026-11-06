"""Live stills from a camera the user configured. HTTP(S) uses the standard library. RTSP uses ffmpeg.

No redirect is followed. TLS verification is on. ffmpeg is invoked with a protocol allowlist and
without a shell. Plain RTSP is unencrypted; rtsps forces certificate verification because ffmpeg's
own default is to skip it.
"""
from __future__ import annotations

import base64
import http.client
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlsplit

from . import imaging
from .cameras import CameraError, redact_text
from .config import max_fetch_bytes, timeout_s

USER_AGENT = "hermes-camera-watch/0.1"
_RTSP_PROTOCOLS = {
    "rtsp": "rtsp,rtp,udp,tcp",
    "rtsps": "rtsp,rtp,udp,tcp,tls,http,https",
}


def build_ssl_context(ca_file: Optional[str]) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    return context


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def rtsp_command(ffmpeg: str, url: str, dest: Path, timeout: float, ca_file: Optional[str], scheme: str) -> list:
    protocols = _RTSP_PROTOCOLS[scheme]
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", protocols,
        "-rw_timeout", str(int(timeout * 1_000_000)),
        "-rtsp_transport", "tcp",
    ]
    if scheme == "rtsps":
        # ffmpeg leaves TLS verification off unless this is set. Never pass a zero here.
        command += ["-tls_verify", "1"]
        if ca_file:
            command += ["-ca_file", ca_file]
    command += ["-i", url, "-frames:v", "1", "-an", str(dest)]
    return command


def jpeg_gray_command(ffmpeg: str) -> list:
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "pipe",
        "-f", "image2pipe", "-i", "pipe:0",
        "-frames:v", "1", "-an",
        "-vf", f"scale={imaging.GRAY_W}:{imaging.GRAY_H}",
        "-f", "image2pipe", "-vcodec", "pgm", "pipe:1",
    ]


def fetch_http(camera: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """GET one still. Redirects are not followed; TLS uses a verifying context."""
    url = camera["url"]
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path = path + "?" + parts.query
    timeout = timeout_s(settings)
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=build_ssl_context(camera.get("ca_file")))
    else:
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    headers = {"User-Agent": USER_AGENT, "Connection": "close"}
    if parts.username is not None or parts.password is not None:
        token = base64.b64encode(f"{unquote(parts.username or '')}:{unquote(parts.password or '')}".encode()).decode("ascii")
        headers["Authorization"] = "Basic " + token
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = response.status
        if 300 <= status < 400:
            response.read(1024)
            raise CameraError(
                "redirect_refused",
                "the camera returned a redirect. Redirects are not followed; put the final snapshot URL in settings.",
            )
        if status != 200:
            response.read(1024)
            raise CameraError("http_status", f"the camera returned HTTP {status}")
        data = _read_limited(response, max_fetch_bytes(settings))
    except CameraError:
        raise
    except (ssl.SSLCertVerificationError, ssl.SSLError) as exc:
        reason = redact_text(str(exc), url)
        raise CameraError("tls_failed", f"TLS verification failed for {camera.get('redacted_url')}: {reason}") from None
    except (TimeoutError, socket.timeout):
        raise CameraError("timeout", f"timed out contacting {camera.get('redacted_url') or 'the camera'}") from None
    except OSError as exc:
        reason = redact_text(str(exc), url)
        raise CameraError("unreachable", f"could not connect to {camera.get('redacted_url')}: {reason}") from None
    finally:
        conn.close()
    try:
        mime, ext = imaging.sniff(data)
    except ValueError:
        raise CameraError("not_an_image", "the URL did not return a PNG or JPEG still") from None
    width = height = None
    gray = None
    note = None
    if ext == "png":
        try:
            width, height, rgb = imaging.decode_png(data)
            gray = imaging.to_gray_small(rgb, width, height)
        except ValueError as exc:
            raise CameraError("bad_image", f"the PNG could not be decoded ({exc})") from None
    else:
        size = imaging.jpeg_size(data)
        if size:
            width, height = size
        gray, note = _jpeg_gray(data, url)
    return _frame(data, mime, ext, width, height, gray, camera, note)


def fetch_rtsp(camera: Dict[str, Any], settings: Dict[str, Any], work_dir: Path) -> Dict[str, Any]:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise CameraError(
            "ffmpeg_missing",
            "ffmpeg is not on PATH, so this RTSP camera was not contacted. Install ffmpeg, or use an HTTP PNG snapshot URL.",
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_dest = tempfile.mkstemp(prefix=".grab-", suffix=".png", dir=work_dir)
    os.close(fd)
    dest = Path(raw_dest)
    command = rtsp_command(
        ffmpeg, camera["url"], dest, timeout_s(settings), camera.get("ca_file"), camera["scheme"],
    )
    try:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_s(settings) + 2,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise CameraError("timeout", f"timed out reading {camera.get('redacted_url')}") from None
        except OSError as exc:
            raise CameraError("ffmpeg_failed", f"could not run ffmpeg ({exc.__class__.__name__})") from None
        if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 32:
            detail = redact_text((proc.stderr or b"").decode("utf-8", "replace"), camera["url"])
            raise CameraError("ffmpeg_failed", f"ffmpeg could not read the stream. {detail}".strip())
        data = dest.read_bytes()
    finally:
        dest.unlink(missing_ok=True)
    try:
        mime, ext = imaging.sniff(data)
        width, height, rgb = imaging.decode_png(data)
        gray = imaging.to_gray_small(rgb, width, height)
    except ValueError as exc:
        raise CameraError("bad_image", f"the grabbed frame was not a usable image ({exc})") from None
    return _frame(data, mime, ext, width, height, gray, camera, None)


def _read_limited(response: http.client.HTTPResponse, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        block = response.read(65536)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise CameraError("too_large", f"the snapshot is larger than {limit} bytes; refusing to keep it")
        chunks.append(block)
    return b"".join(chunks)


def _jpeg_gray(data: bytes, url: str):
    del url
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return None, "JPEG was saved, but ffmpeg is not on PATH so the frame was not compared."
    command = jpeg_gray_command(ffmpeg)
    try:
        proc = subprocess.run(command, input=data, capture_output=True, timeout=20, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None, "JPEG was saved, but ffmpeg could not decode it for motion detection."
    if proc.returncode != 0:
        return None, "JPEG was saved, but ffmpeg could not decode it for motion detection."
    try:
        return imaging.parse_pgm(proc.stdout), None
    except ValueError:
        return None, "JPEG was saved, but the decoder did not return a gray frame."


def _frame(data, mime, ext, width, height, gray, camera, note):
    warning = camera.get("transport_warning")
    return {
        "data": data,
        "mime": mime,
        "ext": ext,
        "width": width,
        "height": height,
        "gray": gray,
        "caption": f"Snapshot from {camera['name']}.",
        "event_caption": "",
        "network": True,
        "transport_warning": warning,
        "note": note,
        "redacted_url": camera.get("redacted_url"),
    }
