"""
play_wavedash_monitor.py -- install the wavedash macro AND monitor your inputs in
ONE process (dme stays hooked, so reads are reliable -- unlike re-attaching to an
already-running Dolphin, which fails).

Reuses the cave + install from play_wavedash_offline.py, then runs a monitor loop
on P1 (Marth) logging per-frame on change: state, asfc, processed stick, buttons.
Diagnoses "Marth jumps instead of wavedashing": watch whether stickY drops below
the up threshold before the airdodge frame (asfc == jumpsquat-1), and whether the
jump ends in LandingFallSpecial (wavedash) or JumpF (bug).

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 play_wavedash_monitor.py [secs]
"""
import struct
import sys
import time

import play_wavedash_offline as P
from melee_harness import finalize_payload, Harness
import instr_writer as iw

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
PORT = 1                          # Marth = P1
P1_GOBJ = 0x80453130
STRIDE = 0xE90
FRAME_ADDR = 0x80479D60
OFF_STATE, OFF_ASTICK_X, OFF_ASTICK_Y = 0x10, 0x620, 0x624
OFF_BUTTONS, OFF_ASFC, OFF_JUMPSQUAT = 0x65C, 0x894, 0x148

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


def rf(h, a):
    try:
        return struct.unpack(">f", h.read_bytes(a, 4))[0]
    except Exception:
        return None


def jdec(raw):
    if raw is None:
        return None
    return raw if raw < 0x100 else struct.unpack(">f", struct.pack(">I", raw))[0]


def main():
    logic = P.assemble(P.CAVE_ASM)
    payload = finalize_payload(logic, P.HOOK_ADDR, P.CAVE, P.DISPLACED)
    P.kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[mon] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[mon] seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    h.write_bytes(P.UPTHRESH_ADDR, bytes([0x40]))
    h.write_bytes(P.FIRE_ADDR, b"\x00")
    iw.write_instrs(h, P.CAVE, payload)
    iw.patch_branch(h, P.HOOK_ADDR, P.CAVE)
    h.wait_frames(15)
    fires = h.read_bytes(P.FIRE_ADDR, 1)[0]
    pd = h.player_data_ptr(PORT)
    js = jdec(struct.unpack(">I", h.read_bytes(pd + OFF_JUMPSQUAT, 4))[0]) if pd != -1 else None
    print(f"[mon] cave fires={fires}; P{PORT} jumpsquat={js} "
          f"(airdodge frame asfc={int(js)-1 if js else '?'})", flush=True)

    print(f"\n[mon] ===== MONITORING {SECS:.0f}s (BOTH ports) -- play and "
          "reproduce the jump-instead-of-wavedash, no rush =====", flush=True)
    print("  frame | pt | state                  | asfc | stickX stickY | buttons",
          flush=True)
    t_end = time.time() + SECS
    last_frame = None
    last_key = {1: None, 2: None}
    while time.time() < t_end:
        fr = h.read_word(FRAME_ADDR)
        if fr == last_frame:
            time.sleep(0.001)
            continue
        last_frame = fr
        for port in (1, 2):
            g = h.read_word(P1_GOBJ + (port - 1) * STRIDE)
            if not (0x80000000 <= g < 0x81800000):
                continue
            p = h.read_word(g + 0x2C)
            if not (0x80000000 <= p < 0x81800000):
                continue
            st = h.read_word(p + OFF_STATE) & 0xFFFF
            sx = rf(h, p + OFF_ASTICK_X)
            sy = rf(h, p + OFF_ASTICK_Y)
            af = rf(h, p + OFF_ASFC)
            bt = h.read_word(p + OFF_BUTTONS) & 0xFFFF
            if None in (sx, sy, af):
                continue
            # only log a port that is doing something (not idle Wait at neutral)
            idle = (st == 0x0E and abs(sx) < 0.1 and abs(sy) < 0.1 and bt == 0)
            key = (st, round(sx, 1), round(sy, 1), bt)
            if key != last_key[port] and not (idle and last_key[port] is None):
                print(f"  {fr & 0xFFFF:5d} | P{port} | {sn(st):22s} | {int(af):4d} | "
                      f"{sx:+5.2f} {sy:+5.2f} | {bt:04X}", flush=True)
                last_key[port] = key

    print("\n[mon] done. Dolphin left running (macro still installed).", flush=True)
    try:
        import dolphin_memory_engine as dme
        dme.un_hook()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
