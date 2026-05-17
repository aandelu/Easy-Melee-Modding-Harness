"""
Task-#4 stage 1: end-to-end iteration-loop smoke test.

Runs one full iteration WITHOUT any macro installed (baseline). Expected:
  - Marth forced to Catch (0xD4) at the trigger frame.
  - Fox stays in Wait (0x0E) for the observation window (no macro to react).
  - latency_frames = None (no reaction).

This proves the scenario driver + observation pipeline works on top of the
all-dme harness. With this baseline locked down, task #4 stage 2 will install
a candidate macro via Harness.install_gecko_c2(...) and re-run -- Fox SHOULD
transition out of Wait if the macro fires.
"""
import sys

from melee_harness import Harness
from scenario import run_grab_trial, classify_trial, CATCH, WAIT


def banner(msg):
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}", flush=True)


def main():
    h = Harness()
    try:
        banner("Launch + seed (autonomous)")
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        print(f"  P1 char=0x{h.char_id(1):02X} action=0x{h.action_state(1):04X}",
              flush=True)
        print(f"  P2 char=0x{h.char_id(2):02X} action=0x{h.action_state(2):04X}",
              flush=True)

        banner("Trial 1: vanilla (no macro). Force Marth Catch, watch Fox.")
        trial = run_grab_trial(h, observe_frames=10)
        result = classify_trial(trial, baseline_p2_state=WAIT)

        print(f"trigger frame: {result['trigger_frame']}", flush=True)
        print("per-frame snapshot:", flush=True)
        for r in result["records"]:
            tag = " <-- trigger" if r["frame"] == result["trigger_frame"] else ""
            print(f"  f={r['frame']:>6}  p1=0x{r['p1_action']:04X}  "
                  f"p2=0x{r['p2_action']:04X}{tag}", flush=True)
        print(f"\nreacted: {result['reacted']}  "
              f"latency_frames: {result['latency_frames']}", flush=True)

        banner("Result")
        # Baseline expectations: Fox does NOT react (no macro installed).
        # Marth IS in Catch on the trigger frame (proves the dme write landed).
        trigger_frame_record = next(
            r for r in result["records"] if r["frame"] == result["trigger_frame"]
        )
        marth_was_in_catch = trigger_frame_record["p1_action"] in (
            CATCH, 0x00D5, 0x00D6
        )
        fox_didnt_react = not result["reacted"]
        ok = marth_was_in_catch and fox_didnt_react
        print(f"  marth_was_in_catch_at_trigger = {marth_was_in_catch}",
              flush=True)
        print(f"  fox_didnt_react_in_vanilla    = {fox_didnt_react}",
              flush=True)
        if ok:
            print("\n[PASS] scenario driver runs, trigger lands in MEM1, "
                  "and Fox stays neutral without a macro -- baseline locked.",
                  flush=True)
            return 0
        print("\n[FAIL] baseline behavior not as expected -- see snapshots above.",
              flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
