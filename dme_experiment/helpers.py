"""Shared helpers for dme_experiment scripts.

Builds on melee_harness.Harness for launch/seed/reset/observe primitives.
Adds dme button/stick writes targeted at multiple input planes so we can
empirically figure out which one Melee's engine actually consumes when we
race the input pipeline from Python.

PLANES we can write to via dme (in pipeline order, earliest to latest):
  1. Raw PADStatus (per-port, 12 bytes, structure {buttons:u16, stickX:s8,
     stickY:s8, ...}). Stored in MEM1 at SI driver locations. Gecko hook
     0x803775B8 fires JUST before the engine reads buttons from here.
  2. Global processed digital data: 0x804C1FAC + 0x44*(port-1). Bit
     layout xxxxUDLR UDLR... xxxSYXBA xLRZUDRL.
  3. Per-player Digital Button Data: PD+0x65C (word, same layout as #2).
  4. Per-player Analog Stick X/Y: PD+0x620, PD+0x624 (floats -1.0..1.0).
  5. Per-player Instant Buttons: PD+0x668 (just-pressed delta).

Button bits (see docs/REFERENCE.md and existing gecko candidates):
  Z = 0x0010, B = 0x0200, Y = 0x0800, A = 0x0100, X = 0x0400, Start = 0x1000,
  D-up = 0x0008, D-down = 0x0004, D-left = 0x0001, D-right = 0x0002.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dolphin_memory_engine as dme  # noqa: E402
from melee_harness import (  # noqa: E402
    Harness, OFF_ACTION_STATE, P1_ENTITY_PTR, ENTITY_PTR_STRIDE,
    OFF_PLAYER_DATA, OFF_BUTTONS, CONTROLLER_DIGITAL, CONTROLLER_STRIDE,
    _is_valid_mem1_ptr,
)
import scenario  # noqa: E402

# --- per-player input offsets (Char_Data_Offsets.csv, PD-relative) ---
OFF_ANALOG_X = 0x0620           # float
OFF_ANALOG_Y = 0x0624           # float
OFF_BUTTONS_PREV = 0x0660       # word
OFF_INSTANT_BUTTONS = 0x0668    # word

# --- digital button bits (engine plane, same as 0x804C1FAC layout) ---
BIT_DRIGHT = 0x0001
BIT_DLEFT = 0x0002
BIT_DDOWN = 0x0004
BIT_DUP = 0x0008
BIT_Z = 0x0010
BIT_R = 0x0020
BIT_L = 0x0040
BIT_A = 0x0100
BIT_B = 0x0200
BIT_X = 0x0400
BIT_Y = 0x0800
BIT_START = 0x1000

# --- PADStatus button bits (raw hardware plane, halfword in PADStatus) ---
# Layout matches GC PADStatus.button per dolphin/libogc: A=0x0100, B=0x0200,
# X=0x0400, Y=0x0800, Start=0x1000, D-left=0x0001, D-right=0x0002,
# D-down=0x0004, D-up=0x0008, Z=0x0010, R=0x0020, L=0x0040.
# (Same bit positions as engine plane -- they were chosen to match.)


def write_pd_buttons(h, port, mask):
    """OR `mask` into PD+0x65C and PD+0x668 (instant). The instant write is
    important: many transitions trigger only on just-pressed."""
    pd = h.player_data_ptr(port)
    if pd == -1:
        return False
    cur = h.read_word(pd + OFF_BUTTONS)
    h.write_words(pd + OFF_BUTTONS, [cur | mask])
    cur_inst = h.read_word(pd + OFF_INSTANT_BUTTONS)
    h.write_words(pd + OFF_INSTANT_BUTTONS, [cur_inst | mask])
    return True


def clear_pd_buttons(h, port, mask):
    """AND-NOT `mask` from PD+0x65C and PD+0x668."""
    pd = h.player_data_ptr(port)
    if pd == -1:
        return False
    cur = h.read_word(pd + OFF_BUTTONS)
    h.write_words(pd + OFF_BUTTONS, [cur & (~mask & 0xFFFFFFFF)])
    cur_inst = h.read_word(pd + OFF_INSTANT_BUTTONS)
    h.write_words(pd + OFF_INSTANT_BUTTONS, [cur_inst & (~mask & 0xFFFFFFFF)])
    return True


def write_pd_stick(h, port, x=None, y=None):
    """Set Fox's analog stick X/Y at PD+0x620/+0x624 (floats -1.0..1.0)."""
    pd = h.player_data_ptr(port)
    if pd == -1:
        return False
    if x is not None:
        h.write_bytes(pd + OFF_ANALOG_X, struct.pack(">f", x))
    if y is not None:
        h.write_bytes(pd + OFF_ANALOG_Y, struct.pack(">f", y))
    return True


def read_pd_stick(h, port):
    pd = h.player_data_ptr(port)
    if pd == -1:
        return None, None
    x = struct.unpack(">f", h.read_bytes(pd + OFF_ANALOG_X, 4))[0]
    y = struct.unpack(">f", h.read_bytes(pd + OFF_ANALOG_Y, 4))[0]
    return x, y


def write_global_buttons(h, port, mask):
    """OR `mask` into 0x804C1FAC + 0x44*(port-1)."""
    addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
    cur = struct.unpack(">I", h.read_bytes(addr, 4))[0]
    h.write_bytes(addr, struct.pack(">I", (cur | mask) & 0xFFFFFFFF))


def write_global_stick(h, port, x=None, y=None):
    """Float stick at +0x10 from controller digital. Per Global_Addresses,
    0x804C1FAC + 0x44*(port-1) layout: digital@+0x00, ..., analog@+0x20."""
    addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
    if x is not None:
        h.write_bytes(addr + 0x20, struct.pack(">f", x))
    if y is not None:
        h.write_bytes(addr + 0x24, struct.pack(">f", y))


def snapshot_frame(h, ports=(1, 2)):
    """One-line per-frame snapshot for logging."""
    frame = h.frame()
    rec = {"frame": frame}
    for p in ports:
        rec[f"p{p}_action"] = h.action_state(p) & 0xFFFF
        pd = h.player_data_ptr(p)
        if pd != -1:
            rec[f"p{p}_buttons"] = h.read_word(pd + OFF_BUTTONS)
            rec[f"p{p}_instant"] = h.read_word(pd + OFF_INSTANT_BUTTONS)
            rec[f"p{p}_stick_x"] = struct.unpack(
                ">f", h.read_bytes(pd + OFF_ANALOG_X, 4))[0]
            rec[f"p{p}_stick_y"] = struct.unpack(
                ">f", h.read_bytes(pd + OFF_ANALOG_Y, 4))[0]
        else:
            rec[f"p{p}_buttons"] = -1
            rec[f"p{p}_instant"] = -1
            rec[f"p{p}_stick_x"] = 0.0
            rec[f"p{p}_stick_y"] = 0.0
    return rec


def print_record(r, prefix=""):
    p1 = f"p1=0x{r['p1_action']:04X} btn=0x{r['p1_buttons']:08X}"
    p2 = (f"p2=0x{r['p2_action']:04X} btn=0x{r['p2_buttons']:08X} "
          f"sy={r['p2_stick_y']:+.2f}")
    print(f"{prefix}f={r['frame']:>5} {p1} | {p2}", flush=True)


def runs_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
    os.makedirs(d, exist_ok=True)
    return d
