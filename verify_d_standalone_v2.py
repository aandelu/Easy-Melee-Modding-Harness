"""Smoke test for candidate_d_standalone_v2 (menu-safe variant).

Must still pass the canonical-JC-shine criterion in-match:
  - KneeBend at +1 frame after Catch is forced
  - Aerial shine startup (0x016D) appears without JumpF in between
Repeated 3 trials without manual counter management; gecko self-resets
when Marth leaves the grab.

The point of v2 vs v1 is preventing menu-side invalid reads, which can't
be reproduced inside this verifier (verifier always runs in-match after
seed_snapshot). So the verifier's job is to confirm no regression."""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import scenario as sc
import candidate_d_standalone_v2 as cs


N_TRIALS = 3
AERIAL_SHINE = 0x016D


def main():
    h = Harness()
    try:
        print("staging meta-flush + standalone v2 candidate", flush=True)
        iw.install_meta_flush(h)
        h.install_gecko_c2(
            name=cs.NAME, hook_addr=cs.HOOK_ADDR,
            logic_words=cs.LOGIC, displaced_orig=cs.DISPLACED_ORIG,
        )
        h.launch()
        h.hook_dme()

        print("waiting for CPU + meta-flush", flush=True)
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                break
            prev = cur
        iw.wait_for_meta_flush_alive(h)

        print("seeding scenario", flush=True)
        h.seed_snapshot(timeout_s=60.0)

        def run_trial():
            h.reset()
            sc.record_window(h, 2)
            sc.force_action_state(h, 1, sc.CATCH)
            trigger_frame = h.frame()
            rest = sc.record_window(h, 14)
            return {"trigger_frame": trigger_frame, "records": rest}

        passes = 0
        for i in range(N_TRIALS):
            trial = run_trial()
            t = trial["trigger_frame"]
            records = trial["records"]

            kb_lat = next((r["frame"] - t for r in records
                          if r["frame"] >= t and r["p2_action"] == sc.KNEE_BEND),
                         None)
            shine_lat = next((r["frame"] - t for r in records
                             if r["frame"] >= t and r["p2_action"] == AERIAL_SHINE),
                            None)
            jumpf_seen = any(r["frame"] >= t and r["p2_action"] == sc.JUMP_F
                            for r in records)

            print(f"\ntrial {i + 1}:", flush=True)
            for r in records:
                tag = ""
                if r["p2_action"] == sc.KNEE_BEND: tag = " KneeBend"
                elif r["p2_action"] == sc.JUMP_F: tag = " JumpF(BAD)"
                elif r["p2_action"] == AERIAL_SHINE: tag = " AERIAL_SHINE"
                elif r["p2_action"] == 0x016E: tag = " aerial_loop"
                marker = ">>" if r["frame"] == t else "  "
                print(f"  {marker} f={r['frame']}  p1=0x{r['p1_action']:04X}  "
                      f"p2=0x{r['p2_action']:04X}{tag}", flush=True)
            print(f"  kb_lat={kb_lat}  shine_lat={shine_lat}  "
                  f"jumpf={jumpf_seen}", flush=True)

            counter_after = h.read_bytes(cs.COUNTER_ADDR, 1)[0]
            print(f"  counter post-trial = 0x{counter_after:02X}", flush=True)

            if kb_lat == 1 and shine_lat is not None and not jumpf_seen:
                passes += 1
                print("  -> PASS", flush=True)
            else:
                print("  -> FAIL", flush=True)

        print(f"\n{passes}/{N_TRIALS} passed", flush=True)
        if passes == N_TRIALS:
            print("\n[PASS] v2 macro still produces canonical JC-shine",
                  flush=True)
            return 0
        print("\n[FAIL]", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
