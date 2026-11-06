"""Tool schemas (what the model reads)."""

_GUARD = (
    "Cameras are chosen by the name in plugin settings, never by a URL or a host. "
    "Arguments other than the ones listed are refused. The safety interlock blocks the call before any connection."
)

CAMERA_LIST = {
    "name": "camera_list",
    "description": "List the cameras this plugin is allowed to use, and entries it rejected. "
                   "Read-only: nothing is contacted. " + _GUARD,
    "parameters": {"type": "object", "properties": {}},
}

CAMERA_STATUS = {
    "name": "camera_status",
    "description": "Show watch state, limits, warnings, and the privacy disclosures for camera-watch. "
                   "Read-only. Call this before the first snapshot of a session. " + _GUARD,
    "parameters": {"type": "object", "properties": {}},
}

CAMERA_SNAPSHOT = {
    "name": "camera_snapshot",
    "description": "Grab one still from a named camera and save it on this machine. "
                   "Returns a local path and a MEDIA: line so the user can see the photo. "
                   "Does not send the still to a model. " + _GUARD,
    "parameters": {"type": "object", "properties": {
        "camera": {"type": "string", "description": "Camera name from camera_list, such as front-door."},
    }, "required": ["camera"]},
}

CAMERA_WATCH_START = {
    "name": "camera_watch_start",
    "description": "Start polling a named camera for scene changes. The first check records a baseline and does not alert. "
                   "Later checks save a still and a one-line caption when the picture changes. "
                   "The interval cannot be faster than the configured minimum (at least 1 second). " + _GUARD,
    "parameters": {"type": "object", "properties": {
        "camera": {"type": "string", "description": "Camera name from camera_list."},
        "interval_s": {"type": "number", "description": "Seconds between checks. Omit it to use the configured interval."},
    }, "required": ["camera"]},
}

CAMERA_WATCH_STOP = {
    "name": "camera_watch_stop",
    "description": "Stop watching one named camera, or every camera when the name is omitted. "
                   "Stopping is always allowed and does not open a new connection. " + _GUARD,
    "parameters": {"type": "object", "properties": {
        "camera": {"type": "string", "description": "Camera name to stop. Omit to stop every watch."},
    }},
}

CAMERA_WATCH_POLL = {
    "name": "camera_watch_poll",
    "description": "Check a named camera once, right now. The first check on a camera only records a baseline. "
                   "The next check reports a scene change when the picture differs, including the saved still. "
                   "In the built-in mock, front-door's second check is a package at the door, and quiet-room never changes. " + _GUARD,
    "parameters": {"type": "object", "properties": {
        "camera": {"type": "string", "description": "Camera name from camera_list."},
    }, "required": ["camera"]},
}

CAMERA_EVENTS = {
    "name": "camera_events",
    "description": "List recent scene-change alerts. Each alert has a local still, a one-line caption, and caption_source. "
                   "model_invoked is false: this plugin did not send the still anywhere. " + _GUARD,
    "parameters": {"type": "object", "properties": {
        "camera": {"type": "string", "description": "Optional camera name to filter by."},
        "limit": {"type": "integer", "description": "How many alerts to return (1-100, default 20)."},
    }},
}

ALL = [
    CAMERA_LIST,
    CAMERA_STATUS,
    CAMERA_SNAPSHOT,
    CAMERA_WATCH_START,
    CAMERA_WATCH_STOP,
    CAMERA_WATCH_POLL,
    CAMERA_EVENTS,
]
