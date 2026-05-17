"""exp04: brute-force write inputs to every plane we can reach, fast.

Goal: make Fox jump (KneeBend) using ONLY dme writes.

The engine reads inputs from:
  - PD+0x65C (digital buttons word)
  - PD+0x668 (instant buttons word) -- just-pressed delta
  - PD+0x620 (analog stick X, float)
  - PD+0x624 (analog stick Y, float)
  - 0x804C1FAC+0x44*port (global digital, "previous frame" + current)
  - 0x804C1FCC+0x44*port (global analog?)

Each frame, Dolphin's input pipeline:
  1. SI driver writes raw PADStatus
  2. HSD_PadRead copies PADStatus -> 0x804C1FAC
  3. Per-character copy: 0x804C1FAC -> PD+0x65C / PD+0x620 etc.
  4. Main character loop reads PD+* and processes transitions

Strategy: from a tight Python loop, write Y press into ALL planes
simultaneously, as fast as possible. The hope: at least ONE iteration
lands between step 3 and step 4 of some frame, and the engine sees our
Y press.

We track P2's action state per frame to see if/when Fox enters KneeBend.
"""
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (  # noqa: E402
    BIT_Y, BIT_B, BIT_DDOWN, BIT_Z, Harness, snapshot_frame, print_record,
    write_pd_buttons, write_pd_stick, write_global_buttons, write_global_stick,
    runs_dir, OFF_ANALOG_X, OFF_ANALOG_Y, OFF_INSTANT_BUTTONS,
)
from melee_harness import OFF_BUTTONS, CONTROLLER_DIGITAL, CONTROLLER_STRIDE  # noqa: E402


def fox_burst_y(h, port=2, duration_s=0.1):
    """Burst-write Y press to every plane for `duration_s`."""
    pd = h.player_data_ptr(port)
    glob_addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
    mask = BIT_Y
    t_end = time.time() + duration_s
    writes = 0
    while time.time() < t_end:
        h.write_words(pd + OFF_BUTTONS, [mask])
        h.write_words(pd + OFF_INSTANT_BUTTONS, [mask])
        h.write_bytes(glob_addr, struct.pack(">I", mask))
        writes += 1
    return writes


def trial(h, label, burst_duration_s, observe_frames=20):
    """Reset, burst inputs, observe."""
    h.reset()
    h.wait_frames(3)
    pre = snapshot_frame(h)
    records = [pre]
    n_writes = fox_burst_y(h, duration_s=burst_duration_s)
    # Sample for `observe_frames` frames after the burst
    for _ in range(observe_frames):
        rec = snapshot_frame(h)
        records.append(rec)
        h.wait_frames(1)
    reaction = None
    for r in records:
        if (r["p2_action"] & 0xFFFF) != 0x000E:
            reaction = (r["frame"], r["p2_action"])
            break
    return {"label": label, "writes": n_writes, "burst_s": burst_duration_s,
            "records": records, "reaction": reaction}


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        results = []
        for dur_s in (0.05, 0.10, 0.20, 0.50, 1.00):
            label = f"burst_{dur_s:.2f}s"
            print(f"\n=== {label} ===", flush=True)
            r = trial(h, label, burst_duration_s=dur_s)
            print(f"  {r['writes']} writes during burst")
            if r["reaction"]:
                print(f"  REACTED at frame {r['reaction'][0]}, "
                      f"state 0x{r['reaction'][1]:04X}")
            else:
                print("  no reaction")
            results.append(r)

        out = os.path.join(runs_dir(), "exp04_brute_force.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)

        print("\n=== SUMMARY ===")
        for r in results:
            mark = "REACTED" if r["reaction"] else "none"
            print(f"  {r['label']} (writes={r['writes']}): {mark}"
                  + (f" -> 0x{r['reaction'][1]:04X}" if r["reaction"] else ""))
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
