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
│   ├── camera_clock.py        <- camera timestamps -> exact time of every camera line
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
 object touches the decision line ──── Delay ────► ejector LED ON ── until last line + Delay + Blow ──► OFF
   "Touched line at" (camera clock)                  "LED ON at"
```

**Exact touch time (camera clock).** The PC gets a frame a few ms after its lines were scanned
(the frame must be full, then it travels over the network). So the software does **not** use
"when the PC saw it". It uses the **camera's own clock** in every frame plus the **line number**:

- camera clock counts in 0.1 ms steps; line time = (time between frames) / (lines per frame)
- camera clock → PC clock with the **fastest** frame of the last 300 (Windows/network delays only add)
- line r of a frame with N lines was scanned at  *frame end − (N − 1 − r) × line time*

The info line also shows **lost frames** (from the camera SDK's counter and from gaps in the camera
clock): **0 in the last second** is required - a lost frame can hide an object. The program lets Python
switch threads every 0.5 ms (like lambton-main), so a busy thread cannot hold up the cameras.

This is the same method as lambton-main `capture_timing.py`. Only the **fastest** transfer time
(well under 1 ms on GigE) cannot be seen by any clock. The info line of each camera says
**line times: CAMERA CLOCK** when this works.

**Line by line.** Every camera line is checked. An object **starts** at its first line and **ends**
after 8 empty lines (`end_gap_lines`, about 1.7 ms). So two grains in one channel with 9+ background lines between
them are **two objects** - even inside one frame.

**When does the LED switch ON?**
- Planned: **touched + Delay**. If the PC is in time, the LED switches ON exactly then → **ON TIME**.
- If the PC finds the object only after that moment → LED ON at once → **LATE** (the log stores how late).
- If the PC finds it after the whole blow window is over → **no LED** → **MISSED**
  (a blast then would only hit the wrong grain - same rule as lambton-main).
- **Rule:** the Delay must be **longer than the PC reaction** (frame filling + transfer + Windows).
  The Logs window shows the reaction for every object and its maximum.

**Blow time follows the object:** LED OFF = object's last line + Delay + Blow time. Long objects get longer blows.

**Either camera** triggers. FRONT and REAR touching the same channel within **30 ms** (real camera times,
`same_object_ms`) = **one** object. The delay starts at the camera that touched **first**.

LED colour = who saw it: **red** both cameras, **orange** FRONT only, **cyan** REAR only.
A 10 ms blow is too short to see on a 25 Hz screen, so each LED is **shown** at least 150 ms.

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

## 6. Logs - when did it touch the line, when did the LED switch ON?

Click **Logs**. One row per **channel** of an object. An object about one channel wide touches
2 channels and gives 2 rows (or more). **Object #** gives all rows of the same object one number:

| Object # | Touched line at | Seen by | Ch | PC saw at | PC reaction | LED ON at | Delay set | Delay measured | Late by | Result | Object | FRONT pixels | REAR pixels |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16:52:03.1250 | BOTH | 16 | 16:52:03.1281 | 3.1 | 16:52:03.1350 | 10.0 | 10.0 | 0.0 | ON TIME | 1.0 | 1000 - 1060 | 1012 - 1071 |
| 2 | 16:52:04.5000 | FRONT | 30 | 16:52:04.5112 | 11.2 | 16:52:04.5112 | 10.0 | 11.2 | 1.2 | LATE | 0.6 | 1890 - 1925 | - |

(times in 0.1 ms, all other numbers in ms)

- **Object #** = rows in the same or a neighbour channel, within 5 ms (`object_join_ms`) of each other,
  belong to one object. Two objects falling very close together (less than 5 ms apart, side by side)
  can get one number - drop test objects a little apart.
- **Touched line at** = exact moment the object touched the decision line (camera clock).
- **PC saw at / PC reaction** = when the program found it, and how long that took after the touch.
- **LED ON at** = when the ejector LED switched ON (empty for MISSED).
- **Delay measured** = LED ON at − Touched line at.
- **Late by** = LED ON at − planned LED ON (Touched + Delay set). **0.0 = exactly on time.**
- **Result**: **ON TIME** (late ≤ 1 ms, `late_tolerance_ms`), **LATE**, or **MISSED** (no LED).
- **Object** = time from the object's first to its last line. **FRONT / REAR pixels** = where each camera saw it.
- Summary line: **rows = objects** (e.g. "31 rows = 10 objects"), **ON TIME / LATE (max) / MISSED** counts, and the **PC reaction average
  and maximum**. Your Delay must be longer than the maximum reaction.
- Filters: **Channel**, **Seen by**, **Result**. **Pause**, **Clear**, **Auto-scroll**.
- **Export CSV** → `logs\ejection_log_<date_time>.csv` with all these columns (times include the date).
- The newest 20,000 rows are kept in memory; not saved automatically - use **Export CSV**.
- **Object snap:** click a row -> on the right you see the **picture of that object** from FRONT and REAR
  (object + 30 pixels left/right, + 8 lines before/after; green marks = where the program saw it).
  A camera that did **not** see it shows the picture at the other camera's place (orange marks) - so you can
  see WHY (too small, too light ...). **Save picture** -> `snapshots\`. **Export CSV** also saves all pictures
  into `logs\ejection_log_<time>_snaps\`. Pictures are kept for the newest 2,000 rows (`snap.keep`).

**Test with 10 objects:** Clear the log, drop 10 objects one by one → the summary should say
**"… rows = 10 objects"** (an object touching 2 channels = 2 rows with the same Object #), all **ON TIME** if the Delay is long enough.

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

## 7b. ESP32 Ejector - the real valves

Firmware on the board: lambton-main `esp/esp.ino` (byte protocol: byte 17 = valve 17 ON, 117 = OFF,
`S` = all OFF, `P` = "are you there?", `W` = board watchdog: a valve closes by itself after 250 ms).
Needs `pip install pyserial`. **Close lambton-main first** - a COM port can be used by one program only.

Click **ESP32 Ejector**:
1. **Find ESP32** (or choose the port yourself) -> **Connect**. Tries 230400, then 115200 baud.
   After connecting: all valves OFF + board watchdog ON. The port is remembered in `camera_config.json`.
2. **Test by hand:** Valve + Pulse (20 ms) -> **Pulse valve**, or **Walk 1 -> 63** (every valve once).
   First with the **air OFF** (listen for the click / watch the driver LEDs), then with air ON.
   Check that valve 17 really is the 17th nozzle from the left.
3. **Fire valves from the cameras** (OFF at every start): every ejector LED now also opens the real valve,
   with the same delay and blow time. The LED title says **VALVES FIRE**, the log column **Valve = FIRED**,
   the CSV column `valve_fired = YES`.
4. **ALL VALVES OFF** (red) = everything off and firing switched off. Disconnect / closing the program also
   switch all valves off.

Long objects: an open valve gets its ON re-sent every 100 ms, so the 250 ms board watchdog does not close it.

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
| **Delay** | LEDs | Time from "object touched the decision line" to "ejector LED ON" (section 5b). |
| **Blow time** | LEDs | LED stays ON until the object's last line + Delay + Blow time. |
| | | Delay + Blow time work **live** (next object) and are **saved automatically** 0.5 s after the last click ("saved 14:55:03" next to the boxes) - also while the feeder runs. |
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
| `ejector.delay_ms` | Object touched the decision line → ejector LED ON (10). |
| `ejector.blow_ms` | LED stays ON until the object's last line + delay + this (10). |
| `ejector.same_object_ms` | FRONT + REAR in one channel within this time (camera times) = one object (30). |
| `ejector.late_tolerance_ms` | Up to this much late still counts as ON TIME (1.0). |
| `ejector.end_gap_lines` | An object ends after this many empty lines (8 = about 1.7 ms; 2 split one object into pieces). |
| `ejector.object_join_ms` | Log only: rows within this time in neighbour channels = one Object # (5; 3 counted 8 drops as 9 on 20 Nov). |
| `snap.enabled` / `history_s` / `keep` | Object pictures in the Logs: on/off, seconds of frames kept (0.6 = ~35 MB per camera), rows that keep a picture (2000). |
| `realtime.high_priority` | Windows: the 2 camera threads + the ejector thread run FIRST when the PC is busy (fewer LATE). Status bar shows `Priority: FRONT camera HIGH ...`. `false` = off. |
| `realtime.timer_1ms` | Windows: 1 ms timer instead of 15.6 ms, so short waits are exact. `false` = off. |
| `clock.use_camera_timestamp` | `true` = exact line times from the camera clock (recommended). |
| `clock.default_line_rate_hz` | Only used until the real line rate is measured (4800). |
| `clock.offset_window_frames` | Camera → PC clock uses the fastest frame of the last N frames (300). |
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
python tests\test_ejector.py  <- camera clock, line by line, ON TIME / LATE / MISSED, first camera,
                                 blow by length, log, CSV
python tests\test_timing.py   <- REAL-TIME: 2 fake cameras with camera clocks + random delays drop
                                 10 objects: 10 rows, delay 10 ms all ON TIME, 2 ms all LATE (~4 s)
python tests\test_objects.py  <- Object #: rows of one object share a number (real drops of 20 Nov)
python tests\test_realtime.py <- high priority switch
python tests\test_esp32.py    <- ESP32 valves with a fake COM port: bytes, timing, watchdog re-send, ALL OFF
python tests\test_snaps.py    <- object pictures for the Logs
```

**Feeder load test** (real time, ~10 s): `python tools\stress_test.py 10 190` - 190 random grains per
second through both fake cameras, ejector armed on a fake ESP32, pictures on. Shows ON TIME / LATE /
MISSED and the CPU of every part. Goal: "found in time but NOT on time" = 0 (or a few, late by ~1 ms).

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
| many LATE rows | Delay shorter than the PC reaction | raise the Delay above the max PC reaction (Logs summary) |
| MISSED rows | PC was very late (busy) | close other programs; raise the Delay |
| info line says "line times: PC clock" | camera gives no timestamp | times are then less exact (about one frame) |
| info line: "lost frames: … N in the last second <- PC TOO SLOW!" | the PC did not take every frame in time - objects can be missed | close other programs; use a bigger frame height (e.g. 32 lines = half as many frames) |
| frames/s lower than the camera's line rate / frame height | the same: frames are being lost | as above |
| ejector LEDs on with an empty chute | lower/upper too close to the background level | lower `lower` / raise `upper` (see section 5) |
| ejector LEDs never on | lower/upper too far away | move them closer to the background level |
| `4096 x 10000 px`, ~1 frame/s | frame height too big | choose a smaller **Frame height** |
| red status "not found" | camera not visible | cable / power / serial number; `tools\list_cameras.py` |
| red status "Cannot open camera" | another program has it | close lambton-main / MVDCP |
