"""
attach_observe_wavedash.py -- ATTACH to the already-running harness Dolphin (no
relaunch) and observe the live wavedash with per-frame re-resolution of the local
player (robust to transient null pointers at match start / respawns).

Run while the user is in their online game on the harness-launched Dolphin:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 attach_observe_wavedash.py
"""
import struct
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
from melee_harness import Harness

SCENE = 0x80479D30
R13 = 0x804DB6A0
ODB_SLOT = R13 - 0x49E4
P1_GOBJ = 0x80453130
STRIDE = 0xE90
E2AC, E680 = 0x8034E2AC, 0x8034E680
WD_PEND = 0x803FA470
OFF_STATE = 0x10
OFF_STICKY = 0x624

S_NAMES = {0x0E: "Wait", 0x0F: "WalkSlow", 0x14: "Dash", 0x15: "Run", 0x18: "KneeBend",
           0x19: "JumpF", 0x1A: "JumpB", 0x1B: "JumpAerialF", 0x1D: "Fall",
           0x20: "FallAerial", 0x2A: "Landing", 0x2B: "LandingFallSpecial(WD)",
           0xEC: "EscapeAir"}


def sn(s):
    return S_NAMES.get(s, f"0x{s:02X}")


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def ensure_hooked():
    if dme.is_hooked():
        return True
    for _ in range(25):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.2)
    return False


def rw(h, a):
    try:
        return h.read_word(a)
    except Exception:
        if ensure_hooked():
            try:
                return h.read_word(a)
            except Exception:
                return None
        return None


def rf(h, a):
    try:
        return struct.unpack(">f", h.read_bytes(a, 4))[0]
    except Exception:
        return None


def rb(h, a):
    try:
        return h.read_bytes(a, 1)[0]
    except Exception:
        return None


def maj(h, a, n=5):
    vals = [v for v in (rw(h, a) for _ in range(n)) if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def resolve_pd(h):
    """ODB -> local port -> GObj -> Player Data. Returns (pd, port, delay) or (None,..)."""
    odb = maj(h, ODB_SLOT)
    if not (odb and (odb >> 24) == 0x80):
        return None, None, None
    port = rb(h, odb + 0)
    delay = rb(h, odb + 0x21)
    if port is None or port > 3:
        return None, None, delay
    gobj = maj(h, P1_GOBJ + port * STRIDE)
    if not (gobj and (gobj >> 24) == 0x80):
        return None, port, delay
    pd = maj(h, gobj + 0x2C)
    if not (pd and (pd >> 24) == 0x80):
        return None, port, delay
    return pd, port, delay


def main():
    h = Harness()
    if not ensure_hooked():
        print("[ao] dme never attached -- relaunch needed", flush=True); return 1
    sc = maj(h, SCENE)
    print(f"[ao] scene 0x{mm(sc):04X} (want 0x0208)" if sc is not None else "[ao] scene read fail",
          flush=True)
    for nm, a, want in [("hook E2AC", E2AC, 0x480AC554), ("hook E680", E680, 0x480ABF80)]:
        v = maj(h, a)
        print(f"  {nm}: {('0x%08X' % v) if v is not None else '<fail>'} "
              f"{'OK' if v == want else 'BAD(re-attach garbage?)'}", flush=True)
    if sc is None or mm(sc) != 0x0208:
        print("[ao] not cleanly attached/in-game -- if BAD above, the running Dolphin "
              "can't be re-attached; we'll relaunch.", flush=True)
        # keep going anyway in case scene was just torn; resolve below will gate it

    print("\n[ao] observing 60s -- HOLD UP now (up / up+left / up+right / neutral) ...", flush=True)
    prev = None
    jumps = wds = airs = 0
    up_max = -9.0
    neutral = []
    log = []
    bad = 0
    t0 = time.time()
    while time.time() - t0 < 60:
        pd, port, delay = resolve_pd(h)
        if pd is None:
            bad += 1
            time.sleep(0.05)
            continue
        st = rw(h, pd + OFF_STATE)
        sy = rf(h, pd + OFF_STICKY)
        wp = rb(h, WD_PEND)
        if st is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        if sy is not None:
            if sy >= 0.5625:
                up_max = max(up_max, sy)
            else:
                neutral.append(sy)
        if st != prev:
            if st == 0x18:
                jumps += 1
            elif st == 0x2B:
                wds += 1
            elif st in (0x19, 0x1B, 0x20, 0xEC):
                airs += 1
            if len(log) < 90:
                log.append((round(time.time() - t0, 2), sn(st),
                            f"{sy:+.2f}" if sy is not None else "?", wp, delay))
            prev = st
        time.sleep(0.015)

    print(f"\n[ao] (resolve misses: {bad})", flush=True)
    print("[ao] === transitions (t, state, stickY, latch, delay) ===", flush=True)
    for t, s, y, w, d in log:
        print(f"  {t:5.2f}  {s:<22} stickY={y}  latch={w}  delay={d}", flush=True)
    print("\n[ao] === SUMMARY ===", flush=True)
    print(f"[ao] jumps(KneeBend)={jumps}  wavedashes(LandingFallSpecial)={wds}  air-states={airs}",
          flush=True)
    print(f"[ao] max stickY when up(>=0.5625)={up_max:+.2f}", flush=True)
    if neutral:
        import statistics
        print(f"[ao] neutral/other stickY: n={len(neutral)} min={min(neutral):+.2f} "
              f"max={max(neutral):+.2f} median={statistics.median(neutral):+.2f}", flush=True)
    if wds > 0:
        print("[ao] [GOOD] wavedashes observed -> airdodge firing on up.", flush=True)
    elif jumps > 0:
        print("[ao] [ISSUE] jumps but no wavedash -> airdodge not completing.", flush=True)
    else:
        print("[ao] [ISSUE] no jumps -> up-trigger not firing.", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
