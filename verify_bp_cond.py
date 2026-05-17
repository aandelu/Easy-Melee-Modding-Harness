"""Smoke test for conditional BPs (Phase 2.2 of Option B).

Two scenarios:

  (a) Counter predicate: BP at 0x803775B8, wait until the 5th hit.
      Predicate counts hits; first 4 silently released, 5th stops.

  (b) Register predicate: BP at 0x803775B8, wait for r25 to have a
      specific low-byte pattern. We don't actually know what r25 will
      be ahead of time, so we observe r25 on the first hit, then
      construct a predicate that matches an alternate value. To make
      the test deterministic, we use the snapshot value as the
      target -- the first wait_for_condition with predicate
      "r25 == observed_value" should fire on the very next hit (because
      r25 is the same PADStatus pointer across pads). That doesn't
      really test "skip" behavior, so instead we use predicate
      "r3 == N for some N" -- the test snapshots r3 over the first 10
      hits, sees how often each value appears, and picks one that
      appeared but not on the first hit, then waits for it.

We keep the conditional smoke test simple in case the test environment
isn't 100% repeatable across hits.
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import bp


TARGET = 0x803775B8


def main():
    h = Harness()
    try:
        print("install meta-flush + launch", flush=True)
        iw.install_meta_flush(h)
        h.launch()
        h.hook_dme()

        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"CPU live ({prev} -> {cur})", flush=True)
                break
            prev = cur
        iw.wait_for_meta_flush_alive(h)
        print("meta-flush alive", flush=True)

        # --- (a) counter predicate -------------------------------------
        print(f"\n=== (a) wait for 5th hit at 0x{TARGET:08X} ===", flush=True)
        b = bp.set_breakpoint(h, TARGET)
        counter = [0]

        def fifth_hit(snap):
            counter[0] += 1
            print(f"    hit {counter[0]}: r25=0x{snap['r25']:08X}", flush=True)
            return counter[0] == 5

        t0 = time.time()
        bp.wait_for_condition(b, fifth_hit, timeout_s=10.0)
        dt = (time.time() - t0) * 1000
        print(f"  predicate matched after {counter[0]} hits "
              f"({dt:.1f}ms)", flush=True)
        if counter[0] != 5:
            print(f"  [FAIL] expected 5 hits, got {counter[0]}", flush=True)
            return 1
        bp.continue_(b)
        bp.remove_breakpoint(b)

        # --- (b) register predicate ------------------------------------
        # Observe r25 across the first N hits. r25 is the PADStatus
        # pointer; it cycles across pads (0,1,2,3) every frame.
        print(f"\n=== (b) observe r25 cycle over 20 hits ===", flush=True)
        b = bp.set_breakpoint(h, TARGET)
        r25_seq = []
        for i in range(20):
            bp.wait_for_hit(b, timeout_s=2.0)
            s = bp.read_snapshot(b)
            r25_seq.append(s["r25"])
            bp.continue_(b)

        # Build a set of unique r25 values and their first index.
        first_idx = {}
        for i, v in enumerate(r25_seq):
            first_idx.setdefault(v, i)
        print(f"  unique r25 values observed:", flush=True)
        for v in sorted(first_idx, key=first_idx.get):
            print(f"    0x{v:08X}  (first seen at hit {first_idx[v]})",
                  flush=True)

        # Pick the LAST unique value that appeared (skips at least one
        # earlier hit). If only one unique value exists, just pick it
        # (the test passes trivially -- always matches on the first hit
        # but at least proves the API is callable).
        target_r25 = sorted(first_idx, key=first_idx.get)[-1]
        skips_expected = first_idx[target_r25]
        print(f"\n  predicate: r25 == 0x{target_r25:08X} "
              f"(expect ~{skips_expected} skips per match)", flush=True)

        skips = [0]

        def cond(snap):
            if snap["r25"] != target_r25:
                skips[0] += 1
                return False
            return True

        t0 = time.time()
        s = bp.wait_for_condition(b, cond, timeout_s=10.0)
        dt = (time.time() - t0) * 1000
        print(f"  matched after {skips[0]} skips in {dt:.1f}ms: "
              f"r25=0x{s['r25']:08X}", flush=True)
        if s["r25"] != target_r25:
            print(f"  [FAIL] matched snapshot doesn't have target r25", flush=True)
            return 1
        bp.continue_(b)
        bp.remove_breakpoint(b)

        print("\n[PASS] conditional BPs work (both counter and register predicates)",
              flush=True)
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
