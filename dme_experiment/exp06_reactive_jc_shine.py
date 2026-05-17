"""exp06: poll Marth's action state, react with Fox JC-shine when Catch.

This is the dme-only equivalent of the full Frame-1 macro:
  1. Reset to seeded scenario (P1 Marth Wait, P2 Fox Wait)
  2. From a tight Python loop, poll Marth's action state
     and force Marth into Catch at a chosen frame T
     (simulating the user grab input)
  3. As soon as Catch (0xD4) is observed, trigger Fox's JC-shine via
     the same dme-burst sequence as exp05
  4. Measure: reaction latency in frames (Fox enters KneeBend vs frame
     Marth entered Catch)

We compare reaction latency under two designs:
  A) THREADED: a background thread polls Marth and signals when to burst
  B) INLINE: single-thread tight loop alternating poll+conditional-burst

For each design we run 5 trials and report mean/min/max latency.
"""
import json
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (  # noqa: E402
    BIT_Y, BIT_B, Harness, snapshot_frame, runs_dir,
    OFF_ANALOG_X, OFF_ANALOG_Y, OFF_INSTANT_BUTTONS,
)
from melee_harness import OFF_BUTTONS, OFF_ACTION_STATE, CONTROLLER_DIGITAL, CONTROLLER_STRIDE  # noqa: E402
from scenario import CATCH  # noqa: E402


Y_BURST_S = 0.050
GAP_FRAMES = 2
SHINE_BURST_S = 0.050


def _cache_fox(h):
    pd = h.player_data_ptr(2)
    glob = CONTROLLER_DIGITAL + (2 - 1) * CONTROLLER_STRIDE
    return pd, glob


def burst_inputs_cached(h, pd, glob, mask, duration_s, stick_y=None):
    sy_bytes = struct.pack(">f", stick_y) if stick_y is not None else None
    mask_bytes = struct.pack(">I", mask)
    t_end = time.time() + duration_s
    writes = 0
    while time.time() < t_end:
        h.write_words(pd + OFF_BUTTONS, [mask])
        h.write_words(pd + OFF_INSTANT_BUTTONS, [mask])
        h.write_bytes(glob, mask_bytes)
        if sy_bytes is not None:
            h.write_bytes(pd + OFF_ANALOG_Y, sy_bytes)
            h.write_bytes(glob + 0x24, sy_bytes)
        writes += 1
    return writes


def force_marth_catch(h):
    """Force Marth (P1) into Catch via PD+0x10 (action state direct write).
    Confirmed from exp01 that the ID flips even though the engine doesn't
    treat it as a real grab; that's enough -- our trigger reads the ID."""
    pd1 = h.player_data_ptr(1)
    h.write_words(pd1 + OFF_ACTION_STATE, [CATCH])


def jc_shine_sequence(h, pd, glob):
    """Execute the full JC-shine input sequence."""
    burst_inputs_cached(h, pd, glob, BIT_Y, Y_BURST_S)
    for _ in range(GAP_FRAMES):
        h.wait_frames(1)
    burst_inputs_cached(h, pd, glob, BIT_B, SHINE_BURST_S, stick_y=-1.0)


def run_trial_inline(h, n_idle_frames_before_trigger=5):
    """Inline (single-thread) reactive trigger.

    Sit in a tight poll loop reading Marth's action state. After
    `n_idle_frames_before_trigger` frames we force Marth into Catch.
    We continue polling and start the JC-shine the moment Catch is
    detected.
    """
    h.reset()
    h.wait_frames(3)

    pd_fox, glob_fox = _cache_fox(h)
    pd_marth = h.player_data_ptr(1)

    # Track frames so we can fire the trigger at a known offset
    start_frame = h.frame()
    trigger_frame = None
    react_frame = None
    detect_wall_t = None

    # Poll until detection
    records = []
    poll_count = 0
    poll_t_start = time.time()
    while True:
        f = h.frame()
        if trigger_frame is None and (f - start_frame) >= n_idle_frames_before_trigger:
            force_marth_catch(h)
            trigger_frame = h.frame()
            trigger_wall_t = time.time()
        # Read Marth's action state via direct dme word read
        marth_state = h.read_word(pd_marth + OFF_ACTION_STATE) & 0xFFFF
        poll_count += 1
        if trigger_frame is not None and marth_state == 0x00D4:
            react_frame = h.frame()
            detect_wall_t = time.time()
            break
        if (time.time() - poll_t_start) > 5.0:
            return {"timeout": True}

    detect_latency_ms = (detect_wall_t - trigger_wall_t) * 1000

    # Execute JC-shine
    jc_shine_sequence(h, pd_fox, glob_fox)

    # Observe for 15 frames
    for _ in range(15):
        records.append(snapshot_frame(h))
        h.wait_frames(1)

    states_seen = []
    for r in records:
        s = r["p2_action"] & 0xFFFF
        if not states_seen or states_seen[-1] != s:
            states_seen.append(s)

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "frame_latency": react_frame - trigger_frame if react_frame else None,
        "detect_latency_ms": detect_latency_ms,
        "poll_count": poll_count,
        "states_seen": [f"0x{s:04X}" for s in states_seen],
        "records": records,
    }


def run_trial_threaded(h, n_idle_frames_before_trigger=5):
    """Threaded design: background thread polls Marth and signals."""
    h.reset()
    h.wait_frames(3)

    pd_fox, glob_fox = _cache_fox(h)
    pd_marth = h.player_data_ptr(1)

    catch_event = threading.Event()
    poll_count = [0]
    detected_at = [None]

    def poller():
        while not catch_event.is_set():
            try:
                s = h.read_word(pd_marth + OFF_ACTION_STATE) & 0xFFFF
            except Exception:
                continue
            poll_count[0] += 1
            if s == 0x00D4:
                detected_at[0] = time.time()
                catch_event.set()
                return

    t = threading.Thread(target=poller, daemon=True)
    t.start()

    # Wait the idle frames then force Marth
    start_frame = h.frame()
    while True:
        if (h.frame() - start_frame) >= n_idle_frames_before_trigger:
            break
        time.sleep(0.0005)
    trigger_wall_t = time.time()
    force_marth_catch(h)
    trigger_frame = h.frame()

    # Wait for detection (with timeout)
    if not catch_event.wait(timeout=2.0):
        return {"timeout": True}
    react_frame = h.frame()
    t.join(timeout=1.0)

    detect_latency_ms = (detected_at[0] - trigger_wall_t) * 1000

    jc_shine_sequence(h, pd_fox, glob_fox)

    records = []
    for _ in range(15):
        records.append(snapshot_frame(h))
        h.wait_frames(1)
    states_seen = []
    for r in records:
        s = r["p2_action"] & 0xFFFF
        if not states_seen or states_seen[-1] != s:
            states_seen.append(s)

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "frame_latency": react_frame - trigger_frame,
        "detect_latency_ms": detect_latency_ms,
        "poll_count": poll_count[0],
        "states_seen": [f"0x{s:04X}" for s in states_seen],
        "records": records,
    }


def summarize_trials(label, trials):
    print(f"\n=== {label} summary ===")
    for i, t in enumerate(trials):
        if t.get("timeout"):
            print(f"  trial {i+1}: TIMEOUT")
            continue
        states = " -> ".join(t["states_seen"])
        canonical = (
            "0x0018" in t["states_seen"] and "0x016D" in t["states_seen"]
            and "0x0019" not in t["states_seen"]
        )
        mark = "JC" if canonical else "??"
        print(f"  trial {i+1}: [{mark}] trigger@f{t['trigger_frame']} "
              f"detected@f{t['react_frame']} "
              f"(frame_latency={t['frame_latency']}, "
              f"detect_ms={t['detect_latency_ms']:.2f}, "
              f"polls={t['poll_count']}) | states: {states}")


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        inline = []
        for i in range(3):
            print(f"\n--- inline trial {i+1} ---", flush=True)
            inline.append(run_trial_inline(h))

        threaded = []
        for i in range(3):
            print(f"\n--- threaded trial {i+1} ---", flush=True)
            threaded.append(run_trial_threaded(h))

        summarize_trials("INLINE", inline)
        summarize_trials("THREADED", threaded)

        out = os.path.join(runs_dir(), "exp06_reactive.json")
        with open(out, "w") as f:
            json.dump({"inline": inline, "threaded": threaded}, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
