# hermes-camera-watch

**A Hermes agent watches cameras you own and answers with a still when the scene changes.**

`hermes-camera-watch` is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin. You name each camera and give it either an RTSP URL or an HTTP(S) snapshot URL. The agent can list those cameras, grab one still, and start or stop a watch. When the picture changes, the plugin saves a still and returns it with a one-line caption.

It ships with a **mock porch**. The first look is a clear doorway. The next look is a package on the step. A second mock camera, `quiet-room`, never changes. Judges, `hermes plugins doctor`, and the tests all run with **no camera and no network**.

This is home automation for cameras you configured yourself, in the spirit of a local NVR. It is not a desktop webcam button, a printer-bed camera, a robot camera, or a phone used as a chat channel. It does not wrap Home Assistant or Frigate, and it does not speak ONVIF.

## Quickstart (mock, one command)

```bash
git clone https://github.com/arcabotai/hermes-camera-watch
python3 hermes-camera-watch/demo/mock_demo.py
```

That runs the real tool handlers and the safety hook. It prints the package caption, writes the PNGs, and prints two refusals: a camera name that is a URL, and a snapshot call that tries to pass its own URL.

From Hermes (mock is the default):

```bash
hermes plugins install arcabotai/hermes-camera-watch --enable
hermes chat -q "Look at the front door. If nothing is there, check again — a package may have arrived. Also check the quiet room."
```

Inside a session, `/cameras` is for the **human**: `/cameras status`, `/cameras snap front-door`, `/cameras poll front-door`, `/cameras watch front-door`, `/cameras stop`, `/cameras events`, `/cameras log`.

The storyboard for a short recording is [demo-script.md](demo-script.md).

## Quickstart (a camera you own)

No discovery. You type the URL of a camera you already know. Merge this under `plugins.entries.hermes-camera-watch.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    hermes-camera-watch:
      settings:
        backend: live
        cameras:
          - name: front-door
            transport: snapshot
            url: https://doorbell.example.com/snap.jpg
          - name: drive
            transport: rtsp
            url: rtsps://nvr.example.com/drive
        tls:
          ca_file: ""          # empty = the system trust store
        watch:
          interval_s: 5
          motion_threshold: 0.08
```

`ffmpeg` must be on `PATH` for RTSP and for JPEG snapshots. An HTTP URL that already returns a PNG does not need ffmpeg. The whole `cameras` list replaces the mock list. There is no merge of individual entries.

## Tools

| Tool | What it does |
|---|---|
| `camera_list` | Allowed cameras and rejected entries. Does not connect. |
| `camera_status` | Backend, watches, limits, warnings, and the privacy disclosures. |
| `camera_snapshot` | One still from a named camera. Returns a local path and a `MEDIA:` line. |
| `camera_watch_start` | Poll a named camera on an interval. The first check is a baseline and does not alert. |
| `camera_watch_poll` | Check once, now. Same baseline rule. This is what the mock demo uses. |
| `camera_watch_stop` | Stop one watch, or all of them. Always allowed. |
| `camera_events` | Recent alerts: caption, caption source, local still, `model_invoked: false`. |

**Dashboard tab (read-only).** `hermes dashboard` shows a **Cameras** tab: the latest still, which cameras are being watched, recent alerts, and the privacy lines. It only reads files the plugin already wrote. It has no buttons that start a watch or add a camera. The API is `/api/plugins/hermes-camera-watch/state` and needs the dashboard session token, like every plugin route.

A bundled skill, `camera-watch`, tells the agent to use names from `camera_list`, to poll twice on the mock porch, to show the `MEDIA:` line, and not to route around a refusal.

## How a watch decides that something happened

Each check keeps a small grayscale copy of the frame (32×24). The next check counts the fraction of those pixels that moved by more than `pixel_delta`. At or above `motion_threshold` (default 0.08, and never below 0.02), it saves a still and writes an alert.

* **Mock, `front-door`:** the caption is the scripted line `A package has appeared at the door.` (`caption_source: mock_script`).
* **Mock, `quiet-room`:** the frames match, so there is no alert.
* **Live:** the caption is local, for example `Scene change on front-door: 18% of the frame changed.` (`caption_source: local_diff`).

This is frame differencing, not a detector. Rain, headlights, and a light turning on will alert. The plugin does not record continuous video. A watch connects, takes one still, disconnects, and waits. The wait is at least one second, whatever the config says.

## Safety model

Checks run in a `pre_tool_call` hook, before the handler. A blocked call is returned to the model as the reason, and appended to `blocked.jsonl`.

| Rule | What it refuses |
|---|---|
| `camera_not_allowlisted` | Any name that is not one of your configured labels. A URL is not a name. |
| `unexpected_argument` | A URL, host, scan flag, or any argument the tool does not list. |
| `over_camera_cap` | Entries past `limits.max_cameras` (hard cap 32). They are not contacted. |
| `interval_too_fast` | A watch interval below the minimum (hard floor 1 second). |
| `tls_bypass_refused` | A setting that asks to turn verification off (`insecure`, `ssl_verify: false`, and the same idea under other names). |
| `tls_ca_missing` | `tls.ca_file` or a per-camera `ca_file` that is not a file. |
| `camera_url_rejected` | A live URL whose scheme does not match the transport, or that is not one concrete host. |
| `unknown_backend` | Anything other than `mock` or `live`. |
| `forged_interlock` | An attempt to pass the hook's internal token. |
| `interlock_not_applied` | A grab whose hook did not run. The handler refuses. |
| `too_many_watches` | More simultaneous watches than `watch.max_watches` (hard cap 8). |

Other points:

* **Fail closed.** Grabbing tools are stamped with a per-process nonce. The handler will not run without it.
* **Redirects are not followed.** Put the final snapshot URL in settings.
* **The allowlist is exact.** The host that is dialed is the host in that camera's URL. There is no subnet walk, no ONVIF search, and no connection to a host that is not written there.
* **Stop always works.** `camera_watch_stop` does not open a new connection.
* **Config, not new environment variables.** Limits live in this plugin's settings. The plugin does not add `HERMES_*` variables.
* **TLS verification stays on.** HTTPS uses a verifying TLS context. RTSPS passes ffmpeg's verify flag explicitly, because ffmpeg's own default is to skip verification. The plugin never turns that flag off. A missing CA file refuses the connection instead of skipping the check.

**What this does not protect against.** The hook covers the `camera_*` tools only. An agent with a terminal or code execution, or anyone who can edit `config.yaml`, can still reach a camera or change these limits. The plugin does not sandbox the rest of Hermes. Treat the allowlist as a guardrail on this plugin, not as a network policy for the machine.

## Privacy

* Stills and the event log are stored under the plugin data directory (`<HERMES_HOME>/plugin-data/…/`, or `storage.output_dir` if you set one). The newest `keep_snapshots` stills and `keep_events` events are kept. Older ones are deleted. Files the plugin deletes match its own `cam-*` still prefix.
* Captions are produced on this machine. Mock captions are scripted. Live captions are the frame-difference line. `model_invoked` is false on every alert.
* The plugin does not call a vision API, paid or otherwise. A still reaches the model already configured in Hermes only if you ask the agent to describe that saved file (for example with a vision tool). The skill tells the agent not to do that unless you ask, and to say so when it does.
* `MEDIA:<path>` is how Hermes shows you the local file. This plugin does not upload it.
* A watch can ask Hermes to deliver the alert text into the current session when one exists. If nothing accepts it, the alert is still in `camera_events` and on the dashboard.
* Camera URLs may contain a username and password. Tool results, the dashboard, and the refusal log show `redacted@host` and drop the query string. The RTSP password can still be visible to other users on the machine in the `ffmpeg` process list while a grab is running. Prefer a URL without embedded secrets when the camera allows it.
* Live HTTP connects directly. Proxy environment variables are not used, so a camera URL is not sent to a proxy.
* Nothing is sent to a third-party vision service.

## Config reference

Read on every call from `plugins.entries.hermes-camera-watch.settings`. Defaults shown.

```yaml
backend: mock                 # mock | live
cameras:                      # the whole list replaces this default
  - {name: front-door, transport: mock, scene: package}
  - {name: quiet-room, transport: mock, scene: quiet}
watch:
  interval_s: 5.0             # raised to at least min_interval_s
  min_interval_s: 2.0         # raised to at least 1 second
  max_watches: 4              # hard cap 8
  motion_threshold: 0.08      # raised to at least 0.02
  pixel_delta: 18             # 1–255 on the small grayscale frame
  cooldown_s: 20.0
storage:
  output_dir: ""              # empty = <plugin data>/snapshots
  keep_snapshots: 30
  keep_events: 100
fetch:
  timeout_s: 8.0
  max_bytes: 8000000          # clamped between 100 KB and 20 MB
tls:
  ca_file: ""                 # empty = system trust store; a missing file refuses the connection
limits:
  max_cameras: 8              # hard cap 32
```

Names match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. `front-door` is a name. `rtsp://…` is not.

In `live`, `transport: snapshot` accepts `http` and `https` only. `transport: rtsp` accepts `rtsp` and `rtsps` only. `http` and `rtsp` are often unencrypted; the tool result and the dashboard say so. Prefer `https` and `rtsps` when the camera offers them.

In `mock`, a URL on an entry is ignored and is not contacted. The scene is `package` or `quiet` (default `quiet`).

## Disclosure

* **Network.** Mock mode (the default) opens no sockets. Live mode connects only to the URLs in `cameras`, one still at a time, with no redirect. HTTP(S) uses the Python standard library. RTSP, and JPEG decoding for motion, run `ffmpeg` locally with a protocol allowlist and without a shell. Registration and import do not connect.
* **Processes.** A watch is a thread named `hermes-camera-watch:<camera>`. It is not hidden. `ffmpeg` is a short-lived child process for RTSP and for JPEG, not a background recorder.
* **Files.** Stills, `events.jsonl`, `blocked.jsonl`, and `status.json` under the plugin data directory, unless `storage.output_dir` points somewhere else. The dashboard only reads them.
* **Credentials.** None of its own. A password embedded in a camera URL is your camera's password; see Privacy. The plugin does not read Hermes auth files or `.env`.
* **Dependencies.** None on PyPI. Optional system binary: `ffmpeg`, for RTSP and JPEG. PNG snapshots and the mock do not need it.
* **What a watch is not.** It is not continuous recording, not a cloud NVR, and not a way to find cameras on the LAN.

## Tests

```bash
pip install -r requirements-dev.txt
cd tests && python3 -m pytest
```

The suite covers the safety refusals, the allowlist, redaction, the mock package and the quiet room, a local HTTP server (redirects, non-images, size limit, rate limit, scene change), the ffmpeg argument list, registration against `plugin.yaml`, and the dashboard API. No camera is required. The HTTP tests bind `127.0.0.1` only.

When the Hermes CLI is installed:

```bash
hermes plugins validate /path/to/hermes-camera-watch --install-deps
hermes plugins doctor /path/to/hermes-camera-watch
```

## Limitations

* Motion is a pixel change, not "a person" or "a package", except for the scripted mock caption.
* RTSP depends on ffmpeg. There is no built-in H.264 decoder and no ONVIF.
* One still per check. Not a clip, not an archive of the whole day beyond the retention counts.
* The interlock is not a firewall. See [Safety model](#safety-model).
* Watches do not resume by themselves after Hermes restarts. Start one again. Import does not connect.

## License

MIT — see [LICENSE](LICENSE).

## AI-assistance disclosure

This plugin, its tests, docs, and demo were written by an AI agent from the Arca Computer, Inc. agent family, and reviewed by a human maintainer.

— Built by an AI agent from Arca. All code here is stewarded and reviewed by Luis Felipe Abarca ([@felirami](https://github.com/felirami)) of Arca Computer, Inc. (https://arca.computer). All Arca OSS contributions: https://oss.arcabot.ai
