---
name: camera-watch
description: Watch cameras the user owns and alert with a saved still when the scene changes. Use only named cameras from settings. Never scan a network or pass a URL.
---

# Watching cameras with hermes-camera-watch

You can check cameras the user has already listed in this plugin's settings. You cannot discover cameras, and you cannot point a tool at a host they did not write down.

## Before you look
1. `camera_status` — backend (`mock` or `live`), warnings, and the disclosures. Read them.
2. `camera_list` — the only names you may pass. Ignore rejected entries; they are not contacted.

## Mock mode (the default)
No camera is contacted. `front-door` is a scripted doorway. `quiet-room` never changes.
- `camera_snapshot` on `front-door` shows a clear doorway.
- The first `camera_watch_poll` on a camera only records a baseline and does not alert.
- The next poll on `front-door` reports a package and a still. Say the caption and include the `MEDIA:` line in your reply so the user sees the photo.
- Two polls on `quiet-room` report no change. Say that nothing happened. Do not invent motion.

## Live mode
Pass only a name from `camera_list`. The plugin dials the URL stored for that name.
- `http` and `rtsp` are often unencrypted. Mention `transport_warning` when it is present.
- A refusal that names TLS means verification failed or a setting tried to turn it off. Do not look for a way to skip verification.
- `camera_watch_start` checks on an interval (at least 1 second). The first check is a baseline. Later changes become alerts with a still.
- `camera_watch_poll` checks once, now. Use it when the user asks "anything at the door?"

## When the interlock says no
A refusal looks like `hermes-camera-watch interlock BLOCKED ... [rule]: reason`.
- Do not retry with a URL, a host, an address, or an extra argument.
- Do not use the terminal, a script, or a config edit to reach a camera the tool refused.
- Do not add cameras yourself. Tell the user the name has to be written into plugin settings, and only for a camera they own.
- `interval_too_fast`: use a slower interval, or poll once.
- `tls_bypass_refused` / `tls_ca_missing`: stop. Verification stays on.
- `camera_not_allowlisted`: stop. Say which names are actually configured.

## Pictures and captions
- Show a still by putting its `MEDIA:` line in your reply.
- The caption is local (`caption_source` is `mock_script` or `local_diff`). `model_invoked` is false. This plugin did not upload the image.
- Do not describe the picture with a vision tool unless the user asks what it shows. If you do, say that the still is being sent to the model already configured in Hermes.
- Do not send the file anywhere else.

## Stopping
`camera_watch_stop` is always appropriate when the user is done or something looks wrong. You do not need a reason to stop.

## Honesty
- Report the tool result, not the story you expected. A quiet room stays quiet.
- Frame differencing is not a person detector. Rain, headlights, and a light switching on can alert. Say that when a live alert is only a percentage change.
- These limits cover the `camera_*` tools only. Say so if the user asks whether the plugin can stop every other way of reaching the camera.
