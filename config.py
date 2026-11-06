"""Plugin settings: defaults plus a deep merge of ``plugins.entries.hermes-camera-watch.settings``.

Settings are read on every call, so a config edit applies on the next tool call. This plugin adds no
``HERMES_*`` environment variables. Limits below the hard floors in code are raised, not honored.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, List, Optional

PLUGIN_NAME = "hermes-camera-watch"

# Hard floors. Config cannot lower these; a value below the floor is raised.
ABSOLUTE_MIN_INTERVAL_S = 1.0
ABSOLUTE_MAX_CAMERAS = 32
ABSOLUTE_MAX_WATCHES = 8
ABSOLUTE_MIN_THRESHOLD = 0.02
ABSOLUTE_MIN_PIXEL_DELTA = 1
ABSOLUTE_MAX_BYTES = 20_000_000
ABSOLUTE_MIN_BYTES = 100_000

STILL_DISCLOSURE = (
    "The still is stored only on this machine. This plugin did not send it to a model or to any vision API."
)
MODEL_DISCLOSURE = (
    "Captions are local: a scripted line in mock mode, otherwise a frame-difference line. "
    "A still reaches the model already configured in Hermes only if you ask the agent to describe that saved file. "
    "This plugin never calls a third-party vision service, and it never sends a frame on its own."
)
SCOPE_DISCLOSURE = (
    "These checks gate this plugin's camera tools only. A terminal, code execution, or an edit to config.yaml "
    "can still reach a camera or change these limits."
)
TLS_DISCLOSURE = (
    "http and rtsp are often unencrypted; https and rtsps are preferred. "
    "TLS is verified. A missing CA file refuses the connection. Verification cannot be turned off."
)

# Keys a user might set hoping to skip TLS. Seeing one refuses every grab until it is removed.
BYPASS_PROBE_KEYS = ("insecure", "ssl_verify", "verify", "verify_ssl", "tls_verify", "no_verify", "insecure_skip_verify")

DEFAULTS: Dict[str, Any] = {
    "backend": "mock",  # mock = scripted scenes, no network; live = only the cameras listed below
    "cameras": [
        {"name": "front-door", "transport": "mock", "scene": "package"},
        {"name": "quiet-room", "transport": "mock", "scene": "quiet"},
    ],
    "watch": {
        "interval_s": 5.0,
        "min_interval_s": 2.0,
        "max_watches": 4,
        "motion_threshold": 0.08,
        "pixel_delta": 18,
        "cooldown_s": 20.0,
    },
    "storage": {
        "output_dir": "",  # default: <plugin data dir>/snapshots
        "keep_snapshots": 30,
        "keep_events": 100,
    },
    "fetch": {
        "timeout_s": 8.0,
        "max_bytes": 8000000,
    },
    "tls": {
        "ca_file": "",  # empty = the system trust store; a path that is not a file refuses the connection
    },
    "limits": {
        "max_cameras": 8,
    },
}


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def backend_name(settings: Dict[str, Any]) -> str:
    raw = str(settings.get("backend") or "mock").strip().lower()
    return raw if raw in {"mock", "live"} else "invalid"


def min_interval_s(settings: Dict[str, Any]) -> float:
    number = _finite((settings.get("watch") or {}).get("min_interval_s"), 2.0)
    return min(3600.0, max(ABSOLUTE_MIN_INTERVAL_S, number))


def default_interval_s(settings: Dict[str, Any]) -> float:
    number = _finite((settings.get("watch") or {}).get("interval_s"), 5.0)
    return min(3600.0, max(min_interval_s(settings), number))


def max_watches(settings: Dict[str, Any]) -> int:
    try:
        number = int((settings.get("watch") or {}).get("max_watches", 4))
    except (TypeError, ValueError):
        number = 4
    return min(ABSOLUTE_MAX_WATCHES, max(1, number))


def max_cameras(settings: Dict[str, Any]) -> int:
    try:
        number = int((settings.get("limits") or {}).get("max_cameras", 8))
    except (TypeError, ValueError):
        number = 8
    return min(ABSOLUTE_MAX_CAMERAS, max(1, number))


def motion_threshold(settings: Dict[str, Any]) -> float:
    number = _finite((settings.get("watch") or {}).get("motion_threshold"), 0.08)
    return min(1.0, max(ABSOLUTE_MIN_THRESHOLD, number))


def pixel_delta(settings: Dict[str, Any]) -> int:
    try:
        number = int((settings.get("watch") or {}).get("pixel_delta", 18))
    except (TypeError, ValueError):
        number = 18
    return min(255, max(ABSOLUTE_MIN_PIXEL_DELTA, number))


def cooldown_s(settings: Dict[str, Any]) -> float:
    number = _finite((settings.get("watch") or {}).get("cooldown_s"), 20.0)
    if number < 0:
        return 20.0
    return min(86400.0, number)


def timeout_s(settings: Dict[str, Any]) -> float:
    number = _finite((settings.get("fetch") or {}).get("timeout_s"), 8.0)
    return min(30.0, max(2.0, number))


def max_fetch_bytes(settings: Dict[str, Any]) -> int:
    try:
        number = int((settings.get("fetch") or {}).get("max_bytes", 8000000))
    except (TypeError, ValueError):
        number = 8000000
    return min(ABSOLUTE_MAX_BYTES, max(ABSOLUTE_MIN_BYTES, number))


def keep_count(settings: Dict[str, Any], key: str, default: int) -> int:
    try:
        number = int((settings.get("storage") or {}).get(key, default))
    except (TypeError, ValueError):
        number = default
    return min(500, max(1, number))


def disclosures(settings: Dict[str, Any]) -> List[str]:
    stills = keep_count(settings, "keep_snapshots", 30)
    events = keep_count(settings, "keep_events", 100)
    return [
        STILL_DISCLOSURE,
        MODEL_DISCLOSURE,
        f"Local retention: the newest {stills} stills and {events} events. Older ones are deleted. "
        "A watch stores a still for the baseline and when the scene changes. It does not record continuous video.",
        TLS_DISCLOSURE,
        SCOPE_DISCLOSURE,
    ]


def behavior_notes() -> List[str]:
    return [
        "Mock mode (the default) plays scripted scenes and opens no sockets.",
        "Live mode connects only to the camera URLs in this plugin's settings. Redirects are not followed.",
        "There is no LAN scan, no ONVIF discovery, and no connection to a host that is not in that list.",
        "RTSP and JPEG decoding run ffmpeg on this machine, with a protocol allowlist. HTTP PNG snapshots do not.",
        "A watch is a named thread in the Hermes process. It polls on an interval (at least 1 second) and then disconnects.",
    ]


class Settings:
    """Live view over the plugin's configuration."""

    def __init__(self, getter: Optional[Callable[..., Any]] = None, overrides: Optional[Dict[str, Any]] = None):
        self._getter = getter
        self._overrides = overrides or {}

    def snapshot(self) -> Dict[str, Any]:
        merged = copy.deepcopy(DEFAULTS)
        for key in DEFAULTS:
            value = None
            if self._getter is not None:
                try:
                    value = self._getter(key, None)
                except Exception:
                    value = None
            if value is None:
                continue
            if isinstance(DEFAULTS[key], dict) and isinstance(value, dict):
                merged[key] = deep_merge(DEFAULTS[key], value)
            elif not isinstance(DEFAULTS[key], dict):
                merged[key] = copy.deepcopy(value)
        merged = deep_merge(merged, self._overrides)
        if self._getter is not None:
            for key in BYPASS_PROBE_KEYS:
                if key in merged:
                    continue
                try:
                    value = self._getter(key, None)
                except Exception:
                    value = None
                if value is not None:
                    merged[key] = copy.deepcopy(value)
        return merged

    def __getitem__(self, key: str) -> Any:
        return self.snapshot()[key]
