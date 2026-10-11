# SPDX-FileCopyrightText: Copyright (c) 2026 Bob Grant for grgrant
#
# SPDX-License-Identifier: MIT
"""
nvmflags.py - Shared NVM flag management
 Stores persistent flags in the last 8 bytes of microcontroller NVM.

 Layout (offsets from end of NVM):
   nvm[-8] = 0xA6 (magic byte 0)
   nvm[-7] = 0x53 (magic byte 1)
   nvm[-6] = version (layout version, currently 1)
   nvm[-5] = boot/repl flags (byte 0)
   nvm[-4] = reserved (byte 1) — e.g. code.py
   nvm[-3] = reserved (byte 2)
   nvm[-2] = reserved (byte 3)
   nvm[-1] = reserved (byte 4)

 Magic bytes chosen with a mix of set/unset bits to avoid matching
 uninitialized NVM (all 0x00 on Espressif, all 0xFF on RP2040).
"""

# --- Block layout ---
_BLOCK_SIZE = 8
_MAGIC_0    = 0xA6  # 0b10100110 — good bit mix
_MAGIC_1    = 0x53  # 0b01010011 — good bit mix
_VERSION    = 1

# Offsets from end of NVM (negative indices)
_OFF_MAGIC_0 = -8
_OFF_MAGIC_1 = -7
_OFF_VERSION = -6
_OFF_BOOT    = -5  # boot/repl flags
# -4 through -1 reserved

# --- Boot/repl flag bit masks (for nvm[_OFF_BOOT]) ---
DISABLE_CIRCUITPY     = 0x01  # bit 0
SUPERVISOR_AUTORELOAD = 0x02  # bit 1
# bits 2-7 available

# Human-readable names keyed by mask
FLAG_NAMES = {
    DISABLE_CIRCUITPY:     "DISABLE_CIRCUITPY",
    SUPERVISOR_AUTORELOAD: "SUPERVISOR_AUTORELOAD",
}


def _check_nvm(nvm):
    """Return True if NVM block is valid, False if we had to initialize."""
    if (nvm[_OFF_MAGIC_0] == _MAGIC_0
            and nvm[_OFF_MAGIC_1] == _MAGIC_1
            and nvm[_OFF_VERSION] == _VERSION):
        return True
    # Initialize entire block
    nvm[_OFF_MAGIC_0] = _MAGIC_0
    nvm[_OFF_MAGIC_1] = _MAGIC_1
    nvm[_OFF_VERSION] = _VERSION
    nvm[_OFF_BOOT] = 0x00
    for i in range(-4, 0):  # nvm[-4] through nvm[-1]
        nvm[i] = 0x00
    return False


def read_flags(nvm, offset=_OFF_BOOT):
    """Validate block and return the flag byte at the given offset."""
    if not _check_nvm(nvm):
        print("NVM Initialized")
    return nvm[offset]


def is_set(nvm, mask, offset=_OFF_BOOT):
    """Check whether flag bit(s) are set."""
    read_flags(nvm, offset)
    return bool(nvm[offset] & mask)


def set_flag(nvm, mask, offset=_OFF_BOOT):
    """Set one or more flag bits."""
    read_flags(nvm, offset)
    nvm[offset] = nvm[offset] | mask


def clear_flag(nvm, mask, offset=_OFF_BOOT):
    """Clear one or more flag bits."""
    read_flags(nvm, offset)
    nvm[offset] = nvm[offset] & ~mask & 0xFF


def toggle_flag(nvm, mask, offset=_OFF_BOOT):
    """Toggle one or more flag bits. Returns new state of those bits."""
    read_flags(nvm, offset)
    nvm[offset] = nvm[offset] ^ mask
    return bool(nvm[offset] & mask)


def flag_status(nvm):
    """Print boot/repl flags and their current state."""
    flags = read_flags(nvm, _OFF_BOOT)
    print(f"  NVM layout version: {nvm[_OFF_VERSION]}")
    for mask, name in FLAG_NAMES.items():
        state = "ON" if (flags & mask) else "OFF"
        print(f"  {name}: {state}")
    # Show reserved bytes if any are non-zero
    for i in range(-4, 0):
        if nvm[i] != 0x00:
            print(f"  nvm[{i}]: 0x{nvm[i]:02X}")
