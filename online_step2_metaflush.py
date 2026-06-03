"""
online_step2_metaflush.py -- ONLINE testing, step 2 (arm meta-flush once).

Re-attaches dme to the ALREADY-RUNNING online Dolphin from step 1 (does NOT
relaunch), confirms we're still in SCENE_ONLINE_IN_GAME (0x0208), then performs
the MINIMAL meta-flush arm: a zero-length "ping". That writes the magic word
0xDEADBEEF to scratch 0x803FA440, the gecko observes it, skips both cache loops
(zero range), and writes 0 back. The only memory touched is the control-plane
scratch (0x803FA440..0x803FA448) -- no code change, no game-state write, no
cache invalidation of gameplay code.

This isolates ONE question: is the debug-menu scratch region inside Slippi's
desync checksum? If the gecko clears the magic AND we stay online, runtime
patching is viable online. If your other machine desyncs, the scratch is
checksummed and we need a different control-plane location (or boot-time gecko).

Leaves Dolphin running.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_step2_metaflush.py
"""
import sys
import time

import dolphin_memory_engine as dme
from melee_harness import Harness
import instr_writer as iw

SCENE_WORD = 0x80479D30
SCENE_ONLINE_IN_GAME = 0x0208
FRAME_PRIMARY = 0x80479D60


def minor_major(word):
    # getMinorMajor: rlwinm reg, reg, 8, 0xFFFF  ==  ((word<<8)|(word>>24)) & 0xFFFF
    return ((word << 8) | (word >> 24)) & 0xFFFF


def online_state(h):
    w = h.read_word(SCENE_WORD)
    return minor_major(w), w


def main():
    h = Harness()
    h.hook_dme()                       # attach to the running step-1 Dolphin
    print("[step2] re-attached dme to running Dolphin", flush=True)

    mm, raw = online_state(h)
    print(f"[step2] scene word 0x{raw:08X} -> minorMajor 0x{mm:04X} "
          f"({'ONLINE IN-GAME' if mm == SCENE_ONLINE_IN_GAME else 'NOT online in-game'})",
          flush=True)
    if mm != SCENE_ONLINE_IN_GAME:
        print("[step2] ABORT: not in an online match. Re-run step 1 / reset the "
              "other machine before arming meta-flush.", flush=True)
        dme.un_hook()
        return 1

    # confirm meta-flush still installed
    instr = h.read_word(iw.META_FLUSH_HOOK)
    is_branch = (instr & 0xFC000000) == 0x48000000
    print(f"[step2] meta-flush hook 0x{iw.META_FLUSH_HOOK:08X} = 0x{instr:08X} "
          f"({'branch (installed)' if is_branch else 'NOT a branch -- abort'})",
          flush=True)
    if not is_branch:
        dme.un_hook()
        return 1

    f0 = h.read_word(FRAME_PRIMARY)
    print(f"[step2] frame before arm: 0x{f0:08X}", flush=True)

    # --- THE TEST: minimal arm (zero-length ping) ---------------------------
    print("[step2] arming meta-flush (zero-length ping) ...", flush=True)
    t0 = time.time()
    try:
        iw.flush_range(h, iw.FLUSH_REQUEST, iw.FLUSH_REQUEST, timeout_s=2.0)
        dt = (time.time() - t0) * 1000
        print(f"[step2] *** gecko CLEARED the magic in {dt:.0f} ms -- meta-flush "
              f"RESPONDS ONLINE ***", flush=True)
    except TimeoutError as e:
        print(f"[step2] gecko did NOT clear the magic: {e}", flush=True)
        print("[step2] meta-flush is installed but not firing online (hook not "
              "reached in online frame path?).", flush=True)
        dme.un_hook()
        return 1

    # --- post-arm: still online? frame still advancing? ---------------------
    time.sleep(1.0)
    mm2, raw2 = online_state(h)
    f1 = h.read_word(FRAME_PRIMARY)
    print(f"\n[step2] post-arm scene 0x{raw2:08X} -> minorMajor 0x{mm2:04X} "
          f"({'STILL ONLINE' if mm2 == SCENE_ONLINE_IN_GAME else 'SCENE CHANGED'})",
          flush=True)
    print(f"[step2] frame after arm: 0x{f1:08X} (+{f1 - f0})", flush=True)

    # sample a few more to confirm steady advance (our side not hung)
    print("[step2] frame advance over ~3s:", flush=True)
    last = f1
    for i in range(6):
        time.sleep(0.5)
        f = h.read_word(FRAME_PRIMARY)
        print(f"  +{f - last}", end="", flush=True)
        last = f
    print(flush=True)

    print("\n[step2] DONE. Dolphin left running.", flush=True)
    print("[step2] >>> CHECK YOUR OTHER MACHINE: is the match still in sync, or "
          "did it desync right after the arm? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
