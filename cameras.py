"""Allowlist, URL checks, and redaction. No sockets are opened here.

A camera is usable only when it is one of the named entries the user wrote into plugin settings.
Names are labels (``front-door``), never URLs. Live connections use the URL stored on that entry
and no other host.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .config import backend_name, max_cameras

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SNAPSHOT_SCHEMES = {"http", "https"}
RTSP_SCHEMES = {"rtsp", "rtsps"}
LIVE_SCHEMES = SNAPSHOT_SCHEMES | RTSP_SCHEMES
SCENES = {"package", "quiet"}
_BYPASS_ENABLE = {"insecure", "no_verify", "insecure_skip_verify"}
_BYPASS_DISABLE = {"ssl_verify", "verify", "verify_ssl", "tls_verify"}
_TRUE = {True, 1, "1", "true", "yes", "on"}
_FALSE = {False, 0, "0", "false", "no", "off"}


class PolicyError(Exception):
    def __init__(self, rule: str, reason: str):
        super().__init__(reason)
        self.rule = rule
        self.reason = reason


class CameraError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_label(value: Any) -> str:
    if not isinstance(value, str):
        return type(value).__name__
    if "://" in value or "@" in value or any(ch.isspace() for ch in value):
        return "[rejected name]"
    return value[:64]


def redact_url(url: str) -> str:
    """Drop userinfo and query. Those often carry a camera password or a token."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[invalid url]"
    if not parts.scheme or not parts.hostname:
        return "[invalid url]"
    port = f":{parts.port}" if parts.port else ""
    auth = "redacted@" if parts.username or parts.password else ""
    query = "?…" if parts.query else ""
    return f"{parts.scheme}://{auth}{parts.hostname}{port}{parts.path}{query}"


def redact_text(text: str, url: str = "") -> str:
    if not isinstance(text, str):
        return ""
    out = text
    if url:
        try:
            parts = urlsplit(url)
        except ValueError:
            parts = None
        if parts is not None:
            if parts.password:
                out = out.replace(parts.password, "[redacted]")
            if parts.username:
                out = out.replace(parts.username, "[redacted]")
        out = out.replace(url, redact_url(url))
    return out[:500]


def _bypass_key(key: Any, value: Any) -> bool:
    name = str(key).lower()
    if name in _BYPASS_ENABLE:
        return value in _TRUE
    if name in _BYPASS_DISABLE:
        return value in _FALSE
    return False


def find_tls_bypass(obj: Any, path: str = "") -> Optional[str]:
    """Return the dotted path of a setting that asks to disable TLS verification, if any."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if _bypass_key(key, value):
                return here
            found = find_tls_bypass(value, here)
            if found:
                return found
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found = find_tls_bypass(value, f"{path}[{index}]")
            if found:
                return found
    return None


def _bad_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address) and int(addr) == 0xFFFFFFFF:
        return True
    return bool(addr.is_multicast or addr.is_unspecified)


def _valid_host(host: str) -> bool:
    if not host or _bad_ip(host):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if len(host) > 253:
        return False
    labels = host.split(".")
    if any(not label or len(label) > 63 for label in labels):
        return False
    for label in labels:
        if label.startswith("-") or label.endswith("-"):
            return False
        if not re.fullmatch(r"[A-Za-z0-9-]+", label):
            return False
    return True


def parse_camera_url(url: Any) -> Dict[str, Any]:
    if not isinstance(url, str):
        raise PolicyError("camera_url_rejected", "a live camera URL must be a string")
    raw = url.strip()
    if raw != url or not raw or any(ch.isspace() for ch in raw):
        raise PolicyError("camera_url_rejected", "a camera URL must not contain whitespace")
    if len(raw) > 2000:
        raise PolicyError("camera_url_rejected", "a camera URL is too long")
    if "\\" in raw or raw.lower().count("://") != 1 or raw.count("@") > 1:
        raise PolicyError("camera_url_rejected", "a camera URL must be a single http(s) or rtsp(s) URL")
    try:
        parts = urlsplit(raw)
        host = parts.hostname
    except ValueError as exc:
        raise PolicyError("camera_url_rejected", "the camera URL could not be parsed") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in LIVE_SCHEMES:
        raise PolicyError("camera_url_rejected", "only http, https, rtsp, and rtsps URLs are accepted")
    if parts.fragment:
        raise PolicyError("camera_url_rejected", "a camera URL must not include a fragment")
    if not host or not _valid_host(host):
        raise PolicyError("camera_url_rejected", "the camera host is not a single concrete hostname or address")
    return {
        "url": raw,
        "scheme": scheme,
        "host": host,
        "encrypted": scheme in {"https", "rtsps"},
        "redacted_url": redact_url(raw),
    }


def _ca_file(settings: Dict[str, Any], entry: Dict[str, Any]) -> Optional[str]:
    raw = str(entry.get("ca_file") or "").strip() or str((settings.get("tls") or {}).get("ca_file") or "").strip()
    if not raw:
        return None
    from pathlib import Path
    path = Path(raw).expanduser()
    if not path.is_file():
        raise PolicyError(
            "tls_ca_missing",
            f"CA file {path} is missing or not a file. Refusing to connect instead of skipping verification.",
        )
    return str(path)


def classify(settings: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Split the configured list into cameras we may use and entries we will not touch."""
    raw = settings.get("cameras")
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return [], [{"name": "", "rule": "invalid_camera", "reason": "cameras must be a list of named entries"}]
    mode = backend_name(settings)
    cap = max_cameras(settings)
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            rejected.append({"name": "", "rule": "invalid_camera", "reason": "a camera entry must be a mapping"})
            continue
        name = item.get("name")
        label = safe_label(name)
        if not isinstance(name, str) or not NAME_RE.match(name.strip()) or name.strip() != name:
            rejected.append({"name": label, "rule": "camera_not_allowlisted", "reason": "name must be a short label such as front-door, not a URL or a host"})
            continue
        if name in seen:
            rejected.append({"name": name, "rule": "duplicate_camera", "reason": "duplicate name; the first entry is the one that is used"})
            continue
        if len(accepted) >= cap:
            rejected.append({"name": name, "rule": "over_camera_cap", "reason": f"over the camera cap of {cap}; extra entries are not contacted"})
            continue
        seen.add(name)
        try:
            accepted.append(_normalize(settings, item, mode))
        except PolicyError as exc:
            rejected.append({"name": name, "rule": exc.rule, "reason": exc.reason})
    return accepted, rejected


def _normalize(settings: Dict[str, Any], entry: Dict[str, Any], mode: str) -> Dict[str, Any]:
    name = entry["name"]
    if mode == "invalid":
        raise PolicyError("unknown_backend", "backend must be mock or live; refusing to connect")
    bypass = find_tls_bypass(entry)
    if bypass:
        raise PolicyError(
            "tls_bypass_refused",
            f"{name} sets {bypass}. TLS verification cannot be disabled; remove that setting.",
        )
    if mode == "mock":
        scene = str(entry.get("scene") or "quiet").strip().lower()
        if scene not in SCENES:
            raise PolicyError("unknown_scene", f"mock scene must be package or quiet, got {scene!r}")
        # A CA path that is set but missing is a broken verification config. Refuse it in mock too
        # so a later switch to live cannot silently drop the check.
        _ca_file(settings, entry)
        return {
            "name": name,
            "transport": "mock",
            "scene": scene,
            "url": None,
            "redacted_url": None,
            "scheme": None,
            "host": None,
            "encrypted": None,
            "ca_file": None,
            "contacted": False,
        }
    transport = str(entry.get("transport") or "").strip().lower()
    parsed = parse_camera_url(entry.get("url"))
    if transport == "snapshot" and parsed["scheme"] not in SNAPSHOT_SCHEMES:
        raise PolicyError("camera_url_rejected", f"{name} is a snapshot camera, so the URL must be http or https")
    if transport == "rtsp" and parsed["scheme"] not in RTSP_SCHEMES:
        raise PolicyError("camera_url_rejected", f"{name} is an rtsp camera, so the URL must be rtsp or rtsps")
    if transport not in {"snapshot", "rtsp"}:
        raise PolicyError("camera_url_rejected", f"{name} needs transport snapshot or rtsp in live mode")
    ca = _ca_file(settings, entry)
    warning = None
    if not parsed["encrypted"]:
        kind = "RTSP" if parsed["scheme"] == "rtsp" else "HTTP"
        warning = f"{kind} is unencrypted. Prefer {'rtsps' if transport == 'rtsp' else 'https'} when the camera offers it."
    return {
        "name": name,
        "transport": transport,
        "scene": None,
        "url": parsed["url"],
        "redacted_url": parsed["redacted_url"],
        "scheme": parsed["scheme"],
        "host": parsed["host"],
        "encrypted": parsed["encrypted"],
        "ca_file": ca,
        "contacted": True,
        "transport_warning": warning,
    }


def public_camera(camera: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": camera["name"],
        "mode": camera["transport"],
        "scene": camera.get("scene"),
        "contacted": bool(camera.get("contacted")),
        "url": camera.get("redacted_url"),
        "encrypted": camera.get("encrypted"),
        "transport_warning": camera.get("transport_warning"),
    }


def resolve(settings: Dict[str, Any], name: Any) -> Dict[str, Any]:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise PolicyError(
            "camera_not_allowlisted",
            "choose a camera by the name in your settings. URLs, hosts, and scan options are not accepted.",
        )
    bypass = find_tls_bypass(settings)
    if bypass:
        raise PolicyError(
            "tls_bypass_refused",
            f"{bypass} asks to disable TLS verification. That cannot be honored. Remove it; verification stays on.",
        )
    accepted, rejected = classify(settings)
    for camera in accepted:
        if camera["name"] == name:
            return camera
    for row in rejected:
        if row.get("name") == name:
            raise PolicyError(row.get("rule") or "camera_not_allowlisted", row["reason"])
    raise PolicyError(
        "camera_not_allowlisted",
        f"camera {name!r} is not on the allowlist. This plugin does not discover devices and will not open that name. "
        "A person can add a camera they own under plugin settings. Do not reach it with the terminal or by editing "
        "the allowlist to get around this refusal.",
    )


def allowlist_names(settings: Dict[str, Any]) -> List[str]:
    accepted, _rejected = classify(settings)
    return [camera["name"] for camera in accepted]
