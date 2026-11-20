"""
tests/test_objects.py - automatic test, NO cameras needed.

    python tests\\test_objects.py      -> must print  ALL TESTS PASSED

Checks the "Object #" in the log (rows of the same object share one number):
  * one object in 2 neighbour channels, seen by both cameras = 1 object
  * two objects far apart in time, same channel = 2 objects
  * two objects at the same time, far apart on the chute = 2 objects
  * a one-camera "edge" row right after the object = same object
  * Clear starts the numbers again at 1
  * the real 10-object drop of 20 Nov 2026 (31 rows) gives a sensible count
  * CSV has the object_no column
"""
import csv
import os
import sys
import tempfile

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

from core import EventLog, write_csv  # noqa: E402

failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


def add(log, t, ch, length=4.0, seen_by="BOTH"):
    log.add(seen_time=t, seen_by=seen_by, channel=ch, found_time=t + 0.004, on_time=t + 0.010,
            delay_set_ms=10, delay_ms=10, late_ms=0, result="ON TIME", length_ms=length)


def numbers(log):
    return [e.object_no for e in log.events_since(0)]


print("Object numbers:")
log = EventLog(object_join_ms=5)
add(log, 100.000, 17)
add(log, 100.0015, 18, length=2.5, seen_by="REAR")          # edge of the same object
check("object in channels 17 + 18 = 1 object", numbers(log) == [1, 1])

add(log, 100.200, 17)                                         # same channel, 200 ms later
check("same channel 200 ms later = new object", numbers(log)[-1] == 2)

add(log, 100.400, 10)
add(log, 100.400, 50)                                         # same moment, far apart
check("same moment, channels 10 and 50 = 2 objects", numbers(log)[-2:] == [3, 4])

add(log, 100.4060, 10, length=1.0, seen_by="FRONT")           # 2 ms after object 3 ended
check("one-camera row just after the object = same object", numbers(log)[-1] == 3)

add(log, 100.4200, 11)                                        # 16 ms later = clearly another one
check("neighbour channel 16 ms later = new object", numbers(log)[-1] == 5)
check("object_count = 5", log.object_count == 5)

log.clear()
add(log, 101.0, 30)
check("after Clear the numbers start at 1 again", numbers(log) == [1])

# --- the real drop of 10 objects (20 Nov 2026, 11:24:15) ----------------------
# (seconds after 11:24:15, channel, object length ms) - from ejection_log_20261120_112517.csv
REAL = [(.8700, 17, 4.0), (.8715, 18, 2.5), (.9107, 15, 5.3), (.9131, 14, 2.9), (.9162, 15, 1.2),
        (.9164, 16, 1.0), (.9559, 19, 4.9), (.9559, 20, 4.9), (.9614, 19, 1.0), (.9662, 21, 2.2),
        (.9668, 20, 1.0), (.9708, 21, 1.9), (.9800, 21, 4.4), (.9800, 22, 4.4), (1.0246, 22, 4.9),
        (1.0246, 23, 4.9), (1.0266, 23, 3.0), (1.0276, 22, 2.0), (1.0693, 18, 6.1), (1.0693, 19, 3.8),
        (1.0735, 17, 5.8), (1.0750, 18, 0.8), (1.0768, 18, 3.4), (1.1006, 16, 2.6), (1.1042, 16, 4.4),
        (1.1058, 15, 2.7), (1.1092, 16, 0.0), (1.1241, 12, 5.1), (1.1241, 13, 5.1), (1.1281, 12, 1.0),
        (1.1283, 13, 0.8)]
log = EventLog(object_join_ms=5)
for t, ch, length in REAL:
    add(log, 1000 + t, ch, length)
count = log.object_count
print(f"  real drop: 31 rows -> {count} objects  {numbers(log)}")
check("real drop: 31 rows grouped into 8..10 objects", 8 <= count <= 10)

# --- 8 objects dropped one by one (20 Nov 2026, 12:16): one object came in 2 pieces 3.4 ms apart
log8 = EventLog(object_join_ms=5)
for t, ch, length in [(12.9579, 62, 5.5), (12.9579, 63, 5.5), (18.0125, 55, 7.6), (23.6044, 2, 3.7),
                      (30.3885, 16, 6.8), (34.8754, 31, 0.7), (34.8754, 32, 0.7), (34.8795, 31, 2.7),
                      (34.8795, 32, 2.7), (38.3030, 48, 6.6), (41.8611, 61, 6.5), (47.9181, 2, 4.2)]:
    add(log8, 2000 + t, ch, length)
check(f"8 objects dropped -> {log8.object_count} counted (2 pieces 3.4 ms apart = 1 object)",
      log8.object_count == 8)

path = os.path.join(tempfile.mkdtemp(), "log.csv")
write_csv(path, log.events_since(0))
with open(path, newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
check("CSV first column is object_no", rows[0][0] == "object_no" and rows[1][0] == "1")
check("CSV has 31 rows + header", len(rows) == 32)

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
