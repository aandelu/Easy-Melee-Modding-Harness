"""exp08: stress-test the reactive JC-shine.

Run 20 trials with the chosen parameters (Y50ms, gap2f, B+down50ms).
Report: success rate, frame-by-frame timing distribution, any failure
modes.

Also: try starting the Fox Y burst BEFORE forcing Marth to Catch
(pre-warm the input pipeline). Maybe this recovers the 1-frame gap
against the gecko version's reaction speed.
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
    OFF_ANALOG_Y, OFF_INSTANT_BUTTONS,
)
from melee_harness import OFF_BUTTONS, OFF_ACTION_STATE, CONTROLLER_DIGITAL, CONTROLLER_STRIDE  # noqa: E402
from scenario import CATCH  # noqa: E402


Y_BURST_S = 0.060
GAP_FRAMES = 2
SHINE_BURST_S = 0.060


def trial_full(h, idle_frames=5):
    h.reset()
    h.wait_frames(3)
    pd_fox = h.player_data_ptr(2)
    glob_fox = CONTROLLER_DIGITAL + (2 - 1) * CONTROLLER_STRIDE
    pd_marth = h.player_data_ptr(1)

    samples = []
    sampler_stop = threading.Event()
    catch_event = threading.Event()
    poll_count = [0]
    last_frame_seen = [h.frame()]
    detect_wall_t = [None]

    def sampler():
        while not sampler_stop.is_set():
            f = h.frame()
            if f != last_frame_seen[0]:
                last_frame_seen[0] = f
                try:
                    samples.append(snapshot_frame(h))
                except Exception:
                    pass
            time.sleep(0.0003)

    def poller():
        while not catch_event.is_set():
            try:
                s = h.read_word(pd_marth + OFF_ACTION_STATE) & 0xFFFF
            except Exception:
                continue
            poll_count[0] += 1
            if s == 0x00D4:
                detect_wall_t[0] = time.time()
                catch_event.set()
                return

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()
    poller_thread = threading.Thread(target=poller, daemon=True)
    poller_thread.start()

    start_frame = h.frame()
    while (h.frame() - start_frame) < idle_frames:
        time.sleep(0.0005)

    trigger_wall_t = time.time()
    h.write_words(pd_marth + OFF_ACTION_STATE, [CATCH])
    trigger_frame = h.frame()

    if not catch_event.wait(timeout=2.0):
        sampler_stop.set()
        return {"timeout": True}
    react_frame = h.frame()

    sy_bytes = struct.pack(">f", -1.0)
    mask_y_bytes = struct.pack(">I", BIT_Y)
    mask_b_bytes = struct.pack(">I", BIT_B)

    t_end = time.time() + Y_BURST_S
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_Y])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_Y])
        h.write_bytes(glob_fox, mask_y_bytes)

    for _ in range(GAP_FRAMES):
        h.wait_frames(1)

    t_end = time.time() + SHINE_BURST_S
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_B])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_B])
        h.write_bytes(glob_fox, mask_b_bytes)
        h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_bytes)
        h.write_bytes(glob_fox + 0x24, sy_bytes)

    final_frame_target = h.frame() + 10
    while h.frame() < final_frame_target:
        time.sleep(0.001)

    sampler_stop.set()
    sampler_thread.join(timeout=1.0)
    poller_thread.join(timeout=1.0)

    states_seen = []
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if not states_seen or states_seen[-1] != s:
            states_seen.append(s)
    has_kneebend = 0x0018 in states_seen
    has_jumpf = 0x0019 in states_seen
    has_aerial_shine = 0x016D in states_seen
    has_ground_shine = 0x0168 in states_seen
    canonical_jc = has_kneebend and has_aerial_shine and not has_jumpf

    # Find frames where Fox first hit each key state
    fox_kneebend_frame = None
    fox_aerialshine_frame = None
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if fox_kneebend_frame is None and s == 0x0018:
            fox_kneebend_frame = r["frame"]
        if fox_aerialshine_frame is None and s == 0x016D:
            fox_aerialshine_frame = r["frame"]

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "frame_latency": react_frame - trigger_frame,
        "detect_latency_ms": (detect_wall_t[0] - trigger_wall_t) * 1000,
        "fox_kneebend_delta": (
            fox_kneebend_frame - trigger_frame
            if fox_kneebend_frame else None),
        "fox_aerial_shine_delta": (
            fox_aerialshine_frame - trigger_frame
            if fox_aerialshine_frame else None),
        "states_seen": [f"0x{s:04X}" for s in states_seen],
        "canonical_jc_shine": canonical_jc,
        "has_ground_shine": has_ground_shine,
        "has_jumpf": has_jumpf,
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
        for i in range(20):
            t = trial_full(h)
            results.append(t)
            if t.get("timeout"):
                print(f"trial {i+1:>2}: TIMEOUT")
                continue
            mark = "[JC]" if t["canonical_jc_shine"] else (
                "[gnd]" if t["has_ground_shine"] else (
                "[J^]" if t["has_jumpf"] else "[??]"))
            print(f"trial {i+1:>2}: {mark} kneebend@T+{t['fox_kneebend_delta']} "
                  f"shine@T+{t['fox_aerial_shine_delta']} "
                  f"detect={t['detect_latency_ms']:.2f}ms | "
                  f"{' -> '.join(t['states_seen'])}")

        print("\n=== SUMMARY ===")
        n_jc = sum(1 for r in results if r.get("canonical_jc_shine"))
        n_ground = sum(1 for r in results if r.get("has_ground_shine"))
        n_jumpf = sum(1 for r in results if r.get("has_jumpf"))
        timeouts = sum(1 for r in results if r.get("timeout"))
        print(f"Canonical JC-shine: {n_jc}/{len(results)} trials "
              f"({100*n_jc/len(results):.0f}%)")
        print(f"Ground shine instead: {n_ground}/{len(results)}")
        print(f"With JumpF visible: {n_jumpf}/{len(results)}")
        print(f"Timeouts: {timeouts}/{len(results)}")

        latencies = [r["detect_latency_ms"] for r in results
                     if not r.get("timeout")]
        if latencies:
            latencies.sort()
            print(f"Detection latency (ms): min={min(latencies):.2f} "
                  f"max={max(latencies):.2f} "
                  f"mean={sum(latencies)/len(latencies):.2f} "
                  f"p95={latencies[int(0.95*len(latencies))]:.2f}")

        kneebend_deltas = [r["fox_kneebend_delta"] for r in results
                          if r.get("fox_kneebend_delta") is not None]
        if kneebend_deltas:
            from collections import Counter
            kc = Counter(kneebend_deltas)
            print(f"Fox KneeBend frame relative to trigger: {dict(kc)}")
        shine_deltas = [r["fox_aerial_shine_delta"] for r in results
                       if r.get("fox_aerial_shine_delta") is not None]
        if shine_deltas:
            from collections import Counter
            sc = Counter(shine_deltas)
            print(f"Fox aerial-shine frame relative to trigger: {dict(sc)}")

        out = os.path.join(runs_dir(), "exp08_reliability_stress.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
