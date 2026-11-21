"""
tools/line_rate_test.py - Does a SLOWER line rate reduce line / frame loss? (fully automatic)

    Double-click RUN_LINE_RATE_TEST.bat   (close the viewer first - a camera can be used by one program only)
    or: .venv\\Scripts\\python.exe tools\\line_rate_test.py               (rates: max 6000 4800 4000 3000 2000)
        .venv\\Scripts\\python.exe tools\\line_rate_test.py 5000 2500     (your own rates; 0 = max)

What it does, for BOTH cameras at the same time:
  1. opens the cameras like the viewer (same settings from camera_config.json)
  2. remembers the line rate the cameras have now
  3. for every line rate: set it, wait 2 s, run the Frame check (1000 frames), keep the result
  4. puts the ORIGINAL line rate back (and checks it) - also if something goes wrong
  5. writes ONE table + all details to logs\\line_rate_test_<date_time>.txt
All camera settings are backed up to camera_backups\\ before the first change.
No valve is fired, no window opens.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PROJECT_DIR, load_config  # noqa: E402
from core import CameraClock, CameraWorker, ChannelDetector, ChannelMap  # noqa: E402
from hardware import SDK_AVAILABLE, SDK_ERROR, MindVisionCamera  # noqa: E402

FRAMES = 1000
SETTLE_S = 2.0


def make_workers(config):
    workers = {}
    for name, cam_cfg in config["cameras"].items():
        cfg = dict(cam_cfg)
        cfg.pop("line_rate", None)                    # do not change the line rate at open
        camera = MindVisionCamera(str(cfg["serial"]).strip(), frame_lines=int(cfg["frame_lines"]),
                                  sdk_buffers=int(cfg["sdk_buffers"]), settings=cfg,
                                  backup_dir=os.path.join(PROJECT_DIR, "camera_backups"))
        detector = ChannelDetector(ChannelMap.from_config(cfg, config["channels"]), lower=cfg["lower"],
                                   upper=cfg["upper"], band_mode=cfg["band_mode"],
                                   colour=config["detection"]["colour"],
                                   min_pixels=config["detection"]["min_pixels"])
        clock = CameraClock(default_line_rate_hz=config["clock"]["default_line_rate_hz"],
                            offset_window_frames=config["clock"]["offset_window_frames"],
                            use_camera_timestamp=config["clock"]["use_camera_timestamp"])
        workers[name] = CameraWorker(name, camera, mirror=bool(cfg["mirror"]), detector=detector, clock=clock,
                                     end_gap=config["ejector"]["end_gap_lines"], snap_seconds=0)
    return workers


def line_rate(worker):
    settings, _ = worker.get_settings()
    return (settings or {}).get("line_rate")


def wait_for(condition, seconds):
    t_end = time.time() + seconds
    while time.time() < t_end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def set_rate(workers, rate):
    for w in workers.values():
        w.request_setting("line_rate", rate)
    ok = wait_for(lambda: all(line_rate(w) == rate or (rate <= 0 and (line_rate(w) or 0) <= 0)
                              for w in workers.values()), 5)
    time.sleep(SETTLE_S)
    return ok


def main():
    rates = [int(x) for x in sys.argv[1:]] or [0, 6000, 4800, 4000, 3000, 2000]
    if not SDK_AVAILABLE:
        print(f"MindVision SDK not available: {SDK_ERROR}")
        return 1
    config = load_config()
    workers = make_workers(config)
    for w in workers.values():
        w.start()
    print("Opening the cameras ...")
    if not wait_for(lambda: all(w.frame_count > 50 for w in workers.values()), 20):
        print("A camera does not stream. Is the viewer / lambton-main closed?")
        for name, w in workers.items():
            print(f"  {name}: {w.get_status()[0]}")
        for w in workers.values():
            w.stop()
        return 1
    wait_for(lambda: all(line_rate(w) is not None for w in workers.values()), 5)
    original = {name: line_rate(w) for name, w in workers.items()}
    print("Line rate now: " + ", ".join(f"{n} {v if v and v > 0 else 'max'}" for n, v in original.items()))

    results = []
    try:
        for rate in rates:
            label = "max" if rate <= 0 else f"{rate} lines/s"
            print(f"\n--- {label}: setting it, waiting {SETTLE_S:.0f} s, then {FRAMES} frames ...")
            if not set_rate(workers, rate):
                print("   the camera did not take this line rate - skipped")
                results.append((rate, None))
                continue
            checks = {name: w.start_frame_check(FRAMES) for name, w in workers.items()}
            if not wait_for(lambda: all(c.done for c in checks.values()), 10 + FRAMES / 50):
                print("   not enough frames came - skipped")
                results.append((rate, None))
                continue
            results.append((rate, checks))
            for name, c in checks.items():
                r = c.report()
                print(f"   {name}: lines lost {r['lines_missed']} ({r['lines_missed_pct']:.2f} %), "
                      f"frames lost {r['missed_frames_camera_clock']}, line time {r['line_time_us']:.1f} us, "
                      f"processing median {r['proc_median_ms']:.2f} ms")
    finally:
        print("\nPutting the original line rate back ...")
        for name, w in workers.items():
            w.request_setting("line_rate", original[name] if original[name] is not None else 0)
        wait_for(lambda: all(line_rate(w) == original[n] for n, w in workers.items()), 5)
        restored = {name: line_rate(w) for name, w in workers.items()}
        for w in workers.values():
            w.stop()

    # ---- one table + details
    lines = [f"Line rate test {time.strftime('%Y-%m-%d %H:%M:%S')} - {FRAMES} frames per camera per line rate",
             "Original line rate: " + ", ".join(f"{n} {v if v and v > 0 else 'max'}" for n, v in original.items())
             + "   restored: " + ", ".join(f"{n} {'OK' if restored[n] == original[n] else 'CHECK! now ' + str(restored[n])}"
                                          for n in original),
             "",
             f"{'line rate':>10} {'line time':>10} {'camera':>6} {'lines lost':>12} {'lost %':>7} {'frames lost':>11} "
             f"{'proc median':>11} {'proc 99%':>9} {'proc max':>9} {'over budget':>11} {'frames/s':>9}"]
    for rate, checks in results:
        label = "max" if rate <= 0 else str(rate)
        if checks is None:
            lines.append(f"{label:>10}  skipped")
            continue
        for name, c in checks.items():
            r = c.report()
            lines.append(f"{label:>10} {r['line_time_us']:>8.1f}us {name:>6} {r['lines_missed']:>12,} "
                         f"{r['lines_missed_pct']:>6.2f}% {r['missed_frames_camera_clock']:>11} "
                         f"{r['proc_median_ms']:>9.2f}ms {r['proc_p99_ms']:>7.2f}ms {r['proc_max_ms']:>7.2f}ms "
                         f"{r['over_budget']:>11} {r['fps']:>9.1f}")
    lines += ["", "Details per line rate:", ""]
    for rate, checks in results:
        if checks:
            lines.append(f"########## line rate {'max' if rate <= 0 else rate}")
            lines += [c.text() + "\n" for c in checks.values()]
    text = "\n".join(lines)
    folder = os.path.join(PROJECT_DIR, "logs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("line_rate_test_%Y%m%d_%H%M%S.txt"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + "\n".join(lines[:4 + 2 * len(results)]))
    print(f"\nSaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
