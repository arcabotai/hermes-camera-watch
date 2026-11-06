"""Dashboard backend, mounted at /api/plugins/hermes-camera-watch/. Read-only.

The dashboard is a separate process from the agent. It reads the files the plugin already
wrote (status, events, stills, refusals) and does not open a camera or clear a watch.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter()
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_MAX_IMAGE_BYTES = 1_500_000
_EVENT_KEYS = ("id", "ts", "iso", "camera", "caption", "caption_source", "model_invoked", "score")
_CAMERA_KEYS = (
    "name", "mode", "scene", "contacted", "url", "encrypted", "transport_warning",
    "watching", "interval_s", "last_score", "last_caption", "last_error",
)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _data_dir() -> Optional[Path]:
    root = _hermes_home() / "plugin-data"
    if not root.is_dir():
        return None
    found = [path for path in root.iterdir() if path.is_dir() and "hermes-camera-watch" in path.name]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def _config():
    spec = importlib.util.spec_from_file_location("_hcw_config", _PLUGIN_ROOT / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings() -> Dict[str, Any]:
    cfg = _config()
    user: Dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly
        entry = ((load_config_readonly() or {}).get("plugins", {}).get("entries", {}) or {})
        user = (entry.get("hermes-camera-watch") or {}).get("settings") or {}
    except Exception:
        pass
    return cfg.deep_merge(cfg.DEFAULTS, user if isinstance(user, dict) else {})


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _events(data: Path, limit: int) -> List[Dict[str, Any]]:
    path = data / "events.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        item = {key: row.get(key) for key in _EVENT_KEYS}
        raw_path = row.get("path")
        if isinstance(raw_path, str) and raw_path:
            item["file"] = Path(raw_path).name
        out.append(item)
    return out


def _blocked(data: Path, limit: int) -> List[Dict[str, Any]]:
    path = data / "blocked.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        out.append({key: row.get(key) for key in ("ts", "iso", "tool", "rule", "reason")})
    return out


def _snapshot_dir(settings: Dict[str, Any], data: Path) -> Path:
    custom = str((settings.get("storage") or {}).get("output_dir") or "").strip()
    if not custom or "://" in custom:
        return data / "snapshots"
    return Path(custom).expanduser()


def _latest_still(directory: Path) -> Optional[Path]:
    if not directory.is_dir():
        return None
    files = [
        path for path in directory.iterdir()
        if path.is_file() and path.name.startswith("cam-") and path.suffix in {".png", ".jpg", ".jpeg"}
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


@router.get("/state")
def state(since: float = 0.0, limit: int = 20) -> Dict[str, Any]:
    cfg = _config()
    settings = _settings()
    data = _data_dir()
    out: Dict[str, Any] = {
        "ok": True,
        "now": time.time(),
        "backend": cfg.backend_name(settings),
        "warnings": [],
        "disclosures": cfg.disclosures(settings),
        "cameras": [],
        "watches": [],
        "events": [],
        "blocked": [],
        "snapshot": None,
        "limits": {
            "min_interval_s": cfg.min_interval_s(settings),
            "motion_threshold": cfg.motion_threshold(settings),
            "keep_snapshots": cfg.keep_count(settings, "keep_snapshots", 30),
            "keep_events": cfg.keep_count(settings, "keep_events", 100),
        },
        "data_dir_found": data is not None,
    }
    if data is None:
        out["warnings"] = ["No camera-watch data directory yet. Nothing has been saved."]
        return out
    status = _read_json(data / "status.json")
    if isinstance(status, dict):
        out["backend"] = status.get("backend") or out["backend"]
        out["warnings"] = list(status.get("warnings") or [])
        out["cameras"] = [
            {key: row.get(key) for key in _CAMERA_KEYS}
            for row in (status.get("cameras") or []) if isinstance(row, dict)
        ]
        out["watches"] = [
            {"camera": row.get("camera"), "interval_s": row.get("interval_s"), "running": row.get("running")}
            for row in (status.get("watches") or []) if isinstance(row, dict)
        ]
    out["events"] = _events(data, max(1, min(int(limit), 100)))
    out["blocked"] = _blocked(data, 30)
    still = _latest_still(_snapshot_dir(settings, data))
    if still is not None and still.stat().st_size <= _MAX_IMAGE_BYTES:
        mtime = still.stat().st_mtime
        info: Dict[str, Any] = {"name": still.name, "mtime": mtime, "age_s": round(time.time() - mtime, 1)}
        if mtime != since:
            mime = "image/png" if still.suffix == ".png" else "image/jpeg"
            info["data_url"] = f"data:{mime};base64," + base64.b64encode(still.read_bytes()).decode("ascii")
        out["snapshot"] = info
    return out
