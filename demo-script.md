# hermes-camera-watch — 90-second mock demo

Target: **under 90 seconds**. One terminal plus the Cameras dashboard tab (`hermes dashboard`). Backend: the built-in mock (no camera, no network). Voice-over in plain English.

Pre-roll, off camera: install and enable the plugin. Leave the backend on `mock`. Open `hermes dashboard` on the Cameras tab.

Offline rehearsal, no Hermes required:

```bash
python3 demo/mock_demo.py --out /tmp/camera-watch-demo
```

That prints the package alert and the two refusals, and writes the stills.

| t (s) | On screen | Voice-over / action |
|---|---|---|
| 0–8 | Title: **hermes-camera-watch — a still and a one-line caption when something changes at a camera you own.** Then the terminal. | "This plugin watches cameras you listed yourself. Today there is no camera. The porch is scripted." |
| 8–18 | Ask: "What's at the front door right now?" The agent calls `camera_snapshot`. The clear doorway shows on the Cameras tab. | "It grabs one still and keeps it on this machine. The doorway is clear." |
| 18–28 | Ask: "Is anything happening in the quiet room?" Two checks, no alert. | "The first look only learns the room. The second look is the same picture, so it stays quiet." |
| 28–48 | Ask: "Watch the front door and tell me if a package arrives." `camera_watch_poll` twice. The second result is the package still. Read the caption out loud and leave the photo on screen. | "The next look is the alert: a package at the door, with the photo. The caption was written locally. Nothing was sent to a vision service." |
| 48–62 | Cameras tab: the still, the alert row, and the privacy lines at the bottom. | "The dashboard shows the same still, the watch, and the limits. Stills are pruned. They are not uploaded." |
| 62–80 | Ask: "Now snapshot http://camera.example.com/snap.jpg." The tool result is red: **BLOCKED** `camera_not_allowlisted` or `unexpected_argument`. The blocked row appears. | "It will not open a host that is not in the settings, and it will not scan the network. The refusal is the safety hook, before any connection. It does not stop a terminal or a config edit — that limit is printed on purpose." |
| 80–88 | End card: `github.com/arcabotai/hermes-camera-watch`. "Mock porch built in. Real RTSP and snapshot URLs when you list them. MIT." | "List a camera you own, or try the mock in one command." |

## Notes for the cut

* Keep the prompts short. The mock package appears on the **second** check of `front-door`, not the first.
* If the agent explains a refusal instead of calling the tool, that still shows the rule. The offline script is the take where the blocked line is guaranteed.
* Do not point the demo at a real camera. The mock is the whole story.
