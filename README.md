# Lambton Camera Viewer

Shows the **FRONT and REAR line-scan cameras live**, with the **64 channel grid lines**
and **63 ejector LEDs** that light when something passes a channel.
No sorting, no valves are fired, no lights. It reads the cameras the same way
`lambton-main` does, but split into clean layers so it is easy to follow and to extend.

```
+--------------------------------------------------------------------------+
| FRONT CAMERA  SN 075002923007                              ● Streaming    |
| |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  ||  <- 1 frame, 16 lines x 3
|  1  2  3  4  5 ...                                                    63 |
| Frame height [16 lines v] Exposure [65 us] V-stretch [3x] [x] Grid  Detect: lower [68] upper [221] |
| 4096 x 16 px per frame | 550 frames/s | 8,800 lines/s | brightness 92    |
+--------------------------------------------------------------------------+
| REAR CAMERA ...                                                          |
+--------------------------------------------------------------------------+
| EJECTOR LEDs ■ both ■ FRONT only ■ REAR only   Delay [2.0 ms] Blow time [10.0 ms] |
| [1][2][3][4]...[16][17]...                                         [63]  |
| [Pause] [Save snapshot] [Save settings] [Logs] [Calibrate channels]      |
+--------------------------------------------------------------------------+
```

---

## 1. Quick start

| What | How |
|---|---|
| Show the cameras | close lambton-main first, then double-click `RUN_VIEWER.bat` (or `python main.py`) |
| Find camera serial numbers | `python tools\list_cameras.py` |

> ⚠️ A camera can be opened by **only one program at a time**.
> If lambton-main or MVDCP is running, the viewer shows *"Cannot open camera"* and retries every 3 seconds.
> This viewer does not switch the LED lights - they must already be ON.

## 2. First-time setup

1. Install **Python 3.10 – 3.13, 64-bit** and the **MindVision camera SDK**.
2. In `D:\lambtonblr`:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

(Or use lambton-main's Python: `D:\lambton-main\.venv\Scripts\python main.py`.)

## 3. The layers

```
            ┌──────────────────────────────────────────────┐
  UI        │ ui/   MainWindow, CameraPanel, CameraView    │  PyQt5. Shows things, passes your clicks on.
            └───────────────▲──────────────────────────────┘
                            │ frames, status, settings  /  "set exposure 120"
            ┌───────────────┴──────────────────────────────┐
  CORE      │ core/ CameraWorker, ChannelDetector, ...     │  Pure Python. NO PyQt5, NO SDK.
            └───────────────▲──────────────────────────────┘
                            │ open / read_frame / set_exposure / close
            ┌───────────────┴──────────────────────────────┐
  HARDWARE  │ hardware/ MindVisionCamera + sdk/            │  Only the camera SDK.
            └──────────────────────────────────────────────┘
  CONFIG      config/ settings.py  <->  camera_config.json     (used by main.py and the UI)
  main.py     connects the layers - contains no logic itself
```

**Rule: each layer only uses the layer directly below it.**
That is what makes future work easy:

| Future idea | Where it goes | What else changes |
|---|---|---|
| Better defect detection (colour, size, AI) | new detector in `core/` with the same `detect(frame)` | nothing in hardware or UI |
| Fire the real valves | new `hardware/ejector.py` + a core part that reads `worker.activity` | LEDs keep working as they are |
| Another camera brand | new file in `hardware/` with the same methods (`open`, `read_frame`, `set_frame_lines`, `set_exposure`, `get_current_settings`, `close`) | one line in `main.py` |
| Web page / no-window version | new UI that uses the same `core/` | nothing in core or hardware |
| Gain controls | `hardware/` method + `core/camera_worker.py` request key + a box in `ui/camera_panel.py` | — |

### Folder layout

```
lambtonblr/
├── main.py                    <- START HERE: connects the layers
├── camera_config.json         <- YOUR SETTINGS
├── config/
│   └── settings.py            <- load_config(), save_camera_settings()
├── hardware/
│   ├── mindvision_camera.py   <- open / read / change settings / close one camera
│   └── sdk/                   <- mvsdk.py + MVCAMSDK_X64.dll (copied unchanged from lambton-main)
├── core/
│   ├── camera_worker.py       <- background thread per camera, applies setting changes
│   ├── frame_store.py         <- holds the newest frame (thread-safe)
│   ├── channel_map.py         <- which pixels belong to which valve (equal or calibrated)
│   ├── object_finder.py       <- finds a thin wire's centre pixel (for calibration)
│   ├── channel_detector.py    <- which of the 63 channels has something in it (like lambton-main)
│   ├── channel_activity.py    <- remembers when each channel was last ON (LED hold time)
│   ├── ejector.py             <- ejector LEDs: ON after the delay, for the blow time
│   └── event_log.py           <- one row per ejection (seen at, LED ON at, delay set / measured)
├── ui/
│   ├── main_window.py         <- window, refresh timer, buttons
│   ├── camera_panel.py        <- one camera's box + controls
│   ├── camera_view.py         <- draws the frame at its real height + grid
│   ├── led_bar.py             <- the 63 ejector LEDs
│   ├── log_window.py          <- the "Logs" window
│   └── calibration_window.py  <- the "Calibrate channels" window
├── tools/list_cameras.py      <- prints all connected cameras
├── tests/                     <- automatic tests, no cameras needed (see section 10)
├── logs/                      <- "Export CSV" in the Logs window saves here
└── snapshots/                 <- "Save snapshot" puts PNG files here
```

## 4. What you see

**One frame, at the frame height you set.** The picture is exactly the newest
camera frame. Its height on screen = **frame lines × V-stretch**:

| Frame height | V-stretch | on screen |
|---|---|---|
| 16 lines | 3 | 48 pixels tall |
| 64 lines | 3 | 192 pixels tall |
| 128 lines | 3 | 384 pixels tall |

So when you change **Frame height**, the picture changes height the same way.
The width always fills the panel (4096 camera pixels squeezed into the window).
If a picture is too tall to fit, it is shrunk and the info line says *"picture shrunk to fit"*.

**64 grid lines = 63 channels (one per valve)**, evenly spaced from `start_pixel` to
`end_pixel` (same values as lambton-main: FRONT 19 → 4046, REAR 60 → 3981, ≈ 64 pixels per channel).
The two outer lines (chute edges) are red; channel numbers are under the picture.
Untick **Grid** to hide them.

## 5. The 63 ejector LEDs (with delay)

**Display only - no valves are fired, nothing is sent to the ESP32.**

### 5a. Detection - "the camera saw something"
Same "background" detection lambton-main uses (`evaluate_ejectors_instant`):

1. In every frame, look at the **blue** colour of each pixel column (the background is blue).
2. A column is a **hit** if any pixel is **darker than `lower`** or **brighter than `upper`**.
3. Only columns between `start_pixel` and `end_pixel` count; each column belongs to one of 63 channels.
4. A channel is ON when it has **at least 2 hit columns** (noise filter).

### 5b. Ejection - "the ejector LED switches ON"

```
 camera sees object ──── Delay ────► ejector LED ON ──── Blow time ────► LED OFF
     "seen at"                          "LED ON at"
```

- **Either camera** triggers (like lambton-main). FRONT and REAR seeing the same channel within
  150 ms (`same_object_ms`) = **one** object = **one** ejection.
- The delay is counted from the **first** camera that saw the object.
- **Delay** and **Blow time** are set at the right of the LED bar (type + Enter, or arrows).
  Starting values = lambton-main's active recipe RICE: delay **2 ms**, pulse **10 ms**.
- LED colour = who saw it: **red** both cameras, **orange** FRONT only, **cyan** REAR only.
- A 10 ms blow is too short to see on a screen that redraws every 40 ms, so each LED is
  **shown** at least 150 ms (`led_hold_ms`). The log has the real times.

### 5c. The decision line (red dotted line in each camera picture)

A camera frame is built line by line: the **top row is the oldest** line, the **bottom row is the
newest** line = "now". The **red dotted DECISION LINE** marks that newest line: the place where the
camera sees an object and the decision is made. The **Delay** starts from here; after the Delay the
ejector LED switches ON.

```
 camera picture:  ┌──────────────────────────────────────┐  <- oldest line
                  │      ███                             │
                  │      ███        (grid lines)         │
 DECISION LINE  - ┼ - - -███- - - - - - - - - - - - - - -┼  <- newest line = now
                  └──────────────────────────────────────┘
```

It is only shown: detection still checks **every** line of every frame, so no grain is missed.

**Choosing lower / upper:** the info line shows the **background level** of the empty chute
(about 93 with the current exposure). `lower` must be **below** it and `upper` **above** it.
If LEDs flicker with an empty chute, move them further away. Starting values are lambton-main's
(`bg_config.json`): FRONT 68 / 221, REAR 60 / 164.

## 6. Logs - when was it seen, when did the LED switch ON?

Click **Logs**. One row per ejection:

| # | Seen at | Seen by | Channel | LED ON at | Delay set | Delay measured | Difference | FRONT pixels | REAR pixels |
|---|---|---|---|---|---|---|---|---|---|
| 12 | 16:52:03.125 | BOTH | 16 | 16:52:03.127 | 2.0 | 2.1 | +0.1 | 1000 - 1060 | 1012 - 1071 |

- **Seen at** = PC time when the first camera's frame with the object was checked.
- **LED ON at** = PC time when the ejector LED switched ON.
- **Delay measured** = LED ON at − Seen at, measured with the PC's precise clock (`perf_counter`).
- **FRONT pixels / REAR pixels** = where each camera saw the object: first pixel - last pixel
  (`-` = that camera did not see it). The range covers the **whole object**, also when it touches two
  channels (then both rows show the same range). It grows while the object is still in view.
  REAR pixels are counted **after mirroring**, the same as the REAR picture on screen.
  Before calibration, FRONT and REAR pixels of one object can differ a little - that is normal;
  each camera has its own pixel numbers.
- **Difference** = measured − set = how late the PC was. On Windows expect about **0 … +1 ms**.
  The summary line shows the average and maximum difference.
- What the measurement does **not** include: the time between the object crossing the scan line
  and the PC receiving that frame (≈ one frame, 3.3 ms at 16 lines, plus network transfer).
- A row appears ~150 ms after the object was seen (then it is sure if one or both cameras saw it).
- Filters: **Channel**, **Seen by** (BOTH / FRONT / REAR). **Pause**, **Clear**, **Auto-scroll**.
- **Export CSV** → `logs\ejection_log_<date_time>.csv` (opens in Excel; times include the date).
- The newest 20,000 rows are kept in memory; not saved automatically - use **Export CSV**.

## 7. Calibrate channels - make FRONT and REAR agree

**The problem:** without calibration, each camera's chute (`start_pixel` … `end_pixel`) is cut into
**63 equal slices**. The valves are equally spaced, but each camera sees them through its own lens
and from its own side, so the slices of FRONT and REAR do not fall on exactly the same valves.
Result: an object in front of valve 3 is channel 3 in one camera but 2 or 4 in the other.
(lambton-main has the same equal rule; it planned the same calibration in `channel_map.py` but never ran it.)

**The fix:** click **Calibrate channels** (bottom of the main window).

1. Empty chute, lights ON → **Learn empty chute** (dust spots are then ignored).
2. Hold a **thin wire / needle** exactly in front of the **centre of valve 1**. The live line shows
   where each camera sees it (pixel → channel) and whether the cameras agree.
   Type the valve number → **Measure** (median of the last second, both cameras at once).
3. Repeat for valves **16, 32, 48, 63** (the number moves on by itself). More valves = better; at least 2.
4. **Apply** → used at once (grid lines and LEDs). Check: move the wire to other valves - the live line
   should now say **"Both cameras: same channel"**, and the right LED should light **red**.
5. **Save to camera_config.json** → used at every start (`channel_points` of each camera).

A smooth curve through the measured valves (pixel = a + b·valve + c·valve², like lambton-main)
corrects the lens: outer channels may become a few pixels wider or narrower than the middle ones.
The info line of each camera says **channels: CALIBRATED** or **equal (not calibrated)**.
**Back to equal channels** undoes it.

## 8. Controls

| Control | Changes | What it does |
|---|---|---|
| **Frame height** | camera | Lines per frame (8 … 1024). Applied to the camera immediately. |
| **Exposure** | camera | Microseconds. Higher = brighter (more blur). Type + **Enter**, or arrows. Range read from the camera. |
| **V-stretch** | screen only | Screen pixels per camera line. |
| **Grid** | screen only | Show / hide the channel lines. |
| **Detect: lower / upper** | LEDs only | Detection thresholds (0–255, blue). Applied on the next frame. |
| **Pause** | screen only | Freeze the pictures. |
| **Save snapshot** | — | Saves the newest frame of each camera, full size, as PNG in `snapshots/`. |
| **Delay** | LEDs | Time from "camera saw it" to "ejector LED ON" (section 5b). |
| **Blow time** | LEDs | How long the ejector LED stays ON. |
| **Logs** | — | Opens the ejection log (section 6). |
| **Calibrate channels** | channels | Measure the valves so both cameras agree (section 7). |
| **Save settings** | file | Writes frame height, exposure, V-stretch, lower/upper of both cameras, plus delay and blow time, into `camera_config.json` (old file kept as `.bak`). Without it, changes are lost when you close. |

The frame height and exposure shown are **read back from the camera**, so they are what it really uses.

## 9. Settings file `camera_config.json`

| Setting | Meaning |
|---|---|
| `display.refresh_fps` | Screen redraws per second (25). |
| `display.show_grid` | Grid on/off at start. |
| `channels` | Number of ejector channels (63). Grid lines = channels + 1 = 64. |
| `detection.colour` | Colour checked for detection: `BLUE` (like lambton-main's wave mode). |
| `detection.min_pixels` | Hit columns needed to switch a channel ON (2). |
| `detection.led_hold_ms` | An ejector LED is shown at least this long on screen (150). |
| `ejector.delay_ms` | Camera saw it → ejector LED ON (2). |
| `ejector.blow_ms` | How long the ejector LED stays ON (10). |
| `ejector.same_object_ms` | FRONT + REAR in one channel within this time = one object (150). |
| `cameras.*.lower`, `upper` | Detection thresholds. |
| `cameras.*.band_mode` | `true` = hit when the value stays INSIDE lower..upper (like lambton-main's band mode). |
| `cameras.*.serial` | Serial number of the camera. If FRONT/REAR look swapped, swap the two numbers. |
| `cameras.*.frame_lines` | Lines per frame, set on the camera at start. |
| `cameras.*.v_stretch` | Screen pixels per camera line. |
| `cameras.*.mirror` | Flip left↔right (REAR = true, like lambton-main). |
| `cameras.*.start_pixel`, `end_pixel` | Chute edges for **equal** channels (used when not calibrated). |
| `cameras.*.channel_points` | Calibration: `{"valve": pixel of its centre}`, written by **Calibrate channels**. Empty = equal channels. |
| `cameras.*.exposure_us`, `analog_gain`, `r_gain`, `g_gain`, `b_gain` | Written to the camera at start, like lambton-main. |

## 10. Tests (no cameras needed)

```bat
python tests\test_core.py     <- worker, frame height / exposure changes, grid, config
python tests\test_leds.py     <- channel detection gives the SAME channels as lambton-main
python tests\test_calibration.py <- simulated lenses: equal channels disagree, calibrated agree 100 %
python tests\test_ejector.py  <- delay / blow time, either camera, one ejection per object, log, CSV,
                                 and how exactly THIS PC keeps the delay (printed)
```

Each must print **ALL TESTS PASSED**. Run them after every change.

## 11. Send the code to GitHub

Double-click **`PUSH_TO_GITHUB.bat`**. It sends this folder to
<https://github.com/pandi7010/line_scanner_lambton> using **your** Git login on this PC
(the first time, a GitHub login window may open - log in there).
Run it again whenever you want to send the newest code.

Not sent (see `.gitignore`): `snapshots/`, `logs/`, `.venv/`, `__pycache__/`, `camera_config.json.bak`.
`camera_config.json` (serials, exposure, calibration) **is** sent.

## 12. Problems

| You see | Meaning | What to do |
|---|---|---|
| brightness 0–10, "very dark" | too little light reaches the camera | check LEDs; raise **Exposure** |
| no rows in Logs | nothing detected yet, or rows still waiting (~150 ms) | check the LED bar; see lower/upper |
| one object = channel 3 in one camera, 2 or 4 in the other | channels not calibrated | **Calibrate channels** (section 7) |
| calibration: "no object found" | wire too thin / too faint | thicker or darker wire; **Learn empty chute** first |
| big "Difference" in the log | PC busy (other programs, many objects) | close other programs; check average, not one row |
| ejector LEDs on with an empty chute | lower/upper too close to the background level | lower `lower` / raise `upper` (see section 5) |
| ejector LEDs never on | lower/upper too far away | move them closer to the background level |
| `4096 x 10000 px`, ~1 frame/s | frame height too big | choose a smaller **Frame height** |
| red status "not found" | camera not visible | cable / power / serial number; `tools\list_cameras.py` |
| red status "Cannot open camera" | another program has it | close lambton-main / MVDCP |
