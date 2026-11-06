"""Plugin runtime: settings, allowlist, mock scenes, live grabs, watches, and stored alerts.

``register()`` builds one of these and does not open a camera. A watch thread is started only by
``camera_watch_start``. Live grabs re-check the allowlist on every poll and only dial the URL
stored for that camera.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import cameras, imaging, scenes
from .backend_live import fetch_http, fetch_rtsp
from .cameras import CameraError, PolicyError
from .config import (
    PLUGIN_NAME,
    Settings,
    backend_name,
    behavior_notes,
    cooldown_s,
    disclosures,
    keep_count,
    max_watches,
    min_interval_s,
    motion_threshold,
    pixel_delta,
)
from .safety import Blocked, Interlock

logger = logging.getLogger("hermes_camera_watch")

_STOP_CODES = {"ffmpeg_missing", "tls_failed", "tls_ca_missing", "not_an_image"}


class Runtime:
    def __init__(self, get_config: Optional[Callable[..., Any]] = None, data_dir: Optional[Path] = None,
                 overrides: Optional[Dict[str, Any]] = None, clock: Optional[Callable[[], float]] = None,
                 notify: Optional[Callable[[str], None]] = None):
        self.settings = Settings(get_config, overrides)
        self._data_dir = Path(data_dir) if data_dir else None
        self._clock = clock or time.time
        self._notify = notify
        self._lock = threading.RLock()
        self._mock: Dict[str, Dict[str, Any]] = {}
        self._motion: Dict[str, Dict[str, Any]] = {}
        self._watches: Dict[str, Dict[str, Any]] = {}
        self._last_grab: Dict[str, float] = {}
        self._seq = 0
        self.interlock = Interlock(self.settings.snapshot, lambda: self.data_dir, self.active_watches)
        self._write_status()

    def now(self) -> float:
        return float(self._clock())

    @property
    def data_dir(self) -> Path:
        if self._data_dir is None:
            self._data_dir = Path(tempfile.mkdtemp(prefix=f"{PLUGIN_NAME}-"))
        return self._data_dir

    def snapshot_dir(self) -> Path:
        raw = str((self.settings.snapshot().get("storage") or {}).get("output_dir") or "").strip()
        if not raw:
            directory = self.data_dir / "snapshots"
        else:
            if "://" in raw:
                raise CameraError("bad_output_dir", "storage.output_dir must be a local directory, not a URL")
            directory = Path(raw).expanduser().resolve()
            if directory == Path("/").resolve() or directory == Path.home().resolve():
                raise CameraError("bad_output_dir", "refusing to store stills in / or the home directory")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def active_watches(self) -> List[str]:
        with self._lock:
            return [name for name, watch in self._watches.items() if not watch["stop"].is_set()]

    def shutdown(self) -> None:
        self.stop_watch(None)

    # ----------------------------------------------------------------- views
    def public_state(self) -> Dict[str, Any]:
        settings = self.settings.snapshot()
        accepted, rejected = cameras.classify(settings)
        with self._lock:
            watches = {name: self._watch_row(name, watch) for name, watch in self._watches.items()}
            motion = {name: dict(row) for name, row in self._motion.items()}
        cameras_out = []
        for camera in accepted:
            row = cameras.public_camera(camera)
            extra = motion.get(camera["name"], {})
            row.update({
                "watching": camera["name"] in watches,
                "interval_s": (watches.get(camera["name"]) or {}).get("interval_s"),
                "last_score": extra.get("last_score"),
                "last_caption": extra.get("last_caption"),
                "last_error": extra.get("last_error"),
            })
            cameras_out.append(row)
        warnings = _warnings(settings, cameras_out, rejected)
        return {
            "backend": backend_name(settings),
            "cameras": cameras_out,
            "rejected": rejected,
            "watches": list(watches.values()),
            "warnings": warnings,
            "disclosures": disclosures(settings),
            "behavior": behavior_notes(),
            "limits": {
                "min_interval_s": min_interval_s(settings),
                "max_watches": max_watches(settings),
                "motion_threshold": motion_threshold(settings),
                "cooldown_s": cooldown_s(settings),
                "keep_snapshots": keep_count(settings, "keep_snapshots", 30),
                "keep_events": keep_count(settings, "keep_events", 100),
            },
            "counters": dict(self.interlock.counters),
            "recent_blocked": self.interlock.recent_blocked(5),
        }

    def _watch_row(self, name: str, watch: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "camera": name,
            "interval_s": watch["interval_s"],
            "started": watch["started"],
            "running": not watch["stop"].is_set(),
        }

    def _write_status(self) -> None:
        try:
            payload = self.public_state()
            payload["updated"] = self.now()
            path = self.data_dir / "status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, default=str))
            os.replace(tmp, path)
        except Exception:
            logger.debug("could not write status.json", exc_info=True)

    # ----------------------------------------------------------------- grabs
    def snapshot(self, name: str, notes: Optional[List[str]] = None) -> Dict[str, Any]:
        camera = cameras.resolve(self.settings.snapshot(), name)
        frame = self._grab(camera, advance=False)
        saved = self._save_still(camera["name"], frame)
        self._remember(camera["name"], frame, None)
        self._write_status()
        return _still_result(camera, frame, saved, baseline=False, event=None, notes=notes, reason="snapshot")

    def poll_camera(self, name: str, notes: Optional[List[str]] = None) -> Dict[str, Any]:
        camera = cameras.resolve(self.settings.snapshot(), name)
        frame = self._grab(camera, advance=True)
        settings = self.settings.snapshot()
        with self._lock:
            state = self._motion.setdefault(camera["name"], {})
            if state.get("scene") != camera.get("scene") or state.get("transport") != camera.get("transport"):
                state.clear()
                state["scene"] = camera.get("scene")
                state["transport"] = camera.get("transport")
            previous = state.get("gray")
            gray = frame.get("gray")
            if gray is None:
                self._remember(camera["name"], frame, None)
                saved = self._save_still(camera["name"], frame)
                state["last_error"] = frame.get("note") or "frame could not be compared"
                self._write_status()
                return _still_result(camera, frame, saved, baseline=False, event=None, notes=notes,
                                     reason="not_compared", score=None)
            if previous is None or len(previous) != len(gray):
                state["gray"] = gray
                state["last_score"] = None
                state["last_caption"] = frame.get("caption")
                state["last_error"] = None
                saved = self._save_still(camera["name"], frame)
                self._write_status()
                return _still_result(camera, frame, saved, baseline=True, event=None, notes=notes,
                                     reason="baseline", score=None)
            score = imaging.changed_fraction(previous, gray, pixel_delta(settings))
            state["gray"] = gray
            state["last_score"] = round(score, 4)
            state["last_error"] = None
            threshold = motion_threshold(settings)
            if score < threshold:
                state["last_caption"] = frame.get("caption")
                self._write_status()
                return _still_result(camera, frame, None, baseline=False, event=None, notes=notes,
                                     reason="unchanged", score=score)
            last_event = float(state.get("last_event_ts") or 0)
            if self.now() - last_event < cooldown_s(settings):
                state["last_caption"] = frame.get("caption")
                self._write_status()
                return _still_result(camera, frame, None, baseline=False, event=None, notes=notes,
                                     reason="cooldown", score=score)
            caption, source = _caption_for(camera, frame, score)
            saved = self._save_still(camera["name"], frame)
            event = self._record_event(camera["name"], caption, source, score, saved, frame)
            state["last_event_ts"] = self.now()
            state["last_caption"] = caption
            self._write_status()
        self._deliver(event)
        return _still_result(camera, frame, saved, baseline=False, event=event, notes=notes,
                             reason="scene_change", score=score)

    def _grab(self, camera: Dict[str, Any], advance: bool) -> Dict[str, Any]:
        if camera["transport"] == "mock":
            return self._grab_mock(camera, advance=advance)
        self._rate_limit(camera["name"])
        if camera["transport"] == "snapshot":
            return fetch_http(camera, self.settings.snapshot())
        if camera["transport"] == "rtsp":
            return fetch_rtsp(camera, self.settings.snapshot(), self.data_dir)
        raise CameraError("camera_not_connectable", "this camera has no usable transport")

    def _rate_limit(self, name: str) -> None:
        settings = self.settings.snapshot()
        floor = min_interval_s(settings)
        now = self.now()
        with self._lock:
            last = self._last_grab.get(name, 0.0)
            if last and now - last < floor:
                wait = floor - (now - last)
                raise CameraError(
                    "rate_limited",
                    f"{name} was contacted {now - last:.1f}s ago. The minimum interval is {floor:.0f}s; wait {wait:.1f}s.",
                )
            self._last_grab[name] = now

    def _grab_mock(self, camera: Dict[str, Any], advance: bool) -> Dict[str, Any]:
        with self._lock:
            state = self._mock.setdefault(camera["name"], {"step": 0, "seen": None, "scene": None})
            if state.get("scene") != camera.get("scene"):
                state["step"] = 0
                state["seen"] = None
                state["scene"] = camera.get("scene")
            if not advance and state["seen"] is not None:
                rgb, caption, event_caption = state["seen"]
            else:
                step = state["step"] if advance else 0
                rgb, caption, event_caption = scenes.render(camera["scene"], step)
                state["seen"] = (rgb, caption, event_caption)
                if advance:
                    state["step"] = step + 1
        width, height = scenes.WIDTH, scenes.HEIGHT
        return {
            "data": imaging.encode_png(width, height, rgb),
            "mime": "image/png",
            "ext": "png",
            "width": width,
            "height": height,
            "gray": imaging.to_gray_small(rgb, width, height),
            "caption": caption,
            "event_caption": event_caption,
            "network": False,
            "transport_warning": None,
            "note": None,
            "redacted_url": None,
            "rgb": rgb,
        }

    def _remember(self, name: str, frame: Dict[str, Any], score: Optional[float]) -> None:
        with self._lock:
            state = self._motion.setdefault(name, {})
            state["last_caption"] = frame.get("caption")
            if score is not None:
                state["last_score"] = score

    def _save_still(self, name: str, frame: Dict[str, Any]) -> Dict[str, Any]:
        directory = self.snapshot_dir()
        with self._lock:
            self._seq += 1
            seq = self._seq
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"cam-{name}-{stamp}-{seq:04d}.{frame['ext']}"
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_bytes(frame["data"])
        os.replace(partial, path)
        _prune(directory, keep_count(self.settings.snapshot(), "keep_snapshots", 30))
        return {"path": str(path), "media_tag": f"MEDIA:{path}", "bytes": len(frame["data"])}

    def _record_event(self, name: str, caption: str, source: str, score: float,
                      saved: Dict[str, Any], frame: Dict[str, Any]) -> Dict[str, Any]:
        ts = self.now()
        event = {
            "id": f"evt-{int(ts * 1000)}-{name}",
            "ts": ts,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
            "camera": name,
            "caption": caption,
            "caption_source": source,
            "model_invoked": False,
            "score": round(score, 4),
            "path": saved["path"],
            "media_tag": saved["media_tag"],
            "mime": frame["mime"],
            "bytes": saved["bytes"],
            "width": frame.get("width"),
            "height": frame.get("height"),
        }
        path = self.data_dir / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        keep = keep_count(self.settings.snapshot(), "keep_events", 100)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > keep:
                path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
        except OSError:
            pass
        return event

    def recent_events(self, camera: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        path = self.data_dir / "events.jsonl"
        if not path.exists():
            return []
        rows = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if camera and row.get("camera") != camera:
                continue
            rows.append({key: row.get(key) for key in (
                "id", "ts", "iso", "camera", "caption", "caption_source", "model_invoked",
                "score", "path", "media_tag", "mime", "bytes", "width", "height",
            )})
            if len(rows) >= limit:
                break
        return rows

    def _deliver(self, event: Dict[str, Any]) -> None:
        if not self._notify:
            return
        text = (
            f"Camera alert ({event['camera']}): {event['caption']}\n"
            f"{event['media_tag']}\n"
            "The still is stored only on this machine. This plugin did not send it to a model or to any vision API."
        )
        try:
            self._notify(text)
        except Exception:
            logger.debug("camera alert was stored; no session accepted it", exc_info=True)

    # ---------------------------------------------------------------- watches
    def start_watch(self, name: str, interval: float, notes: Optional[List[str]] = None) -> Dict[str, Any]:
        camera = cameras.resolve(self.settings.snapshot(), name)
        with self._lock:
            existing = self._watches.get(camera["name"])
            if existing and not existing["stop"].is_set():
                first = None
                already = True
            else:
                already = False
                stop = threading.Event()
                self._watches[camera["name"]] = {
                    "stop": stop,
                    "interval_s": float(interval),
                    "started": self.now(),
                    "thread": None,
                }
        if already:
            state = self.public_state()
            state.update({"ok": True, "already_watching": True, "camera": camera["name"], "interval_s": interval})
            return state
        try:
            first = self.poll_camera(camera["name"], notes)
        except Exception:
            self.stop_watch(camera["name"])
            raise
        thread = threading.Thread(
            target=self._watch_loop,
            args=(camera["name"], self._watches[camera["name"]]["stop"], float(interval)),
            name=f"hermes-camera-watch:{camera['name']}",
            daemon=True,
        )
        with self._lock:
            watch = self._watches.get(camera["name"])
            if watch is None or watch["stop"].is_set():
                return first
            watch["thread"] = thread
        thread.start()
        self._write_status()
        if isinstance(first, dict):
            first["watching"] = True
            first["interval_s"] = float(interval)
        return first

    def _watch_loop(self, name: str, stop: threading.Event, interval: float) -> None:
        while not stop.is_set():
            if stop.wait(interval):
                break
            try:
                self.poll_camera(name)
            except (Blocked, PolicyError) as exc:
                logger.warning("watch %s stopped: %s", name, exc)
                self._note_error(name, str(exc))
                self.stop_watch(name)
                break
            except CameraError as exc:
                self._note_error(name, exc.message)
                if exc.code in _STOP_CODES:
                    self.stop_watch(name)
                    break
            except Exception:
                logger.exception("watch %s failed", name)
                self._note_error(name, "watch poll failed")

    def _note_error(self, name: str, message: str) -> None:
        with self._lock:
            self._motion.setdefault(name, {})["last_error"] = message
        self._write_status()

    def stop_watch(self, name: Optional[str]) -> Dict[str, Any]:
        with self._lock:
            targets = []
            for watch_name, watch in list(self._watches.items()):
                if name and watch_name != name:
                    continue
                watch["stop"].set()
                thread = watch.get("thread")
                if thread is not None and thread is not threading.current_thread():
                    targets.append(thread)
                self._watches.pop(watch_name, None)
        for thread in targets:
            thread.join(timeout=2)
        self._write_status()
        return {"stopped": name or "all", "watching": self.active_watches()}


def _caption_for(camera: Dict[str, Any], frame: Dict[str, Any], score: float):
    if camera.get("transport") == "mock" and camera.get("scene") == "package" and frame.get("event_caption"):
        return frame["event_caption"], "mock_script"
    percent = round(score * 100)
    return f"Scene change on {camera['name']}: {percent}% of the frame changed.", "local_diff"


def _still_result(camera, frame, saved, baseline, event, notes, reason, score=None):
    body = {
        "ok": True,
        "camera": camera["name"],
        "backend": "mock" if camera["transport"] == "mock" else "live",
        "network": bool(frame.get("network")),
        "baseline": baseline,
        "reason": reason,
        "score": None if score is None else round(score, 4),
        "caption": (event or {}).get("caption") or frame.get("caption"),
        "caption_source": (event or {}).get("caption_source") or ("mock_script" if camera["transport"] == "mock" else "local_diff"),
        "model_invoked": False,
        "event": event,
        "encrypted": camera.get("encrypted"),
        "transport_warning": frame.get("transport_warning"),
        "note": frame.get("note"),
        "interlock": notes or [],
        "disclosure": (
            "The still is stored only on this machine. This plugin did not send it to a model or to any vision API."
        ),
    }
    if saved:
        body.update({
            "path": saved["path"],
            "media_tag": saved["media_tag"],
            "mime": frame["mime"],
            "bytes": saved["bytes"],
            "width": frame.get("width"),
            "height": frame.get("height"),
        })
    if camera.get("redacted_url"):
        body["url"] = camera["redacted_url"]
    return body


def _warnings(settings, cameras_out, rejected) -> List[str]:
    mode = backend_name(settings)
    notes = []
    if mode == "mock":
        notes.append("Mock mode: scripted scenes only. No camera URL is contacted.")
    elif mode == "live":
        notes.append("Live mode: connecting only to cameras listed in this plugin's settings.")
    else:
        notes.append("Backend is not mock or live. Refusing to connect until it is corrected.")
    bypass = cameras.find_tls_bypass(settings)
    if bypass:
        notes.append(f"{bypass} asks to disable TLS verification. Connections stay refused until that setting is removed.")
    for camera in cameras_out:
        if camera.get("transport_warning"):
            notes.append(f"{camera['name']}: {camera['transport_warning']}")
    if rejected:
        notes.append(f"{len(rejected)} configured entr{'y' if len(rejected) == 1 else 'ies'} rejected and not contacted.")
    return notes


def _prune(directory: Path, keep: int) -> None:
    try:
        files = [
            path for path in directory.iterdir()
            if path.is_file() and path.name.startswith("cam-") and path.suffix in {".png", ".jpg", ".jpeg"}
        ]
        files.sort(key=lambda path: path.stat().st_mtime)
        for path in files[:-max(1, keep)]:
            path.unlink(missing_ok=True)
    except OSError:
        pass
