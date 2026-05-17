"""
Stage-1 harness verification (all-dme architecture, no libmelee).

Proves the harness foundation works through dme alone:
  1. launch Dolphin directly + dme hook.
  2. seed_snapshot: user loads savestate slot 2 via Dolphin's GUI menu; the
     harness snapshots MEM1 via dme.
  3. The seeded state is the scenario: P1=Marth, P2=Fox, in-game -- fully
     observable through dme (entity pointers, char ids, action states).
  4. A documented frame counter is live and advancing (the timing oracle).
  5. restore_snapshot actually reverts MEM1: write a sentinel into the code
     cave, confirm the write lands, let the game run forward, restore, and
     confirm BOTH the cave word and the frame counter snapped back.
"""
import struct
import sys

from melee_harness import (
    Harness, MEM1_BASE, DEFAULT_CAVE, _is_valid_mem1_ptr,
)

MARTH = 0x12
FOX = 0x01


def banner(msg):
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}", flush=True)


def main():
    h = Harness()
    try:
        banner("Launching Dolphin + hooking dme")
        h.launch()
        h.hook_dme()
        print("launched, dme hooked", flush=True)

        banner("Seeding scenario snapshot (load savestate slot 2 via Dolphin menu)")
        h.seed_snapshot()

        banner("Inspecting seeded state (pure dme observation)")
        for port in (1, 2):
            print(f"  port {port}: entity_ptr=0x{h.entity_ptr(port):08X} "
                  f"char_id=0x{h.char_id(port) & 0xFF:02X} "
                  f"action_state=0x{h.action_state(port) & 0xFFFF:04X} "
                  f"port_id={h.port_id(port)}", flush=True)
        p1_ent, p2_ent = h.entity_ptr(1), h.entity_ptr(2)
        observe_ok = _is_valid_mem1_ptr(p1_ent) and _is_valid_mem1_ptr(p2_ent)
        if not observe_ok:
            print("[FAIL] one or both entity pointers invalid -- not in-game",
                  flush=True)
            return 1
        if h.char_id(1) != MARTH:
            print(f"[WARN] P1 char_id 0x{h.char_id(1) & 0xFF:02X}, expected "
                  f"MARTH 0x12", flush=True)
        if h.char_id(2) != FOX:
            print(f"[WARN] P2 char_id 0x{h.char_id(2) & 0xFF:02X}, expected "
                  f"FOX 0x01", flush=True)

        banner("Frame counter check (timing oracle)")
        if h._frame_addr is None:
            print("[FAIL] no advancing frame counter found", flush=True)
            return 1
        f0 = h.frame()
        f1 = h.wait_frames(10)
        advanced = (f1 - f0) & 0xFFFFFFFF
        print(f"  counter 0x{h._frame_addr:08X}: {f0} -> {f1} "
              f"(advanced {advanced} over wait_frames(10))", flush=True)
        frame_ok = advanced >= 10

        banner("Testing restore_snapshot (reset must revert MEM1)")
        # Read the snap value from whichever counter calibration actually picked
        # (could be FRAME_TIMER or POWERON_COUNT) -- comparing different counters
        # is meaningless.
        snap_frame = struct.unpack_from(">I", h._snapshot,
                                        h._frame_addr - MEM1_BASE)[0]
        cave_orig = struct.unpack_from(">I", h._snapshot,
                                       DEFAULT_CAVE - MEM1_BASE)[0]
        print(f"snapshot frame={snap_frame}  cave word=0x{cave_orig:08X}",
              flush=True)

        SENTINEL = 0xDEADBEEF
        h.write_words(DEFAULT_CAVE, [SENTINEL])
        poked = h.read_word(DEFAULT_CAVE)
        write_ok = (poked == SENTINEL)
        print(f"wrote sentinel to cave 0x{DEFAULT_CAVE:08X}: "
              f"read back 0x{poked:08X}  write_ok={write_ok}", flush=True)

        h.wait_frames(30)               # let the game run well past snap_frame
        pre_restore_frame = h.frame()
        print(f"after running forward, frame={pre_restore_frame}", flush=True)

        h.restore_snapshot()
        post_cave = h.read_word(DEFAULT_CAVE)
        post_frame = h.frame()
        cave_revert_ok = (post_cave == cave_orig)
        frame_revert_ok = (post_frame < pre_restore_frame and
                           abs(post_frame - snap_frame) < 30)
        print(f"after restore: cave word=0x{post_cave:08X} "
              f"(cave_revert_ok={cave_revert_ok})", flush=True)
        print(f"after restore: frame={post_frame} vs snap {snap_frame} "
              f"(frame_revert_ok={frame_revert_ok})", flush=True)
        restore_ok = write_ok and cave_revert_ok and frame_revert_ok

        banner("Result")
        if observe_ok and frame_ok and restore_ok:
            print("[PASS] direct launch + dme hook, scenario observable via dme, "
                  "frame counter live, restore_snapshot reverts MEM1.", flush=True)
            return 0
        print(f"[FAIL] observe_ok={observe_ok} frame_ok={frame_ok} "
              f"restore_ok={restore_ok}", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
