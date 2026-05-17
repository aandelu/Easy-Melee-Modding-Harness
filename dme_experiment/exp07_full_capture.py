"""exp07: full per-frame capture of the reactive JC-shine.

Final integration: polls Marth's action state from a thread, fires the
JC-shine sequence the moment Catch is observed, and captures per-frame
snapshots throughout the sequence (not just after). Should let us see
the canonical KneeBend -> aerial shine path frame-by-frame from the
reactive trigger.
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


def burst_with_sampling(h, pd, glob, mask, duration_s, samples_buf,
                       frame_seen, stick_y=None):
    """Burst writes, sampling P2 state once per emulated frame transition."""
    sy_bytes = struct.pack(">f", stick_y) if stick_y is not None else None
    mask_bytes = struct.pack(">I", mask)
    t_end = time.time() + duration_s
    writes = 0
    last_frame = frame_seen[0]
    while time.time() < t_end:
        h.write_words(pd + OFF_BUTTONS, [mask])
        h.write_words(pd + OFF_INSTANT_BUTTONS, [mask])
        h.write_bytes(glob, mask_bytes)
        if sy_bytes is not None:
            h.write_bytes(pd + OFF_ANALOG_Y, sy_bytes)
            h.write_bytes(glob + 0x24, sy_bytes)
        writes += 1
        # Sample once per frame change
        f = h.frame()
        if f != last_frame:
            last_frame = f
            samples_buf.append(snapshot_frame(h))
    frame_seen[0] = last_frame
    return writes


def trial_full(h, idle_frames_before_trigger=5):
    """Full reactive trial with continuous per-frame sampling."""
    h.reset()
    h.wait_frames(3)
    pd_fox = h.player_data_ptr(2)
    glob_fox = CONTROLLER_DIGITAL + (2 - 1) * CONTROLLER_STRIDE
    pd_marth = h.player_data_ptr(1)

    samples = []
    catch_event = threading.Event()
    poll_count = [0]
    last_frame_seen = [h.frame()]

    # Background frame sampler — runs constantly during burst
    sampler_stop = threading.Event()

    def sampler():
        while not sampler_stop.is_set():
            f = h.frame()
            if f != last_frame_seen[0]:
                last_frame_seen[0] = f
                samples.append(snapshot_frame(h))
            time.sleep(0.0005)

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()

    # Poller — only runs until detection
    def poller():
        while not catch_event.is_set():
            try:
                s = h.read_word(pd_marth + OFF_ACTION_STATE) & 0xFFFF
            except Exception:
                continue
            poll_count[0] += 1
            if s == 0x00D4:
                catch_event.set()
                return

    poller_thread = threading.Thread(target=poller, daemon=True)
    poller_thread.start()

    # Wait the idle frames
    t_start = time.time()
    start_frame = h.frame()
    while (h.frame() - start_frame) < idle_frames_before_trigger:
        time.sleep(0.0005)

    # Trigger Marth -> Catch
    trigger_wall_t = time.time()
    h.write_words(pd_marth + OFF_ACTION_STATE, [CATCH])
    trigger_frame = h.frame()

    # Wait for detection
    if not catch_event.wait(timeout=2.0):
        sampler_stop.set()
        return {"timeout": True}
    detect_wall_t = time.time()
    react_frame = h.frame()

    # Run the JC-shine sequence (sampler keeps recording)
    seq_t0 = time.time()

    sy_bytes = struct.pack(">f", -1.0)
    mask_y_bytes = struct.pack(">I", BIT_Y)
    mask_b_bytes = struct.pack(">I", BIT_B)

    # Y burst
    t_end = time.time() + Y_BURST_S
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_Y])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_Y])
        h.write_bytes(glob_fox, mask_y_bytes)

    # Gap
    for _ in range(GAP_FRAMES):
        h.wait_frames(1)

    # B+down burst
    t_end = time.time() + SHINE_BURST_S
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_B])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_B])
        h.write_bytes(glob_fox, mask_b_bytes)
        h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_bytes)
        h.write_bytes(glob_fox + 0x24, sy_bytes)

    seq_duration_ms = (time.time() - seq_t0) * 1000

    # Continue sampling for 10 more frames
    final_frame_target = h.frame() + 10
    while h.frame() < final_frame_target:
        time.sleep(0.001)

    sampler_stop.set()
    sampler_thread.join(timeout=1.0)
    poller_thread.join(timeout=1.0)

    # Compute states
    states_seen = []
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if not states_seen or states_seen[-1] != s:
            states_seen.append(s)

    has_kneebend = 0x0018 in states_seen
    has_jumpf = 0x0019 in states_seen
    has_aerial_shine = 0x016D in states_seen
    canonical_jc = has_kneebend and has_aerial_shine and not has_jumpf

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "frame_latency": react_frame - trigger_frame,
        "detect_latency_ms": (detect_wall_t - trigger_wall_t) * 1000,
        "seq_duration_ms": seq_duration_ms,
        "poll_count": poll_count[0],
        "sample_count": len(samples),
        "states_seen": [f"0x{s:04X}" for s in states_seen],
        "canonical_jc_shine": canonical_jc,
        "samples": samples,
    }


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        results = []
        for i in range(5):
            print(f"\n--- trial {i+1} ---", flush=True)
            t = trial_full(h)
            results.append(t)
            if t.get("timeout"):
                print("  TIMEOUT")
                continue
            mark = "[JC]" if t["canonical_jc_shine"] else "[??]"
            print(f"  {mark} trigger@f{t['trigger_frame']} "
                  f"detect@f{t['react_frame']} "
                  f"latency={t['frame_latency']}f / "
                  f"{t['detect_latency_ms']:.2f}ms | "
                  f"states: {' -> '.join(t['states_seen'])}")

        print("\n=== SUMMARY ===")
        n_jc = sum(1 for r in results if r.get("canonical_jc_shine"))
        print(f"Canonical JC-shine: {n_jc}/{len(results)} trials")
        latencies = [r["detect_latency_ms"] for r in results if not r.get("timeout")]
        if latencies:
            print(f"Detection latency (ms): min={min(latencies):.2f}, "
                  f"max={max(latencies):.2f}, "
                  f"mean={sum(latencies)/len(latencies):.2f}")

        out = os.path.join(runs_dir(), "exp07_full_capture.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
