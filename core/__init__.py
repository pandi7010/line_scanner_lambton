"""
CORE LAYER - the "brain" between the hardware and the screen.

  camera_clock.py  -> camera timestamps -> exact time of every camera line
  camera_worker.py -> reads one camera in a background thread, applies
                      setting changes, keeps status + statistics
  frame_store.py   -> holds the newest frame (safe between threads)
  channel_map.py   -> which pixels belong to which channel (equal, or calibrated on the valves)
  object_finder.py -> finds a thin object's centre pixel (for calibration)
  channel_detector.py -> which of the 63 channels has something in a frame
  channel_activity.py -> remembers when each channel was last ON (LED hold time)
  ejector.py       -> ejector LEDs: ON after the delay, for the blow time (either camera)
  event_log.py     -> one row per ejection: seen at, LED ON at, delay set / measured

Rules for this layer:
  * NO PyQt5 here. The core works without any window, so later a sorting
    engine, a recorder or a test script can use it exactly the same way.
  * It does not import the SDK either: it gets a camera object and only
    calls open / read_frame / set_frame_lines / set_exposure / close on it.
"""

from core.camera_clock import CameraClock  # noqa: F401
from core.camera_worker import CameraWorker  # noqa: F401
from core.channel_activity import ChannelActivity  # noqa: F401
from core.channel_detector import ChannelDetector  # noqa: F401
from core.ejector import EjectScheduler  # noqa: F401
from core.event_log import EjectEvent, EventLog, clock_text, write_csv  # noqa: F401
from core.channel_map import ChannelMap  # noqa: F401
from core.object_finder import column_profile, find_object  # noqa: F401
from core.frame_store import FrameStore  # noqa: F401
