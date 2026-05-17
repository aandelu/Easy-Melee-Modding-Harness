"""Candidate B.2 verification: full jump-cancelled shine sequence.

Drives the dme flag once. Expected per-frame timeline (using B.1's empirical
3-frame jumpsquat data):

  trigger frame T:    flag set; both players still in Wait at sample time.
  frame T+1:          Marth -> Catch (0x00D4); Fox -> KneeBend (0x0018).
                      counter advanced 0 -> 1 on this pad pass.
  frame T+2:          Fox in KneeBend (jumpsquat frame 1). counter 1 -> 2.
  frame T+3:          Fox in KneeBend (jumpsquat frame 2). counter 2 -> 3.
  frame T+4:          Fox transitions out of KneeBend. counter 3 -> 4.
                      First B+down input lands on this pad pass.
  frame T+5:          Fox should be in aerial shine (state ID TBD, expected
                      to be Fox-specific, > 0x154). counter 4 -> 5.
  frame T+6+:         Continued shine input; observe loop / fall transitions.

Key empirical questions to answer:
  - Does B+down on the JumpF frame produce aerial shine, or does Fox stay in
    JumpF? (If the latter, the JC-shine timing needs to fire B+down one
    frame earlier, i.e. on counter==3 instead of counter==4.)
  - What is Fox's aerial shine action state ID? Compare to the ground shine
    states 0x0168 / 0x0169 discovered in Candidate A.
"""
import sys
import time

import candidate_b
from melee_harness import Harness
from scenario import (
    FOX_SHINE_GROUND_LOOP,
    FOX_SHINE_GROUND_START,
    GRAB_STATES,
    JUMP_F,
    KNEE_BEND,
    WAIT,
    drive_marth_z,
    record_window,
    reset_b2_counter,
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

        # Clear flag AND counter, let Fox settle, then re-save slot 1.
        # The counter MUST be zero in slot 1 -- otherwise the per-trial F1
        # reload restores a non-zero starting counter and the macro fires
        # mid-state instead of from the trigger.
        drive_marth_z(h, False)
        reset_b2_counter(h)
        time.sleep(1.0)
        h.save_savestate(1)
        h.load_savestate(slot=1, wait_in_game=False)
        time.sleep(0.5)
        print(f"  P1 char=0x{h.char_id(1):02X} action=0x{h.action_state(1):04X}",
              flush=True)
        print(f"  P2 char=0x{h.char_id(2):02X} action=0x{h.action_state(2):04X}",
              flush=True)
        counter_pre = h.read_word(candidate_b.COUNTER_ADDR) >> 24
        print(f"  counter byte @ 0x{candidate_b.COUNTER_ADDR:08X} = 0x{counter_pre:02X}",
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
        counter_baseline = h.read_word(candidate_b.COUNTER_ADDR) >> 24
        print(f"baseline_clean: {baseline_clean}", flush=True)
        print(f"counter after baseline trial: 0x{counter_baseline:02X}",
              flush=True)

        banner("Trial 2: drive Marth Z via dme flag -> expect JC shine")
        # Hold flag long enough for the macro counter to advance past 6 so
        # B+down lands across frames 4, 5, 6. observe_frames=30 to see Fox
        # finish the shine and start falling/transitioning.
        trial = run_z_trigger_trial(h, hold_frames=8, observe_frames=30)
        for r in trial["records"]:
            tag = " <-- trigger" if r["frame"] == trial["trigger_frame"] else ""
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}{tag}", flush=True)

        marth_grabbed = any(
            r["p1_action"] in GRAB_STATES
            for r in trial["records"]
            if r["frame"] >= trial["trigger_frame"]
        )

        post = [r for r in trial["records"]
                if r["frame"] >= trial["trigger_frame"]]
        fox_kneebent = any(r["p2_action"] == KNEE_BEND for r in post)
        fox_jumpf = any(r["p2_action"] == JUMP_F for r in post)
        fox_shine_ground = any(
            r["p2_action"] in (FOX_SHINE_GROUND_START, FOX_SHINE_GROUND_LOOP)
            for r in post
        )
        # Aerial shine ID is unknown; flag any Fox state above 0x154 that
        # isn't already in our known set as a candidate. JumpF (0x19) and
        # KneeBend (0x18) are universal.
        known_states = {
            WAIT, KNEE_BEND, JUMP_F, 0x001A, 0x001B, 0x001C, 0x001D,
            FOX_SHINE_GROUND_START, FOX_SHINE_GROUND_LOOP,
        }
        novel_states = sorted(
            {r["p2_action"] for r in post if r["p2_action"] not in known_states}
        )
        terminal_counter = h.read_word(candidate_b.COUNTER_ADDR) >> 24

        print(f"\nmarth_grabbed:        {marth_grabbed}", flush=True)
        print(f"fox_entered_kneebend: {fox_kneebent}", flush=True)
        print(f"fox_entered_jumpf:    {fox_jumpf}", flush=True)
        print(f"fox_shine_ground:     {fox_shine_ground} (unexpected for "
              "airborne shine -- ground shine means B+down landed before "
              "Fox left the ground)", flush=True)
        print(f"novel_fox_states:     {[f'0x{s:04X}' for s in novel_states]} "
              "(likely aerial shine / fall transitions)", flush=True)
        print(f"terminal counter:     0x{terminal_counter:02X} (expect 0x07)",
              flush=True)

        banner("Result")
        # Pass criteria for B.2: same as B.1 PLUS some shine-like transition
        # after the Y press. Aerial shine state ID is unknown ahead of time
        # so we accept ANY transition out of JumpF other than a normal fall
        # (0x1D Fall) or known jump variants (0x1A-0x1C).
        non_jump_post_kb = [
            r for r in post
            if r["p2_action"] not in {KNEE_BEND, WAIT, JUMP_F,
                                     0x001A, 0x001B, 0x001C, 0x001D}
        ]
        fox_shined = bool(non_jump_post_kb) or fox_shine_ground
        ok = baseline_clean and marth_grabbed and fox_kneebent and fox_shined
        if ok:
            first_shine = non_jump_post_kb[0] if non_jump_post_kb else None
            label = (f"ground shine 0x{FOX_SHINE_GROUND_START:04X}"
                     if fox_shine_ground and not non_jump_post_kb
                     else (f"frame {first_shine['frame']} -> "
                           f"0x{first_shine['p2_action']:04X}"
                           if first_shine else "?"))
            print(f"\n[PASS] Candidate B.2: JC-shine sequence executed. "
                  f"First non-jump Fox state: {label}.", flush=True)
            return 0
        print("\n[FAIL] one or more legs did not fire:", flush=True)
        if not baseline_clean:
            print("  - baseline NOT clean", flush=True)
        if not marth_grabbed:
            print("  - Marth never entered Catch", flush=True)
        if not fox_kneebent:
            print("  - Fox never entered KneeBend (Y press leg broken)",
                  flush=True)
        if not fox_shined:
            print("  - Fox never transitioned to a shine-like state "
                  "(B+down leg either landed at wrong frame or didn't "
                  "land at all). Try shifting the shine window earlier "
                  "(counter 3..5 instead of 4..6).", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
