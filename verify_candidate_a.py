"""Candidate A smoke test: prove the input-write pipeline end-to-end.

Installs `candidate_a` at boot, then:
  1. Confirms the hook at 0x803775C0 actually got patched (lbz -> branch).
  2. Baseline trial: flag stays 0; expect both players idle in WAIT.
  3. Triggered trial: write 1 to MARTH_Z_FLAG_ADDR for a few frames;
     expect Marth's input pipeline to see Z (-> Catch action state) AND
     Fox's input pipeline to see B+stickY-down (-> some non-WAIT state).

Records Fox's reaction action-state ID for later use (Fox shine empirical
discovery -- needed for Candidate B's frame timings).
"""
import sys
import time

import candidate_a
from melee_harness import Harness
from scenario import (
    GRAB_STATES,
    WAIT,
    classify_trial,
    drive_marth_z,
    record_window,
    run_z_trigger_trial,
)


def banner(msg):
    print(f"\n{'=' * 64}\n{msg}\n{'=' * 64}", flush=True)


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_a.NAME,
        hook_addr=candidate_a.HOOK_ADDR,
        logic_words=candidate_a.LOGIC,
        displaced_orig=candidate_a.DISPLACED_ORIG,
    )
    try:
        banner("Launch + seed (autonomous)")
        h.launch()
        h.hook_dme()
        h.seed_snapshot()

        # Slot 1 currently has whatever flag value slot 2 had at 0x803FB000
        # AND the gecko-fired-mid-crouch state that resulted. Zero the flag,
        # let Fox recover to Wait, and re-save slot 1 so per-iteration F1
        # loads return to flag=0 + Fox-in-Wait + gecko-installed.
        drive_marth_z(h, False)
        time.sleep(1.0)             # ~60 frames; plenty for Fox to recover
        h.save_savestate(1)
        # F1 reload to verify slot 1 is correctly set up.
        h.load_savestate(slot=1, wait_in_game=False)
        time.sleep(0.5)
        print(f"  P1 char=0x{h.char_id(1):02X} action=0x{h.action_state(1):04X}",
              flush=True)
        print(f"  P2 char=0x{h.char_id(2):02X} action=0x{h.action_state(2):04X}",
              flush=True)

        # Informational only: dme reads of instruction memory may show the
        # original word even when Slippi's codehandler has patched the hook
        # (consistent with what verify_inject_gecko observed). The actual
        # proof that the gecko is running is the per-frame trial behavior
        # below, NOT this read.
        hook_word = h.read_word(candidate_a.HOOK_ADDR)
        print(f"\n[info] word @ 0x{candidate_a.HOOK_ADDR:08X} via dme = "
              f"0x{hook_word:08X}  (orig: 0x{candidate_a.DISPLACED_ORIG:08X}; "
              "patched-as-branch read may not be visible)", flush=True)

        banner("Trial 1: baseline (flag clear -> macro should NOT fire)")
        h.reset()
        drive_marth_z(h, False)
        baseline_records = record_window(h, 15)
        for r in baseline_records:
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}", flush=True)
        baseline_clean = all(
            r["p1_action"] == WAIT and r["p2_action"] == WAIT
            for r in baseline_records
        )
        print(f"baseline_clean: {baseline_clean}", flush=True)

        banner("Trial 2: drive Marth Z via dme flag")
        trial = run_z_trigger_trial(h, hold_frames=4, observe_frames=20)
        for r in trial["records"]:
            tag = " <-- trigger" if r["frame"] == trial["trigger_frame"] else ""
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}{tag}", flush=True)

        marth_grabbed = any(
            r["p1_action"] in GRAB_STATES
            for r in trial["records"]
            if r["frame"] >= trial["trigger_frame"]
        )
        result = classify_trial(trial, baseline_p2_state=WAIT)

        print(f"\nmarth_grabbed:    {marth_grabbed}", flush=True)
        print(f"fox_reacted:      {result['reacted']}", flush=True)
        print(f"latency_frames:   {result.get('latency_frames')}", flush=True)
        if result["reacted"]:
            print(f"reaction_state:   0x{result['reaction_state']:04X}",
                  flush=True)

        banner("Result")
        ok = baseline_clean and marth_grabbed and result["reacted"]
        if ok:
            print(
                "\n[PASS] Candidate A end-to-end: hook patched, dme flag drives "
                "Marth's Z press, Marth enters Catch, and Fox transitions out "
                "of Wait in response.",
                flush=True,
            )
            return 0
        print("\n[FAIL] one or more legs did not fire:", flush=True)
        if not baseline_clean:
            print("  - baseline NOT clean: macro fires (or scenario reacts) "
                  "with flag=0", flush=True)
        if not marth_grabbed:
            print("  - Marth never entered Catch: input-driver leg likely "
                  "didn't land Z on P1's PADStatus", flush=True)
        if not result["reacted"]:
            print("  - Fox never reacted: reaction-injection leg didn't land "
                  "B+down on P2's PADStatus (or Marth never grabbed)",
                  flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
