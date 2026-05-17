"""exp02: which button plane does Fox's engine logic actually consume?

We have 3 candidate planes accessible via dme (PADStatus we don't yet have an
address for; gecko hooks it via r25 at runtime):
  PLANE_GLOBAL    = 0x804C1FAC + 0x44*(port-1)        # processed digital
  PLANE_PD        = PD + 0x65C                        # per-player digital
  PLANE_INSTANT   = PD + 0x668                        # per-player instant
  PLANE_STICK     = PD + 0x620/0x624 (floats)         # per-player analog

Test: from Fox in Wait (0x000E), write a Y press into each plane for one
frame, observe whether Fox enters KneeBend (0x0018). Repeat for several
hold-duration variants because Melee's input system may require a button
to be pressed across multiple frames (just-pressed detection).

We try writing for 1, 2, 3, 5 frames. After each test we reset back to
the seeded slot so each test starts clean.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (  # noqa: E402
    BIT_Y, BIT_B, BIT_DDOWN, BIT_Z, Harness, snapshot_frame, print_record,
    write_pd_buttons, clear_pd_buttons, write_global_buttons,
    write_pd_stick, write_global_stick, runs_dir,
)
from melee_harness import OFF_BUTTONS, CONTROLLER_DIGITAL, CONTROLLER_STRIDE  # noqa: E402


def _zero_global(h, port):
    """Zero a port's global digital word."""
    addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
    h.write_bytes(addr, b"\x00\x00\x00\x00")


def _zero_pd_buttons(h, port):
    pd = h.player_data_ptr(port)
    if pd == -1:
        return
    h.write_words(pd + OFF_BUTTONS, [0])
    h.write_words(pd + 0x668, [0])


def trial(h, plane, hold_frames, observe_frames=12, button_mask=BIT_Y):
    """Run one trial: reset, write Y to plane for hold_frames, observe."""
    h.reset()
    h.wait_frames(2)
    pre = snapshot_frame(h)
    records = [pre]

    for f in range(hold_frames):
        if plane == "global":
            write_global_buttons(h, 2, button_mask)
        elif plane == "pd":
            write_pd_buttons(h, 2, button_mask)
        elif plane == "pd_only_065c":
            pd = h.player_data_ptr(2)
            cur = h.read_word(pd + OFF_BUTTONS)
            h.write_words(pd + OFF_BUTTONS, [cur | button_mask])
        elif plane == "pd_only_0668":
            pd = h.player_data_ptr(2)
            cur = h.read_word(pd + 0x668)
            h.write_words(pd + 0x668, [cur | button_mask])
        elif plane == "global_and_pd":
            write_global_buttons(h, 2, button_mask)
            write_pd_buttons(h, 2, button_mask)
        elif plane == "noop":
            pass
        else:
            raise ValueError(plane)
        rec = snapshot_frame(h)
        records.append(rec)
        h.wait_frames(1)

    # Clear and let it settle
    if plane in ("pd", "pd_only_065c", "pd_only_0668", "global_and_pd"):
        clear_pd_buttons(h, 2, button_mask)
    _zero_global(h, 2)
    for _ in range(observe_frames):
        rec = snapshot_frame(h)
        records.append(rec)
        h.wait_frames(1)
    return records


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        results = {}
        for plane in ("noop", "global", "pd", "pd_only_065c",
                      "pd_only_0668", "global_and_pd"):
            for hold in (1, 3, 5):
                key = f"{plane}_hold{hold}"
                print(f"\n=== trial: {key} ===", flush=True)
                records = trial(h, plane, hold_frames=hold)
                # Did Fox leave Wait (0x000E)?
                reaction = None
                for r in records:
                    if (r["p2_action"] & 0xFFFF) != 0x000E:
                        reaction = (r["frame"], r["p2_action"])
                        break
                results[key] = {
                    "records": records,
                    "reaction": reaction,
                }
                if reaction:
                    print(f"  REACTED at frame {reaction[0]}, "
                          f"state 0x{reaction[1]:04X}")
                else:
                    print(f"  no reaction (Fox stayed in 0x000E for all "
                          f"{len(records)} sampled frames)")

        out = os.path.join(runs_dir(), "exp02_button_planes.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
        print("\n=== SUMMARY ===")
        for k, v in results.items():
            r = v["reaction"]
            mark = "REACTED" if r else "none"
            print(f"  {k}: {mark}"
                  + (f" -> 0x{r[1]:04X} at f={r[0]}" if r else ""))
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
