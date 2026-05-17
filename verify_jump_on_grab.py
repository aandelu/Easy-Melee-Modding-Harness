"""Smoke test for the Phase A "jump on grab" netplay-safe macro.

Boots Dolphin with two boot-time geckos staged: the meta-flush primitive
(harmless here; future-proofs runtime tweaks) and candidate_jump_on_grab.
Then seeds slot 2 / slot 1, runs N trials of force-Marth-to-Catch, and
classifies each trial's Fox transition.

PASS criteria: in >= 2 of 3 trials, Fox enters KneeBend (0x18) within
3 frames of the trigger -- the structural latency for an action-state-keyed
trigger is 2 frames (per MACRO_PLAN.md section 4D), so we allow a 1-frame
margin for any noise.
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import scenario as sc
import candidate_jump_on_grab as cjg


N_TRIALS = 3


def main():
    h = Harness()
    try:
        print("staging meta-flush + jump-on-grab", flush=True)
        iw.install_meta_flush(h)
        h.install_gecko_c2(
            name=cjg.NAME,
            hook_addr=cjg.HOOK_ADDR,
            logic_words=cjg.LOGIC,
            displaced_orig=cjg.DISPLACED_ORIG,
        )

        print("launching + hooking", flush=True)
        h.launch()
        h.hook_dme()

        # Wait for the bootloader and codehandler to settle, same as
        # verify_meta_flush.py.
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

        # seed_snapshot does the full F2-load + dme-overlay + save-slot-1
        # round-trip so both geckos survive the slot-2 wipe AND we have a
        # fast F1-based per-trial reset path.
        print("\nseeding scenario (slot 2 + gecko overlay) ...", flush=True)
        h.seed_snapshot(timeout_s=60.0)

        # Sanity: Marth=Wait, Fox=Wait, counter=0 at the reset point.
        p1 = h.action_state(1) & 0xFFFF
        p2 = h.action_state(2) & 0xFFFF
        counter = h.read_bytes(cjg.COUNTER_ADDR, 1)[0]
        print(f"\npost-seed state:", flush=True)
        print(f"  P1 (Marth) action = 0x{p1:04X}", flush=True)
        print(f"  P2 (Fox)   action = 0x{p2:04X}", flush=True)
        print(f"  counter byte      = 0x{counter:02X}", flush=True)
        if p1 != sc.WAIT or p2 != sc.WAIT:
            print(f"[FAIL] baseline not Wait/Wait", flush=True)
            return 1
        if counter != 0:
            print(f"[WARN] counter byte not zero -- macro will be one-shot "
                  f"and won't fire", flush=True)

        # Run trials. We need a custom loop because scenario.run_grab_trial
        # doesn't zero the counter scratch byte between reset and trigger --
        # the slot-1 savestate captured whatever garbage was at COUNTER_ADDR
        # in slot 2 (0x44 in practice), so the macro's "already fired" guard
        # rejects every fire attempt.
        def run_trial():
            h.reset()
            h.write_bytes(cjg.COUNTER_ADDR, b"\x00")
            baseline = sc.record_window(h, 1)
            sc.force_action_state(h, 1, sc.CATCH)
            trigger_frame = h.frame()
            rest = sc.record_window(h, 12)
            return {"trigger_frame": trigger_frame,
                    "records": baseline + rest}

        print(f"\nrunning {N_TRIALS} trials ...", flush=True)
        passed = 0
        for i in range(N_TRIALS):
            trial = run_trial()
            result = sc.classify_trial(trial)
            rf = result.get("latency_frames")
            rs = result.get("reaction_state", 0)
            print(f"\ntrial {i + 1}:", flush=True)
            print(f"  trigger_frame = {trial['trigger_frame']}", flush=True)
            for r in trial["records"]:
                pre = "  " if r["frame"] != trial["trigger_frame"] else ">>"
                kn = " KNEE_BEND" if r["p2_action"] == sc.KNEE_BEND else ""
                print(f"  {pre} f={r['frame']:6d}  p1=0x{r['p1_action']:04X}  "
                      f"p2=0x{r['p2_action']:04X}{kn}", flush=True)
            print(f"  reacted={result['reacted']}  latency={rf}  "
                  f"reaction_state=0x{rs:04X}", flush=True)
            if result["reacted"] and rf is not None and rf <= 3 \
                    and rs == sc.KNEE_BEND:
                passed += 1
                print("  -> PASS", flush=True)
            else:
                print("  -> FAIL", flush=True)

        print(f"\n{passed}/{N_TRIALS} trials passed", flush=True)
        if passed >= 2:
            print("\n[PASS] netplay-safe jump-on-grab works", flush=True)
            return 0
        print("\n[FAIL] jump-on-grab did not produce KneeBend reliably",
              flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
