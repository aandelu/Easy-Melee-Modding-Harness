"""Candidate D.1 verification: action-state-keyed JC-shine.

D.1 fires when Marth's action state ∈ {0xD4, 0xD5, 0xD6} (Catch/CatchDash/
CatchTurn), NOT when a dme flag is set. To test offline, drive Marth into
Catch via direct dme write to his Player Data action-state word -- that's
exactly what scenario.run_grab_trial already does.

Expected timing (one frame later than B.3 by construction):
  - run_grab_trial samples 1 baseline frame, then writes CATCH to Marth's
    action state. Call this write-time frame T.
  - Frame T+1: Marth observed in Catch (the write took effect; Fox's pad
    pass for this frame sees Marth in Catch).
  - Frame T+2: Fox in KneeBend (gecko's Y press from previous pad pass
    consumed).
  - Frames T+3, T+4: Fox in KneeBend (jumpsquat).
  - Frame T+5: Fox in 0x016D aerial shine startup.
  - Frame T+6+: shine continues.
"""
import sys
import time

import candidate_d
from melee_harness import Harness
from scenario import (
    FOX_SHINE_GROUND_LOOP,
    FOX_SHINE_GROUND_START,
    GRAB_STATES,
    JUMP_F,
    KNEE_BEND,
    WAIT,
    classify_trial,
    drive_marth_z,
    record_window,
    reset_b2_counter,
    run_grab_trial,
)


def banner(msg):
    print(f"\n{'=' * 64}\n{msg}\n{'=' * 64}", flush=True)


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d.NAME,
        hook_addr=candidate_d.HOOK_ADDR,
        logic_words=candidate_d.LOGIC,
        displaced_orig=candidate_d.DISPLACED_ORIG,
    )
    try:
        banner("Launch + seed (autonomous)")
        h.launch()
        h.hook_dme()
        h.seed_snapshot()

        # D.1 no longer uses the dme Z-press flag, but the counter is still
        # the macro's state. Zero it so slot 1 starts clean. Also clear the
        # flag byte just to keep the address inert (older candidates might
        # have left it non-zero; harmless either way).
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

        banner("Trial 1: baseline (Marth stays in Wait -> macro must NOT fire)")
        h.reset()
        baseline_records = record_window(h, 15)
        for r in baseline_records:
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}", flush=True)
        baseline_clean = all(
            r["p1_action"] == WAIT and r["p2_action"] == WAIT
            for r in baseline_records
        )
        counter_baseline = h.read_word(candidate_d.COUNTER_ADDR) >> 24
        word_after_baseline = h.read_word(candidate_d.COUNTER_ADDR + 4)
        print(f"baseline_clean: {baseline_clean}", flush=True)
        print(f"counter after baseline trial: 0x{counter_baseline:02X}",
              flush=True)
        print(f"[diag] word @ 0x{candidate_d.COUNTER_ADDR + 4:08X} AFTER baseline = "
              f"0x{word_after_baseline:08X}  "
              f"(diag halfword at +0x42A = 0x{word_after_baseline & 0xFFFF:04X})",
              flush=True)

        banner("Trial 2: force Marth into Catch -> expect Fox JC-shine")
        trial = run_grab_trial(h, observe_frames=30)
        for r in trial["records"]:
            tag = " <-- trigger" if r["frame"] == trial["trigger_frame"] else ""
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}{tag}", flush=True)

        post = [r for r in trial["records"]
                if r["frame"] >= trial["trigger_frame"]]
        marth_grabbed = any(r["p1_action"] in GRAB_STATES for r in post)
        fox_kneebent = any(r["p2_action"] == KNEE_BEND for r in post)
        fox_jumpf_frames = [r for r in post if r["p2_action"] == JUMP_F]
        known_shine = {FOX_SHINE_GROUND_START, FOX_SHINE_GROUND_LOOP,
                       0x016D, 0x016E, 0x0170}
        shine_records = [r for r in post if r["p2_action"] in known_shine]
        first_shine = shine_records[0] if shine_records else None

        # Reaction latency from Marth's first-observed grab frame to Fox's
        # first-observed shine state.
        marth_grab_records = [r for r in post if r["p1_action"] in GRAB_STATES]
        first_marth_grab = marth_grab_records[0] if marth_grab_records else None
        reaction_latency = None
        if first_marth_grab and first_shine:
            reaction_latency = first_shine["frame"] - first_marth_grab["frame"]

        terminal_counter = h.read_word(candidate_d.COUNTER_ADDR) >> 24
        sentinel_byte = h.read_word(candidate_d.COUNTER_ADDR + 4) >> 24
        # 0x803FA428..0x803FA42B word; sth at +6 writes bytes 0x42A-0x42B.
        scratch_word = h.read_word(candidate_d.COUNTER_ADDR + 8)  # 0x803FA42C? off by 4...
        # Read the word containing 0x803FA42A: that's the word starting at 0x803FA428.
        word_428 = h.read_word(candidate_d.COUNTER_ADDR + 4)
        halfword_42A = word_428 & 0xFFFF

        print(f"\n[diag] scratch sentinel byte @ 0x{candidate_d.COUNTER_ADDR + 4:08X} "
              f"= 0x{sentinel_byte:02X}  (0x42 means trigger arm DID fire)",
              flush=True)
        print(f"[diag] halfword @ 0x{candidate_d.COUNTER_ADDR + 6:08X} "
              f"= 0x{halfword_42A:04X}  (0x00D4 means we reached pos 15 AND read Marth's state)",
              flush=True)

        print(f"\nmarth_grabbed:        {marth_grabbed}", flush=True)
        print(f"fox_entered_kneebend: {fox_kneebent}", flush=True)
        print(f"fox_jumpf_frames:     {len(fox_jumpf_frames)} "
              "(canonical JC-shine wants 0)", flush=True)
        if first_marth_grab:
            print(f"marth_grab_first:     frame {first_marth_grab['frame']} "
                  f"-> 0x{first_marth_grab['p1_action']:04X}", flush=True)
        if first_shine:
            print(f"fox_shine_first:      frame {first_shine['frame']} "
                  f"-> 0x{first_shine['p2_action']:04X}", flush=True)
        if reaction_latency is not None:
            print(f"shine_latency_frames: {reaction_latency} "
                  "(Marth's grab visible -> Fox's shine visible)", flush=True)
        print(f"terminal counter:     0x{terminal_counter:02X} (expect 0x06)",
              flush=True)

        banner("Result")
        ok = (baseline_clean and marth_grabbed and fox_kneebent
              and bool(first_shine))
        if ok:
            extra = " (canonical -- no JumpF)" if not fox_jumpf_frames else (
                f" ({len(fox_jumpf_frames)} JumpF frame(s) -- stale by 1)")
            print(f"\n[PASS] Candidate D.1: action-state trigger fired JC-shine"
                  f"{extra}. Marth's Catch state was detected and Fox "
                  f"executed the JC-shine sequence.", flush=True)
            return 0
        print("\n[FAIL] one or more legs did not fire:", flush=True)
        if not baseline_clean:
            print("  - baseline NOT clean (macro fired without a Marth grab)",
                  flush=True)
        if not marth_grabbed:
            print("  - Marth never entered Catch (harness force_action_state "
                  "didn't take effect)", flush=True)
        if not fox_kneebent:
            print("  - Fox never entered KneeBend (Y press leg broken, or "
                  "action-state read produced wrong value)", flush=True)
        if not first_shine:
            print("  - Fox never entered a shine state (B+down leg didn't "
                  "land, or shine input timing is off)", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
