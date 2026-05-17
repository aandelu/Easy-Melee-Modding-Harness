"""Smoke test for the software-BP primitive (Phase 2 of Option B).

At the Slippi pre-game menu, the pad-read hook at 0x803775B8 fires once per
pad per frame. We set a BP there and verify the full lifecycle:

  1. setup: install meta-flush + launch + hook dme.
  2. install: set_breakpoint writes the handler + patches the target.
  3. hit: BP fires within ~1 frame, hit flag goes high.
  4. snapshot: r25 (PADStatus ptr), r1 (stack), LR all look like MEM1.
  5. continue: spin releases; CPU executes displaced original + branches
     back to target+4; game keeps running.
  6. multi-hit: 10 more hit/continue cycles, no hangs or corruption.
  7. teardown: remove_breakpoint restores the original instruction; no
     further hits within 1 s of polling.

If all 7 stages pass, the breakpoint primitive is sound -- we can now
inspect arbitrary state at any PC.
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import bp


TARGET = 0x803775B8
EXPECTED_ORIG = 0xA0190000      # lhz r0, 0(r25)


def main():
    h = Harness()
    try:
        print("install meta-flush + launch", flush=True)
        iw.install_meta_flush(h)
        h.launch()
        h.hook_dme()

        print("waiting for CPU", flush=True)
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"  CPU live ({prev} -> {cur})", flush=True)
                break
            prev = cur

        iw.wait_for_meta_flush_alive(h)
        print("meta-flush alive", flush=True)

        orig = h.read_word(TARGET)
        print(f"\ntarget 0x{TARGET:08X} = 0x{orig:08X} "
              f"(expected 0x{EXPECTED_ORIG:08X})", flush=True)
        if orig != EXPECTED_ORIG:
            print("[FAIL] target word doesn't look vanilla", flush=True)
            return 1

        # --- install ----------------------------------------------------
        print("\nsetting BP at 0x803775B8 ...", flush=True)
        b = bp.set_breakpoint(h, TARGET)
        print(f"  {b}", flush=True)

        hook_now = h.read_word(TARGET)
        expected_branch = 0x48000000 | ((b.handler - TARGET) & 0x03FFFFFC)
        print(f"  hook now = 0x{hook_now:08X} "
              f"(expected branch 0x{expected_branch:08X})", flush=True)
        if hook_now != expected_branch:
            print("[FAIL] target patch did not land", flush=True)
            return 1

        # --- first hit --------------------------------------------------
        print("\nwaiting for first hit (should be < 100 ms) ...", flush=True)
        t0 = time.time()
        bp.wait_for_hit(b, timeout_s=5.0)
        dt = (time.time() - t0) * 1000
        print(f"  hit after {dt:.1f} ms", flush=True)

        snap = bp.read_snapshot(b)
        print("\nsnapshot:", flush=True)
        for name in ("r0", "r1", "r2", "r3", "r25", "lr", "ctr", "cr"):
            v = snap[name]
            tag = " (MEM1 ptr)" if 0x80000000 <= v < 0x81800000 else ""
            print(f"  {name:4s} = 0x{v:08X}{tag}", flush=True)

        # r25 must look like a MEM1 ptr (PADStatus buffer base).
        if not 0x80000000 <= snap["r25"] < 0x81800000:
            print("[FAIL] r25 (PADStatus ptr) is not a MEM1 pointer", flush=True)
            return 1
        if not 0x80000000 <= snap["r1"] < 0x81800000:
            print("[FAIL] r1 (stack pointer) is not a MEM1 pointer", flush=True)
            return 1
        if not 0x80000000 <= snap["lr"] < 0x81800000:
            print("[FAIL] LR is not a MEM1 pointer", flush=True)
            return 1

        # --- continue + second hit --------------------------------------
        print("\ncontinuing ...", flush=True)
        bp.continue_(b)

        t0 = time.time()
        bp.wait_for_hit(b, timeout_s=5.0)
        dt = (time.time() - t0) * 1000
        snap2 = bp.read_snapshot(b)
        print(f"  second hit after {dt:.1f} ms; "
              f"r25={snap2['r25']:#010x} (was {snap['r25']:#010x})",
              flush=True)
        bp.continue_(b)

        # --- multi-hit stability ----------------------------------------
        print("\nrunning 10 hit/continue cycles ...", flush=True)
        for i in range(10):
            bp.wait_for_hit(b, timeout_s=2.0)
            bp.continue_(b)
        print("  10/10 cycles handled", flush=True)

        # --- teardown ---------------------------------------------------
        print("\nremoving BP ...", flush=True)
        bp.remove_breakpoint(b)
        restored = h.read_word(TARGET)
        print(f"  target restored: 0x{restored:08X} "
              f"(expected 0x{EXPECTED_ORIG:08X})", flush=True)
        if restored != EXPECTED_ORIG:
            print("[FAIL] target word not restored", flush=True)
            return 1

        # Hit flag should stay 0 for a full second -- BP is gone.
        print("\npolling hit flag for 1.0s (expect 0 spurious hits) ...",
              flush=True)
        h.write_words(bp.hit_flag_addr(b.slot), [0])
        spurious = 0
        end = time.time() + 1.0
        while time.time() < end:
            if h.read_word(bp.hit_flag_addr(b.slot)) != 0:
                spurious += 1
                h.write_words(bp.hit_flag_addr(b.slot), [0])
            time.sleep(0.01)
        print(f"  spurious hits: {spurious}", flush=True)
        if spurious:
            print("[FAIL] BP still firing after removal", flush=True)
            return 1

        print("\n[PASS] software BP primitive works end-to-end", flush=True)
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
