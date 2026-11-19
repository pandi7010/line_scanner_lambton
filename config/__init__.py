"""
CONFIG LAYER - reading and writing camera_config.json.

  settings.py -> load_config(), save_camera_settings(), paths

Every other layer gets its numbers from here; nobody else opens the JSON file.
"""

from config.settings import (CONFIG_FILE, PROJECT_DIR, SNAPSHOT_DIR,  # noqa: F401
                             load_config, save_camera_settings)
