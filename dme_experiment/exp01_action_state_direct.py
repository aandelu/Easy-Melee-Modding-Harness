"""exp01: confirm whether writing PD+0x10 (action state) alone does anything.

Per `SSBM memory address sheet/Char_Data_Offsets.csv`:
    0x10 -> Action State -- The value at this address indicates the action
            state of the character. Modifying this alone does nothing.

The previous session used `force_action_state` (a direct PD+0x10 write) to
trigger Marth's Catch state and the scenarios appeared to react -- so the
claim must be at least partially false (or only true in some sense). This
script tests both directions:

  A) Write Fox into Catch (0xD4): does the state hold? Does Fox actually
     start grabbing? (Visually no, presumably -- the engine reads PD+0x10
     during state-machine ticks but the animation pointer / sub-action
     state / etc. don't get reset.)

  B) Write Fox into aerial shine (0x016D) from Wait (0x000E): does the
     state hold? Does Fox actually shine? If "modifying alone does nothing"
     is strict, the state should immediately snap back to Wait.

  C) Write Fox into KneeBend (0x0018): does the engine count out 3 frames
     and transition to JumpF?

For each test we sample PD+0x10 for the next 12 frames after the write.
The result determines whether direct action-state writes are a viable
shortcut for reproducing JC-shine, or if we MUST drive inputs.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (  # noqa: E402
    Harness, snapshot_frame, print_record, runs_dir,
)
from melee_harness import OFF_ACTION_STATE  # noqa: E402
import scenario  # noqa: E402


def force_state(h, port, state):
    pd = h.player_data_ptr(port)
    h.write_words(pd + OFF_ACTION_STATE, [state])


def run_test(h, label, port, state, n_frames=12):
    h.reset()
    h.wait_frames(2)
    pre = snapshot_frame(h)
    print(f"[{label}] pre-write: f={pre['frame']} "
          f"p{port}_action=0x{pre[f'p{port}_action']:04X}", flush=True)
    force_state(h, port, state)
    print(f"[{label}] wrote PD+0x10 = 0x{state:04X} on port {port}", flush=True)
    records = []
    for i in range(n_frames):
        rec = snapshot_frame(h)
        records.append(rec)
        print_record(rec, prefix=f"  [{label} +{i}] ")
        h.wait_frames(1)
    return {"label": label, "port": port, "wrote_state": state, "pre": pre,
            "records": records}


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        # Save a clean slot 1 we can return to.
        h.save_savestate(1)

        results = []
        results.append(run_test(h, "A_fox_catch", port=2, state=0x00D4))
        results.append(run_test(h, "B_fox_aerialshine", port=2, state=0x016D))
        results.append(run_test(h, "C_fox_kneebend", port=2, state=0x0018))
        results.append(run_test(h, "D_marth_catch", port=1, state=0x00D4))

        out = os.path.join(runs_dir(), "exp01_action_state.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)

        # Summarize
        print("\n=== SUMMARY ===")
        for r in results:
            held = sum(
                1 for x in r["records"]
                if (x[f"p{r['port']}_action"] & 0xFFFF) == r["wrote_state"]
            )
            other = set(
                f"0x{x[f'p{r['port']}_action']:04X}" for x in r["records"]
                if (x[f"p{r['port']}_action"] & 0xFFFF) != r["wrote_state"]
            )
            print(f"{r['label']}: wrote 0x{r['wrote_state']:04X}, "
                  f"observed_held={held}/{len(r['records'])} frames, "
                  f"other_states_observed={sorted(other)}")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
