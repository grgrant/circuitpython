"""
crash_log.py -- read, format, and clear the crash / safe-mode record that
safemode.py leaves in NVM.  Replaces the old read_safemode.py.

Deploy alongside safemode.py in the CIRCUITPY root (or lib/).  Depends only on
`microcontroller`, so it is safe to import from anywhere -- including a minimal
code.py used for board bring-up/debugging (e.g. the QtPy).

NVM byte map -- MUST stay in sync with safemode.py:
    0          reset counter        (owned by code.py; we NEVER touch it)
    1..7       reserved for code.py
    CRASH_BASE (=8) .. crash-capture region written by safemode.py:
      +0       magic 0xA5
      +1       reason code (0 = unknown, else index into REASON_NAMES)
      +2       consecutive count (safemode.py increments while magic present)
      +3       flags: 0x00 normal / 0xEE safemode.py itself raised
      +4       phase  (last breadcrumb; 0xFF if none)
      +5       hb_valid: 1 = RTC breadcrumb survived, 0 = lost (power/brownout)
      +6..+7   free KiB at last breadcrumb (LE)
      +8..+11  uptime seconds at last breadcrumb (LE)
      +12      (error record only) error-text length
      +13..    (error record only) error text

The breadcrumb itself lives in alarm.sleep_memory (see breadcrumb.py), NOT nvm.
"""

import microcontroller

CRASH_BASE = 8          # <<< MUST equal CRASH_BASE in safemode.py >>>

_MAGIC = 0xA5
_FLAG_SMERR = 0xEE

REASON_NAMES = (
    "UNKNOWN", "BROWNOUT", "WATCHDOG", "HARD_FAULT", "NLR_JUMP_FAIL",
    "GC_ALLOC_OUTSIDE_VM", "NO_HEAP", "STACK_OVERFLOW", "INTERRUPT_ERROR",
    "SDK_FATAL_ERROR", "PROGRAMMATIC",
)

# Phase codes -- shared with breadcrumb.py (single source of truth so the two
# files can't drift the way the reader/writer did).
PH_IDLE, PH_BOOT, PH_I2C, PH_SENSOR, PH_WIFI, PH_AIO, PH_LOOP = 0, 1, 2, 3, 4, 5, 6
PHASE_NAMES = {
    PH_IDLE: "idle", PH_BOOT: "boot", PH_I2C: "i2c", PH_SENSOR: "sensor",
    PH_WIFI: "wifi", PH_AIO: "aio", PH_LOOP: "loop", 0xFF: "?",
}


def crash_pending():
    """True if safemode.py left an unread record."""
    return microcontroller.nvm[CRASH_BASE] == _MAGIC


def report_crash():
    """
    Return a formatted incident string, or None if there is no record.

    Does NOT clear the record -- call clear_crash_record() only once the board
    is confirmed healthy, so safemode.py's consecutive-count backoff (it stops
    auto-resetting after 5) stays meaningful across a crash loop.
    """
    nvm = microcontroller.nvm
    if nvm[CRASH_BASE] != _MAGIC:
        return None

    flags = nvm[CRASH_BASE + 3]
    if flags == _FLAG_SMERR:
        n = nvm[CRASH_BASE + 12]
        if n > 59:                      # stale/garbage length guard
            n = 0
        try:
            msg = bytes(nvm[CRASH_BASE + 13:CRASH_BASE + 13 + n]).decode()
        except Exception:
            msg = "<undecodable>"
        return "safemode.py error: " + msg

    reason = nvm[CRASH_BASE + 1]
    name = REASON_NAMES[reason] if reason < len(REASON_NAMES) else "UNKNOWN(%d)" % reason
    count = nvm[CRASH_BASE + 2]
    phase = PHASE_NAMES.get(nvm[CRASH_BASE + 4], nvm[CRASH_BASE + 4])
    free_k = nvm[CRASH_BASE + 6] | (nvm[CRASH_BASE + 7] << 8)
    uptime = (nvm[CRASH_BASE + 8] | (nvm[CRASH_BASE + 9] << 8)
              | (nvm[CRASH_BASE + 10] << 16) | (nvm[CRASH_BASE + 11] << 24))
    hb = "ok" if nvm[CRASH_BASE + 5] == 1 else "LOST"
    return "%s x%d | phase=%s | free=%dk | uptime=%ds | breadcrumb=%s" % (
        name, count, phase, free_k, uptime, hb)


def clear_crash_record():
    """
    Clear the crash magic.  Call ONLY after the board reaches a known-healthy
    state (WiFi + AIO + startup log), mirroring your reset-counter clear.
    Resets safemode.py's consecutive-count to zero.
    """
    microcontroller.nvm[CRASH_BASE] = 0x00
