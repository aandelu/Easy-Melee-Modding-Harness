"""exp09: adaptive JC-shine via state-keyed timing.

Instead of a fixed gap, we poll Fox's action state during the Y burst.
The moment KneeBend is observed, we end the Y burst and start the
B+down burst. The shine burst lands during KneeBend, gets buffered,
and the engine transitions KneeBend -> aerial shine (no JumpF).

This is the dme equivalent of the gecko-version's "counter==3" insight:
fire the shine input while Fox is still in KneeBend, and the engine
takes care of the JC.
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


def trial_adaptive(h, max_y_burst_s=0.10, shine_burst_s=0.040,
                   idle_frames=5):
    h.reset()
    h.wait_frames(3)
    pd_fox = h.player_data_ptr(2)
    glob_fox = CONTROLLER_DIGITAL + (2 - 1) * CONTROLLER_STRIDE
    pd_marth = h.player_data_ptr(1)
    fox_as_addr = pd_fox + OFF_ACTION_STATE

    samples = []
    sampler_stop = threading.Event()
    catch_event = threading.Event()
    poll_count = [0]
    last_frame_seen = [h.frame()]
    detect_wall_t = [None]
    kneebend_wall_t = [None]
    kneebend_frame = [None]

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

    def marth_poller():
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
    marth_thread = threading.Thread(target=marth_poller, daemon=True)
    marth_thread.start()

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

    mask_y_bytes = struct.pack(">I", BIT_Y)
    mask_b_bytes = struct.pack(">I", BIT_B)
    sy_bytes = struct.pack(">f", -1.0)
    sy_neutral = struct.pack(">f", 0.0)

    # ADAPTIVE Y BURST: write Y inputs and check Fox state every iteration.
    # As soon as Fox enters KneeBend, stop Y and start B+down.
    t_end = time.time() + max_y_burst_s
    fox_in_kneebend = False
    y_writes = 0
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_Y])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_Y])
        h.write_bytes(glob_fox, mask_y_bytes)
        y_writes += 1
        # check Fox state
        try:
            s = h.read_word(fox_as_addr) & 0xFFFF
            if s == 0x0018:
                kneebend_wall_t[0] = time.time()
                kneebend_frame[0] = h.frame()
                fox_in_kneebend = True
                break
        except Exception:
            pass

    # B+down burst -- starts immediately on KneeBend detection
    shine_writes = 0
    t_end = time.time() + shine_burst_s
    while time.time() < t_end:
        h.write_words(pd_fox + OFF_BUTTONS, [BIT_B])
        h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_B])
        h.write_bytes(glob_fox, mask_b_bytes)
        h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_bytes)
        h.write_bytes(glob_fox + 0x24, sy_bytes)
        shine_writes += 1

    # Clear inputs
    h.write_words(pd_fox + OFF_BUTTONS, [0])
    h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [0])
    h.write_bytes(glob_fox, b"\x00\x00\x00\x00")
    h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_neutral)
    h.write_bytes(glob_fox + 0x24, sy_neutral)

    # Observe more frames
    final_frame_target = h.frame() + 12
    while h.frame() < final_frame_target:
        time.sleep(0.001)

    sampler_stop.set()
    sampler_thread.join(timeout=1.0)
    marth_thread.join(timeout=1.0)

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

    fox_kneebend_frame_sampled = None
    fox_aerial_shine_frame_sampled = None
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if fox_kneebend_frame_sampled is None and s == 0x0018:
            fox_kneebend_frame_sampled = r["frame"]
        if fox_aerial_shine_frame_sampled is None and s == 0x016D:
            fox_aerial_shine_frame_sampled = r["frame"]

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "detect_latency_ms": (detect_wall_t[0] - trigger_wall_t) * 1000,
        "kneebend_detect_latency_ms": (
            (kneebend_wall_t[0] - detect_wall_t[0]) * 1000
            if kneebend_wall_t[0] else None),
        "kneebend_frame_polled": kneebend_frame[0],
        "y_writes": y_writes,
        "shine_writes": shine_writes,
        "kneebend_delta": (
            kneebend_frame[0] - trigger_frame
            if kneebend_frame[0] else None),
        "fox_kneebend_delta_sampled": (
            fox_kneebend_frame_sampled - trigger_frame
            if fox_kneebend_frame_sampled else None),
        "fox_aerial_shine_delta": (
            fox_aerial_shine_frame_sampled - trigger_frame
            if fox_aerial_shine_frame_sampled else None),
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
            t = trial_adaptive(h)
            results.append(t)
            if t.get("timeout"):
                print(f"trial {i+1:>2}: TIMEOUT")
                continue
            mark = "[JC]" if t["canonical_jc_shine"] else (
                "[gnd]" if t["has_ground_shine"] else (
                "[J^]" if t["has_jumpf"] else "[??]"))
            print(f"trial {i+1:>2}: {mark} "
                  f"kn_detect@T+{t['kneebend_delta']} "
                  f"shine@T+{t['fox_aerial_shine_delta']} "
                  f"kn_lat={t.get('kneebend_detect_latency_ms')}ms "
                  f"y_w={t['y_writes']} sh_w={t['shine_writes']} | "
                  f"{' -> '.join(t['states_seen'])}")

        print("\n=== SUMMARY ===")
        n_jc = sum(1 for r in results if r.get("canonical_jc_shine"))
        n_ground = sum(1 for r in results if r.get("has_ground_shine"))
        n_jumpf = sum(1 for r in results if r.get("has_jumpf"))
        print(f"Canonical JC-shine: {n_jc}/{len(results)} "
              f"({100*n_jc/len(results):.0f}%)")
        print(f"Ground shine: {n_ground}/{len(results)}")
        print(f"JumpF visible: {n_jumpf}/{len(results)}")

        from collections import Counter
        kd = [r["kneebend_delta"] for r in results
              if r.get("kneebend_delta") is not None]
        if kd:
            print(f"KneeBend entry frame (polled, vs trigger): {dict(Counter(kd))}")
        sd = [r["fox_aerial_shine_delta"] for r in results
              if r.get("fox_aerial_shine_delta") is not None]
        if sd:
            print(f"Aerial-shine entry frame (sampled, vs trigger): {dict(Counter(sd))}")

        out = os.path.join(runs_dir(), "exp09_adaptive_jc_shine.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
