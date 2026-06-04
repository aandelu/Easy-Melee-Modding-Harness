"""
play_wavedash_observe.py -- READ-ONLY monitor of the OFFLINE play session, to
diagnose "Marth jumps instead of wavedashing". Re-attaches dme to the running
Dolphin (left up by play_wavedash_offline.py) and logs P1 (Marth) per-frame on
change: state, asfc, processed stick, buttons, jumpsquat.

Diagnostic read: on KneeBend frames BEFORE the airdodge frame the macro does NOT
override the stick, so the processed stickY there = YOUR raw input -> we can see
whether up was released before asfc==jumpsquat-1. The jump's OUTCOME (JumpF =
bug / LandingFallSpecial = wavedash) shows whether the airdodge fired.

Run (while playing -- Dolphin keeps focus, this just reads memory):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 play_wavedash_observe.py [secs]
"""
import struct
import sys
import time

import dolphin_memory_engine as dme

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

PORT = 1                          # Marth = P1
P1_GOBJ = 0x80453130
STRIDE = 0xE90
FRAME_ADDR = 0x80479D60

OFF_STATE = 0x10
OFF_FACING = 0x2C
OFF_ASTICK_X = 0x620
OFF_ASTICK_Y = 0x624
OFF_BUTTONS = 0x65C
OFF_ASFC = 0x894
OFF_JUMPSQUAT = 0x148

STATE_NAMES = {
    0x0E: "Wait", 0x0F: "WalkSlow", 0x10: "WalkMid", 0x11: "WalkFast",
    0x12: "Turn", 0x14: "Dash", 0x15: "Run", 0x17: "RunBrake",
    0x18: "KneeBend", 0x19: "JumpF", 0x1A: "JumpB", 0x1B: "JumpAerialF",
    0x1C: "JumpAerialB", 0x1D: "Fall", 0x1F: "FallB", 0x20: "FallAerial",
    0x27: "Squat", 0x28: "SquatWait", 0x2A: "Landing",
    0x2B: "LandingFallSpecial(WD)", 0xEC: "EscapeAir(AD)",
}


def sn(s):
    return STATE_NAMES.get(s, f"0x{s:02X}")


def rehook():
    try:
        dme.un_hook()
    except Exception:
        pass
    for _ in range(30):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.1)
    return False


def sw(a):
    try:
        return dme.read_word(a) & 0xFFFFFFFF
    except Exception:
        return None


def fb(a):
    try:
        return struct.unpack(">f", dme.read_bytes(a, 4))[0]
    except Exception:
        return None


def valid(p):
    return p is not None and 0x80000000 <= p < 0x81800000


def jdec(raw):
    """jump-startup (0x148): int or float -> int frames."""
    if raw is None:
        return None
    if raw < 0x100:
        return raw
    return struct.unpack(">f", struct.pack(">I", raw))[0]


def main():
    if not rehook():
        print("[obs] could not hook dme (is Dolphin running?)", flush=True)
        return 1
    gobj = sw(P1_GOBJ + (PORT - 1) * STRIDE)
    pd = sw(gobj + 0x2C) if valid(gobj) else None
    if not valid(pd):
        print(f"[obs] P{PORT} player data invalid (gobj={gobj})", flush=True)
        return 1
    js = jdec(sw(pd + OFF_JUMPSQUAT))
    print(f"[obs] P{PORT} pd=0x{pd:08X} char=0x{sw(pd+4)&0xFF:02X} "
          f"jumpsquat={js} -> airdodge frame asfc={int(js)-1 if js else '?'}",
          flush=True)
    print(f"[obs] monitoring {SECS:.0f}s -- reproduce the bug now (hold up, and "
          "try the input that makes Marth jump).\n", flush=True)
    print("  frame | state                  | asfc | stickX stickY | buttons",
          flush=True)

    t_end = time.time() + SECS
    last_frame = None
    last_key = None
    while time.time() < t_end:
        fr = sw(FRAME_ADDR)
        if fr is None:
            time.sleep(0.01)
            continue
        if fr == last_frame:
            time.sleep(0.001)
            continue
        last_frame = fr
        g = sw(P1_GOBJ + (PORT - 1) * STRIDE)
        p = sw(g + 0x2C) if valid(g) else None
        if not valid(p):
            continue
        st = sw(p + OFF_STATE)
        if st is None:
            continue
        st &= 0xFFFF
        sx = fb(p + OFF_ASTICK_X)
        sy = fb(p + OFF_ASTICK_Y)
        af = fb(p + OFF_ASFC)
        bt = sw(p + OFF_BUTTONS)
        if None in (sx, sy, af, bt):
            continue
        key = (st, round(sx, 1), round(sy, 1), bt & 0xFFFF)
        if key != last_key:
            print(f"  {fr & 0xFFFF:5d} | {sn(st):22s} | {int(af):4d} | "
                  f"{sx:+5.2f} {sy:+5.2f} | {bt & 0xFFFF:04X}", flush=True)
            last_key = key

    print("\n[obs] done.", flush=True)
    try:
        dme.un_hook()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
