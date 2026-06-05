"""
observe_live_wavedash.py -- launch the netplay Dolphin via the harness HARDLINK
(so dme can attach) using the USER'S REAL config (so the shipped wavedash gecko in
GALE01.ini loads exactly as it would for normal play), do NOT load a savestate, and
OBSERVE the live game the user starts -- to debug the real-input wavedash.

Captures, on the LOCAL player (resolved via ODB) once an online match starts:
  * action state transitions
  * Analog Stick Data Y (Player Data +0x624, float) -- what the up-check reads
  * WD_PEND latch (0x803FA470)
  * whether holding up produces LandingFallSpecial (0x2B = wavedash) vs just hops

Verifies first that the wavedash hooks actually loaded from the real config
(0x8034E2AC -> branch, 0x8034E680 -> branch, caves present).

Run (the user starts an online game once the window appears + this prints READY):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 observe_live_wavedash.py
"""
import struct
import subprocess
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
from melee_harness import Harness, DOLPHIN_HARDLINK, ISO_PATH, USER_DIR, DOLPHIN_LOG

SCENE = 0x80479D30
POWERON = 0x804D7420
R13 = 0x804DB6A0
ODB_SLOT = R13 - 0x49E4
P1_GOBJ = 0x80453130
STRIDE = 0xE90
E2AC, E680 = 0x8034E2AC, 0x8034E680
CAVE_B, CAVE_A = 0x803FA800, 0x803FA600
WD_PEND = 0x803FA470
OFF_STATE = 0x10
OFF_STICKY = 0x624        # Analog Stick Data Y (float)

S_NAMES = {0x0E: "Wait", 0x0F: "WalkSlow", 0x14: "Dash", 0x15: "Run", 0x18: "KneeBend",
           0x19: "JumpF", 0x1A: "JumpB", 0x1B: "JumpAerialF", 0x1D: "Fall",
           0x20: "FallAerial", 0x2A: "Landing", 0x2B: "LandingFallSpecial(WD)",
           0xEC: "EscapeAir"}


def sn(s):
    return S_NAMES.get(s, f"0x{s:02X}")


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def ensure_hooked():
    if dme.is_hooked():
        return True
    for _ in range(20):
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


def main():
    kill_stale()
    print("[obs] launching netplay Dolphin (hardlink, REAL config, NO savestate) ...", flush=True)
    logf = open(DOLPHIN_LOG, "w")
    proc = subprocess.Popen([DOLPHIN_HARDLINK, "-e", ISO_PATH, "-u", USER_DIR],
                            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"[obs] pid {proc.pid}; attaching dme ...", flush=True)
    h = Harness()
    t0 = time.time()
    while time.time() - t0 < 60:
        if ensure_hooked():
            break
        time.sleep(0.3)
    if not dme.is_hooked():
        print("[obs] dme never attached -- abort", flush=True); return 1
    # wait for CPU alive
    last = None
    for _ in range(120):
        v = rw(h, POWERON)
        if v is not None and last is not None and v != last:
            break
        last = v
        time.sleep(0.25)
    print("[obs] CPU alive.", flush=True)

    # verify the shipped wavedash loaded from the real config
    print("\n[obs] === verify wavedash gecko loaded ===", flush=True)
    for nm, a, want in [("hook E2AC", E2AC, 0x480AC554), ("hook E680", E680, 0x480ABF80),
                        ("cave B[0]", CAVE_B, 0x9421FFD0), ("cave A[0]", CAVE_A, 0x9421FFD0)]:
        v = rw(h, a)
        ok = (v == want)
        print(f"  {nm}: {('0x%08X' % v) if v is not None else '<fail>'} "
              f"(want 0x{want:08X})  {'OK' if ok else 'NOT LOADED'}", flush=True)

    print("\n" + "=" * 64, flush=True)
    print("  READY -- start your ONLINE game now (no savestate). Once you're in-game,", flush=True)
    print("  HOLD UP repeatedly (try up, up+left, up+right, and neutral) for ~60s.", flush=True)
    print("=" * 64, flush=True)

    # wait for online in-game (0x0208)
    online = False
    t0 = time.time()
    while time.time() - t0 < 300:
        vals = [mm(x) for x in (rw(h, SCENE) for _ in range(7)) if x is not None]
        top = Counter(vals).most_common(1)[0][0] if vals else 0
        if top == 0x0208:
            online = True; break
        time.sleep(1.0)
    if not online:
        print("[obs] never saw online in-game (0x0208) in 180s. Did the game start "
              "online? (offline won't trigger the macro.)", flush=True)
        dme.un_hook(); return 1
    print("[obs] ONLINE in-game detected. Observing 60s -- HOLD UP now ...", flush=True)

    # observe -- re-resolve the local player EACH iteration (robust to transient
    # null pointers at match start / respawns)
    def resolve_pd():
        odb = rw(h, ODB_SLOT)
        if not (odb and (odb >> 24) == 0x80):
            return None, None
        port = rb(h, odb + 0)
        d = rb(h, odb + 0x21)
        if port is None or port > 3:
            return None, d
        gobj = rw(h, P1_GOBJ + port * STRIDE)
        if not (gobj and (gobj >> 24) == 0x80):
            return None, d
        pd = rw(h, gobj + 0x2C)
        if not (pd and (pd >> 24) == 0x80):
            return None, d
        return pd, d

    prev = None
    jumps = wds = airs = 0
    up_sticky_max = -9.0
    neutral_sticky_samples = []
    log = []
    delay = None
    bad = 0
    t0 = time.time()
    while time.time() - t0 < 60:
        pd, delay = resolve_pd()
        if pd is None:
            bad += 1; time.sleep(0.05); continue
        st = rw(h, pd + OFF_STATE)
        sy = rf(h, pd + OFF_STICKY)
        wp = rb(h, WD_PEND)
        if st is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        if sy is not None:
            if sy >= 0.5625:
                up_sticky_max = max(up_sticky_max, sy)
            else:
                neutral_sticky_samples.append(sy)
        if st != prev:
            if st == 0x18:
                jumps += 1
            elif st == 0x2B:
                wds += 1
            elif st in (0x19, 0x1B, 0x20, 0xEC):
                airs += 1
            if len(log) < 80:
                log.append((round(time.time() - t0, 2), sn(st),
                            f"{sy:+.2f}" if sy is not None else "?", wp))
            prev = st
        time.sleep(0.015)

    print("\n[obs] === state transitions (t, state, stickY, WD_PEND) ===", flush=True)
    for t, s, y, w in log:
        print(f"  {t:5.2f}  {s:<22} stickY={y}  latch={w}", flush=True)
    print("\n[obs] === SUMMARY ===", flush=True)
    print(f"[obs] KneeBend(jumps)={jumps}  LandingFallSpecial(wavedashes)={wds}  air-states={airs}",
          flush=True)
    print(f"[obs] max stickY while 'up' (>=0.5625) = {up_sticky_max:+.2f}", flush=True)
    if neutral_sticky_samples:
        import statistics
        print(f"[obs] neutral/other stickY: n={len(neutral_sticky_samples)} "
              f"min={min(neutral_sticky_samples):+.2f} max={max(neutral_sticky_samples):+.2f} "
              f"median={statistics.median(neutral_sticky_samples):+.2f}", flush=True)
    if wds > 0:
        print("[obs] [GOOD] wavedashes (LandingFallSpecial) observed -> airdodge firing.", flush=True)
    elif jumps > 0:
        print("[obs] [ISSUE] jumps but no wavedash -> airdodge not firing (timing/latch).", flush=True)
    else:
        print("[obs] [ISSUE] no jumps -> up-trigger not firing (stickY read/threshold).", flush=True)
    print("[obs] DONE. Dolphin left running.", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
