"""
breadcrumb.py -- volatile "what was I doing" marker in RTC RAM.

Survives deep sleep but is CLEARED on power loss / brownout, which is exactly
how safemode.py distinguishes "crashed with power intact" (breadcrumb=ok) from
"power actually dropped" (breadcrumb=LOST).

Entirely optional: without breadcrumbs you still get reason + consecutive count
from safemode.py; you just won't get phase, and every record reads
breadcrumb=LOST.  Add beat() calls incrementally, wherever a crash is most
likely (I2C bring-up, WiFi, AIO send).

Requires alarm.sleep_memory (ESP32-S2/S3 etc.).  Guard the import on boards
that lack it.

Layout in alarm.sleep_memory[0:8] -- MUST match what safemode.py reads:
    [0]     magic 0x5A
    [1]     phase
    [2:4]   free KiB (LE)
    [4:8]   uptime seconds (LE)
"""

import alarm
import gc
import time

# Phase codes come from crash_log so the two files can't drift.
from crash_log import (
    PH_IDLE, PH_BOOT, PH_I2C, PH_SENSOR, PH_WIFI, PH_AIO, PH_LOOP,
)

_HB = alarm.sleep_memory
_MAGIC = 0x5A


def hb_init():
    """Re-arm the breadcrumb for this run.  Call once, early in boot."""
    _HB[0] = _MAGIC
    _HB[1:8] = b"\x00" * 7


def beat(phase):
    """Drop a breadcrumb right before entering a subsystem block."""
    _HB[1] = phase
    _HB[2:4] = min(gc.mem_free() // 1024, 0xFFFF).to_bytes(2, "little")
    _HB[4:8] = (int(time.monotonic()) & 0xFFFFFFFF).to_bytes(4, "little")
