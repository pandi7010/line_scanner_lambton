"""
HARDWARE LAYER - everything that talks directly to a device.

  mindvision_camera.py -> one MindVision GigE line-scan camera (open / read / settings / close)
  sdk/                 -> MindVision SDK (mvsdk.py + MVCAMSDK_X64.dll, copied from lambton-main)

Rules for this layer:
  * no PyQt5 (no windows, no signals)
  * no threads - the core layer decides WHEN to read
  * a new device (another camera brand, a lights board, ...) = a new file here
"""

from hardware.mindvision_camera import (SDK_AVAILABLE, SDK_ERROR, CameraError,  # noqa: F401
                                        MindVisionCamera, list_cameras)
