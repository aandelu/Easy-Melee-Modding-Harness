"""Candidate B.1 smoke test: prove Y press makes Fox jump.

Installs `candidate_b` at boot, then:
  1. Baseline trial: flag stays 0; expect both players idle in WAIT.
  2. Triggered trial: write 1 to MARTH_Z_FLAG_ADDR for a few frames; expect
     Marth's pipeline to see Z (-> Catch action state) AND Fox's pipeline to
     see Y (-> KneeBend / JumpF action states).

Records Fox's per-frame action state for empirical discovery of the
jumpsquat -> airborne timing (MACRO_PLAN section 11: "Are jumpsquat frames
1-4 or 1-3?"). This is the data B.2 will use to time the B+down (shine)
press in the full jump-cancelled sequence.
"""
import sys
import time

import candidate_b
from melee_harness import Harness
from scenario import (
    GRAB_STATES,
    KNEE_BEND,
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
        name=candidate_b.NAME,
        hook_addr=candidate_b.HOOK_ADDR,
        logic_words=candidate_b.LOGIC,
        displaced_orig=candidate_b.DISPLACED_ORIG,
    )
    try:
        banner("Launch + seed (autonomous)")
        h.launch()
        h.hook_dme()
        h.seed_snapshot()

        # Slot 1 currently has whatever flag value slot 2 had at 0x803FB000
        # AND whatever gecko-fired state resulted. Zero the flag, let Fox
        # recover to Wait, and re-save slot 1 so per-iteration F1 loads
        # return to flag=0 + Fox-in-Wait + gecko-installed.
        drive_marth_z(h, False)
        time.sleep(1.0)
        h.save_savestate(1)
        h.load_savestate(slot=1, wait_in_game=False)
        time.sleep(0.5)
        print(f"  P1 char=0x{h.char_id(1):02X} action=0x{h.action_state(1):04X}",
              flush=True)
        print(f"  P2 char=0x{h.char_id(2):02X} action=0x{h.action_state(2):04X}",
              flush=True)

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

        banner("Trial 2: drive Marth Z + Fox Y via dme flag")
        # Hold flag long enough to span jumpsquat (~3-4 frames) + initial jump.
        # Observe 25 frames to see Fox transition out of KneeBend into JumpF
        # (or whichever airborne state). This data informs B.2's frame timing.
        trial = run_z_trigger_trial(h, hold_frames=6, observe_frames=25)
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
        fox_kneebent = any(
            r["p2_action"] == KNEE_BEND
            for r in trial["records"]
            if r["frame"] >= trial["trigger_frame"]
        )

        # Empirical: how many frames does Fox spend in KneeBend before leaving?
        kneebend_frames = [
            r["frame"] for r in trial["records"]
            if r["frame"] >= trial["trigger_frame"]
            and r["p2_action"] == KNEE_BEND
        ]
        post_kneebend = [
            r for r in trial["records"]
            if kneebend_frames and r["frame"] > max(kneebend_frames)
        ]
        first_post_kneebend = post_kneebend[0] if post_kneebend else None

        print(f"\nmarth_grabbed:        {marth_grabbed}", flush=True)
        print(f"fox_reacted:          {result['reacted']}", flush=True)
        print(f"fox_entered_kneebend: {fox_kneebent}", flush=True)
        print(f"latency_frames:       {result.get('latency_frames')}", flush=True)
        if result["reacted"]:
            print(f"reaction_state:       0x{result['reaction_state']:04X}",
                  flush=True)
        if kneebend_frames:
            print(f"kneebend_frame_span:  {min(kneebend_frames)}..{max(kneebend_frames)} "
                  f"({len(kneebend_frames)} frames)", flush=True)
        if first_post_kneebend:
            print(f"after_kneebend:       frame {first_post_kneebend['frame']} -> "
                  f"0x{first_post_kneebend['p2_action']:04X}", flush=True)

        banner("Result")
        # B.1 is purely a smoke test for the Y-press leg. Fox MUST enter
        # KneeBend (jumpsquat). The Marth Z leg is regression-tested too --
        # if it stops working, the harness drifted somehow.
        ok = baseline_clean and marth_grabbed and fox_kneebent
        if ok:
            print(
                "\n[PASS] Candidate B.1: Y press lands on Fox's PADStatus and "
                "transitions him into KneeBend (jumpsquat). Empirical "
                "jumpsquat-duration data above will drive B.2's shine timing.",
                flush=True,
            )
            return 0
        print("\n[FAIL] one or more legs did not fire:", flush=True)
        if not baseline_clean:
            print("  - baseline NOT clean: macro fires with flag=0", flush=True)
        if not marth_grabbed:
            print("  - Marth never entered Catch: regression in Z-press leg",
                  flush=True)
        if not fox_kneebent:
            print("  - Fox never entered KneeBend: Y press didn't land, or "
                  "jumpsquat state ID is something other than 0x0018",
                  flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
