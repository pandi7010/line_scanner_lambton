"""
list_cameras.py - Shows every MindVision camera this PC can see.

Use it to find the serial numbers to put in camera_config.json.

    python tools\\list_cameras.py
"""

import os
import sys

# Let this script find the project folder (one level up).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware import CameraError, list_cameras  # noqa: E402


def main():
    try:
        cameras = list_cameras()
    except CameraError as e:
        print(f"ERROR: {e}")
        return 1

    if not cameras:
        print("No cameras found. Check the network cable, camera power and PC IP address.")
        return 1

    print(f"Found {len(cameras)} camera(s):\n")
    for i, cam in enumerate(cameras, start=1):
        print(f"  {i}. serial: {cam['serial']}")
        print(f"     name  : {cam['name']}   model: {cam['model']}")
        print(f"     port  : {cam['port']}   ip: {cam['ip'] or '-'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
