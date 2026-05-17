"""exp05: reproduce JC-shine via dme writes only.

Sequence:
  - frame T:       burst Y for ~1 frame -> Fox enters KneeBend
  - frame T+1, T+2: KneeBend jumpsquat
  - frame T+3:     burst B + analog-Y=-1.0 for ~1 frame (last KneeBend)
                   -- engine buffers input, transitions KneeBend -> aerial
                      shine 0x016D directly (no JumpF visible)
  - frame T+4+:    Fox in aerial shine

Empirical question: does the engine accept buffered B+down input written
ONLY to the engine planes (PD+0x65C, PD+0x668, PD+0x620/0x624, and
0x804C1FAC)? The gecko writes raw PADStatus (the earlier plane) and
that works. We're testing whether the later planes are sufficient.

We try several burst configurations to find one that produces canonical
JC-shine (0x016D without visible 0x0019 JumpF).
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


def burst_inputs(h, port, mask, duration_s, stick_y=None):
    """Burst-write `mask` to all button planes + stick_y to analog Y for
    `duration_s`. Returns number of writes."""
    pd = h.player_data_ptr(port)
    glob_addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
    sy_bytes = struct.pack(">f", stick_y) if stick_y is not None else None
    t_end = time.time() + duration_s
    writes = 0
    while time.time() < t_end:
        h.write_words(pd + OFF_BUTTONS, [mask])
        h.write_words(pd + OFF_INSTANT_BUTTONS, [mask])
        h.write_bytes(glob_addr, struct.pack(">I", mask))
        if sy_bytes is not None:
            h.write_bytes(pd + OFF_ANALOG_Y, sy_bytes)
            h.write_bytes(glob_addr + 0x24, sy_bytes)
        writes += 1
    return writes


def trial(h, label, y_burst_s, gap_frames, shine_burst_s, observe_frames=15):
    """Run one JC-shine trial.

    y_burst_s   - duration of Y burst (jump)
    gap_frames  - frames to wait after Y burst before shine burst
    shine_burst_s - duration of B+down burst
    observe_frames - frames to observe after shine burst
    """
    h.reset()
    h.wait_frames(3)
    records = [snapshot_frame(h)]

    # Burst Y
    t0 = time.time()
    n_y = burst_inputs(h, 2, BIT_Y, y_burst_s)
    t_y = time.time() - t0
    records.append(snapshot_frame(h))

    # Wait the gap
    for _ in range(gap_frames):
        h.wait_frames(1)
        records.append(snapshot_frame(h))

    # Burst B + down
    t0 = time.time()
    n_shine = burst_inputs(h, 2, BIT_B, shine_burst_s, stick_y=-1.0)
    t_shine = time.time() - t0
    records.append(snapshot_frame(h))

    # Observe
    for _ in range(observe_frames):
        h.wait_frames(1)
        records.append(snapshot_frame(h))

    return {
        "label": label, "y_burst_s": y_burst_s, "gap_frames": gap_frames,
        "shine_burst_s": shine_burst_s, "n_y_writes": n_y,
        "n_shine_writes": n_shine,
        "y_burst_actual_s": t_y, "shine_burst_actual_s": t_shine,
        "records": records,
    }


def analyze(t):
    """Pretty-print one trial and identify if JC-shine happened."""
    records = t["records"]
    states_seen = []
    for r in records:
        s = r["p2_action"] & 0xFFFF
        if not states_seen or states_seen[-1] != s:
            states_seen.append(s)
    states_str = [f"0x{s:04X}" for s in states_seen]
    print(f"  [{t['label']}] states (dedup): {' -> '.join(states_str)}")
    print(f"  [{t['label']}] y_burst {t['y_burst_actual_s']*1000:.1f}ms"
          f" ({t['n_y_writes']} writes), gap {t['gap_frames']}f,"
          f" shine_burst {t['shine_burst_actual_s']*1000:.1f}ms"
          f" ({t['n_shine_writes']} writes)")
    has_kneebend = 0x0018 in states_seen
    has_jumpf = 0x0019 in states_seen
    has_shine = any(s in (0x016D, 0x016E, 0x0168, 0x0169) for s in states_seen)
    if has_shine and has_kneebend and not has_jumpf:
        print("  *** CANONICAL JC-SHINE: KneeBend -> shine, NO JumpF ***")
    elif has_shine and has_kneebend and has_jumpf:
        print("  Jumpsquat -> JumpF -> shine (not canonical JC)")
    elif has_shine:
        print(f"  Reached shine but unusual path: {states_str}")
    elif has_kneebend:
        print("  Jumped but no shine")
    else:
        print("  No reaction or unexpected state")
    return states_seen


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        results = []
        trials_to_run = [
            # (label, y_burst_s, gap_frames, shine_burst_s)
            ("y010_gap2_shine010", 0.010, 2, 0.010),
            ("y020_gap2_shine020", 0.020, 2, 0.020),
            ("y050_gap2_shine050", 0.050, 2, 0.050),
            ("y020_gap1_shine020", 0.020, 1, 0.020),
            ("y020_gap0_shine020", 0.020, 0, 0.020),
            ("y020_gap3_shine020", 0.020, 3, 0.020),
            ("y100_gap0_shine100", 0.100, 0, 0.100),
            ("y020_gap2_shine100", 0.020, 2, 0.100),
        ]
        for tup in trials_to_run:
            print(f"\n=== trial: {tup[0]} ===", flush=True)
            t = trial(h, *tup)
            results.append(t)
            analyze(t)

        out = os.path.join(runs_dir(), "exp05_dme_jc_shine.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
