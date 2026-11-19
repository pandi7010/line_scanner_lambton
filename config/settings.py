"""
config/settings.py - Reads and writes the settings file  camera_config.json

Everything you may want to change (serial numbers, frame height, exposure,
grid, zoom ...) lives in camera_config.json, NOT in the code.
This file loads it, fills in safe defaults for anything missing, and can
save new values back.
"""

import json
import os

# The project folder (D:\lambtonblr) = one level above this file.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PROJECT_DIR, "camera_config.json")
SNAPSHOT_DIR = os.path.join(PROJECT_DIR, "snapshots")

# Used when a value is missing from camera_config.json.
CAMERA_DEFAULTS = {
    "serial": "",
    "frame_lines": 16,        # lines per frame (set on the camera)
    "v_stretch": 3,           # screen pixels per camera line (view only)
    "mirror": False,          # flip left <-> right (view only)
    "start_pixel": 0,         # first pixel of the chute (grid start)
    "end_pixel": 4096,        # last pixel of the chute (grid end)
    "lower": 68,              # detection: darker than this = something there
    "upper": 221,             # detection: brighter than this = something there
    "band_mode": False,       # detection: True = hit when INSIDE lower..upper
    "channel_points": {},     # calibration: {valve: pixel of its centre}; empty = equal channels
}

DEFAULTS = {
    "channels": 63,           # ejector channels (valves); the grid has channels + 1 = 64 lines
    "display": {
        "refresh_fps": 25,    # screen redraws per second
        "show_grid": True,
    },
    "detection": {
        "colour": "BLUE",     # colour that is checked (background is blue) - like lambton-main
        "min_pixels": 2,      # a channel needs at least this many hit pixels (noise filter)
        "led_hold_ms": 150,   # an LED is SHOWN at least this long (a 10 ms blow is too short to see)
    },
    "ejector": {
        "delay_ms": 10.0,     # object touched the decision line -> ejector LED ON
        "blow_ms": 10.0,      # LED stays ON until the object's last line + delay + this
        "same_object_ms": 30,   # FRONT + REAR in one channel within this time = the same object
        "late_tolerance_ms": 1.0,  # up to this much late still counts as ON TIME
        "end_gap_lines": 2,   # an object ends after this many empty camera lines
    },
    "clock": {
        "use_camera_timestamp": True,   # exact line times from the camera's own clock
        "default_line_rate_hz": 4800,   # only used until the real line rate is measured
        "offset_window_frames": 300,    # camera -> PC clock: smallest offset of the last N frames
    },
    "cameras": {},
}


def _merge(defaults, user):
    """Copy of `defaults` with the values from `user` written on top."""
    result = dict(defaults)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path=CONFIG_FILE):
    """Load camera_config.json and fill in defaults."""
    user_config = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    else:
        print(f"[CONFIG] {path} not found - using default settings.")

    config = _merge(DEFAULTS, user_config)
    # Every camera gets all CAMERA_DEFAULTS keys.
    config["cameras"] = {name: _merge(CAMERA_DEFAULTS, cam)
                         for name, cam in config["cameras"].items()}
    return config


def save_camera_settings(values, path=CONFIG_FILE, ejector=None):
    """
    Write new camera values into camera_config.json, keeping everything else.

    values example:
        {"FRONT": {"frame_lines": 16, "exposure_us": 80.0, "v_stretch": 3, "lower": 68, "upper": 221},
         "REAR":  {"frame_lines": 16, "exposure_us": 110.0, "v_stretch": 3}}

    ejector example (optional): {"delay_ms": 5.0, "blow_ms": 10.0}

    A copy of the old file is kept as camera_config.json.bak, just in case.
    """
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(path + ".bak", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    cameras = data.setdefault("cameras", {})
    for name, new_values in values.items():
        cameras.setdefault(name, {}).update(new_values)
    if ejector:
        data.setdefault("ejector", {}).update(ejector)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
