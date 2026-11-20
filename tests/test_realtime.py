"""
tests/test_realtime.py - automatic test, NO cameras needed.

    python tests\\test_realtime.py      -> must print  ALL TESTS PASSED

Checks the "high priority" switch (core/realtime.py):
  * ON  -> on Windows the thread gets priority HIGH (on other systems: no crash)
  * OFF -> the thread stays normal
  * the status text for the status bar
  * the ejector + camera threads call it when they start
"""
import os
import sys
import threading

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

from config import load_config  # noqa: E402
from core import realtime  # noqa: E402

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


def in_thread(name):
    out = []
    t = threading.Thread(target=lambda: out.append(realtime.boost_this_thread(name)))
    t.start()
    t.join()
    return out[0]


windows = os.name == "nt"
print("Windows:", windows)

config = load_config()
check("config has realtime switches", set(config["realtime"]) >= {"high_priority", "timer_1ms"})

realtime.configure(high_priority=True, timer_1ms=True)
result = in_thread("test ON")
check(f"switch ON  -> {result}", result == "HIGH" if windows else result.startswith("normal"))

realtime.configure(high_priority=False, timer_1ms=False)
result = in_thread("test OFF")
check(f"switch OFF -> {result}", result == "normal")

text = realtime.status_text()
print("  status bar text:", text)
check("status text names the threads and the timer", "test ON" in text and "timer" in text)

src = open("core/ejector.py").read() + open("core/camera_worker.py").read()
check("ejector + camera threads boost themselves",
      src.count("realtime.boost_this_thread(") == 2)

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
