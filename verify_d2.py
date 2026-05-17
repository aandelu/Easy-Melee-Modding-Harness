"""Smoke test for candidate_d2 -- the netplay-safe Frame-1 JC-shine.

Same harness shape as verify_jump_on_grab. Different PASS criteria:

  - latency to KneeBend == 1 frame (action-state trigger working)
  - Fox transitions KneeBend -> aerial shine startup (0x016D) directly,
    WITHOUT passing through JumpF (0x0019). This is the "canonical
    jump-cancelled shine" property: shine input buffered during the
    last jumpsquat frame skips JumpF entirely.

If JumpF appears between KneeBend and 0x016D, B+down was issued late;
that's a "1-frame stale" JC. If 0x016D never appears, the shine
input wasn't received (counter advance bug, or B bit wrong).
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import scenario as sc
import candidate_d2 as cd


N_TRIALS = 3
AERIAL_SHINE = 0x016D
AERIAL_SHINE_LOOP = 0x016E


def main():
    h = Harness()
    try:
        print("staging meta-flush + jc-shine candidate", flush=True)
        iw.install_meta_flush(h)
        h.install_gecko_c2(
            name=cd.NAME,
            hook_addr=cd.HOOK_ADDR,
            logic_words=cd.LOGIC,
            displaced_orig=cd.DISPLACED_ORIG,
        )
        h.launch()
        h.hook_dme()

        print("waiting for CPU to tick ...", flush=True)
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

        print("\nseeding scenario (slot 2 + gecko overlay) ...", flush=True)
        h.seed_snapshot(timeout_s=60.0)

        def run_trial():
            h.reset()
            h.write_bytes(cd.COUNTER_ADDR, b"\x00")
            baseline = sc.record_window(h, 1)
            sc.force_action_state(h, 1, sc.CATCH)
            trigger_frame = h.frame()
            rest = sc.record_window(h, 14)
            return {"trigger_frame": trigger_frame,
                    "records": baseline + rest}

        def classify(trial):
            t = trial["trigger_frame"]
            records = trial["records"]
            # Build P2 transition list (only frames where p2 changed).
            transitions = []
            prev = None
            for r in records:
                if r["p2_action"] != prev:
                    transitions.append(
                        (r["frame"] - t, r["p2_action"]))
                    prev = r["p2_action"]
            # Latency to KneeBend = first frame after trigger where p2 = 0x18.
            kb_lat = next((lat for lat, st in transitions
                           if lat >= 0 and st == sc.KNEE_BEND), None)
            shine_lat = next((lat for lat, st in transitions
                              if lat >= 0 and st == AERIAL_SHINE), None)
            jumpf_seen = any(lat >= 0 and st == sc.JUMP_F
                             for lat, st in transitions)
            return {
                "transitions": transitions,
                "kb_latency": kb_lat,
                "shine_latency": shine_lat,
                "passed_through_jumpf": jumpf_seen,
            }

        print(f"\nrunning {N_TRIALS} trials ...", flush=True)
        passes = 0
        for i in range(N_TRIALS):
            trial = run_trial()
            cls = classify(trial)

            print(f"\ntrial {i + 1}:", flush=True)
            for r in trial["records"]:
                pre = ">>" if r["frame"] == trial["trigger_frame"] else "  "
                tag = ""
                if r["p2_action"] == sc.KNEE_BEND:
                    tag = " KneeBend"
                elif r["p2_action"] == sc.JUMP_F:
                    tag = " JumpF (BAD)"
                elif r["p2_action"] == AERIAL_SHINE:
                    tag = " AERIAL_SHINE_STARTUP"
                elif r["p2_action"] == AERIAL_SHINE_LOOP:
                    tag = " AERIAL_SHINE_LOOP"
                print(f"  {pre} f={r['frame']}  p1=0x{r['p1_action']:04X}  "
                      f"p2=0x{r['p2_action']:04X}{tag}", flush=True)

            print(f"  KneeBend at +{cls['kb_latency']} frames; "
                  f"aerial shine at +{cls['shine_latency']} frames; "
                  f"passed through JumpF = {cls['passed_through_jumpf']}",
                  flush=True)

            ok = (cls["kb_latency"] == 1
                  and cls["shine_latency"] is not None
                  and not cls["passed_through_jumpf"])
            if ok:
                print("  -> PASS (canonical JC-shine)", flush=True)
                passes += 1
            else:
                # Distinguish failure modes
                if cls["kb_latency"] is None:
                    print("  -> FAIL: never reached KneeBend (macro didn't fire)",
                          flush=True)
                elif cls["shine_latency"] is None:
                    print("  -> FAIL: KneeBend but no aerial shine "
                          "(shine input missed)", flush=True)
                elif cls["passed_through_jumpf"]:
                    print("  -> FAIL: shine landed but JumpF in between "
                          "(stale JC, shine input 1 frame late)", flush=True)
                else:
                    print(f"  -> FAIL: kb_lat={cls['kb_latency']}", flush=True)

        print(f"\n{passes}/{N_TRIALS} trials produced canonical JC-shine",
              flush=True)
        if passes >= 2:
            print("\n[PASS] netplay-safe Frame-1 JC-shine works", flush=True)
            return 0
        print("\n[FAIL]", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
