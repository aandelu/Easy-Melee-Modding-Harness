"""Candidate B.3: shine input shifted one frame earlier than B.2.

Same scenario as verify_candidate_b2.py but installing candidate_b3 instead.
Key pass-criterion difference: Fox should transition KneeBend -> aerial
shine WITHOUT visible JumpF (0x0019). If JumpF appears in the trial
records, B.3 fired too late and we need to shift earlier still (counter
2..4) -- though that risks the engine ignoring the B input during
jumpsquat entirely.
"""
import sys
import time

import candidate_b3
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
        name=candidate_b3.NAME,
        hook_addr=candidate_b3.HOOK_ADDR,
        logic_words=candidate_b3.LOGIC,
        displaced_orig=candidate_b3.DISPLACED_ORIG,
    )
    try:
        banner("Launch + seed (autonomous)")
        h.launch()
        h.hook_dme()
        h.seed_snapshot()

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

        banner("Trial 1: baseline")
        h.reset()
        drive_marth_z(h, False)
        baseline_records = record_window(h, 15)
        baseline_clean = all(
            r["p1_action"] == WAIT and r["p2_action"] == WAIT
            for r in baseline_records
        )
        print(f"baseline_clean: {baseline_clean}", flush=True)

        banner("Trial 2: JC-shine with earlier B+down timing")
        trial = run_z_trigger_trial(h, hold_frames=8, observe_frames=25)
        for r in trial["records"]:
            tag = " <-- trigger" if r["frame"] == trial["trigger_frame"] else ""
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}{tag}", flush=True)

        post = [r for r in trial["records"]
                if r["frame"] >= trial["trigger_frame"]]
        marth_grabbed = any(r["p1_action"] in GRAB_STATES for r in post)
        fox_kneebent = any(r["p2_action"] == KNEE_BEND for r in post)
        fox_jumpf_frames = [r for r in post if r["p2_action"] == JUMP_F]
        # Discover Fox's first non-KneeBend / non-Wait state after trigger.
        first_non_wait_non_kb = next(
            (r for r in post
             if r["p2_action"] not in (WAIT, KNEE_BEND)),
            None,
        )

        terminal_counter = h.read_word(candidate_b3.COUNTER_ADDR) >> 24

        print(f"\nmarth_grabbed:        {marth_grabbed}", flush=True)
        print(f"fox_entered_kneebend: {fox_kneebent}", flush=True)
        print(f"fox_jumpf_frames:     {len(fox_jumpf_frames)} "
              "(canonical JC-shine wants 0)", flush=True)
        if first_non_wait_non_kb:
            print(f"first_post_kb_state:  frame {first_non_wait_non_kb['frame']} "
                  f"-> 0x{first_non_wait_non_kb['p2_action']:04X}", flush=True)
        print(f"terminal counter:     0x{terminal_counter:02X} (expect 0x06)",
              flush=True)

        banner("Result")
        # Canonical JC-shine: KneeBend -> aerial shine (0x016D) with NO
        # JumpF in between. B.3 passes if no JumpF frames are present.
        no_jumpf = len(fox_jumpf_frames) == 0
        # Sanity: Fox must still react. Use ground-shine OR known-aerial set.
        known_shine = {FOX_SHINE_GROUND_START, FOX_SHINE_GROUND_LOOP,
                       0x016D, 0x016E, 0x0170}
        fox_shined = any(r["p2_action"] in known_shine for r in post)

        ok = baseline_clean and marth_grabbed and fox_kneebent and fox_shined
        clean_jc = ok and no_jumpf

        if clean_jc:
            print("\n[PASS] Candidate B.3: canonical JC-shine (KneeBend "
                  "-> aerial shine, no visible JumpF).", flush=True)
            return 0
        if ok and not no_jumpf:
            print(f"\n[PARTIAL] B.3 produces shine but Fox still passes "
                  f"through JumpF for {len(fox_jumpf_frames)} frame(s). "
                  f"Shine input may need to land one frame earlier still.",
                  flush=True)
            return 2
        print("\n[FAIL] one or more legs did not fire:", flush=True)
        if not baseline_clean:
            print("  - baseline NOT clean", flush=True)
        if not marth_grabbed:
            print("  - Marth never entered Catch", flush=True)
        if not fox_kneebent:
            print("  - Fox never entered KneeBend", flush=True)
        if not fox_shined:
            print("  - Fox never entered a known shine state", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
