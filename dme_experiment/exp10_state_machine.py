"""exp10: state-machine input driver.

Continuous-poll Fox's action state. Based on observed state, write the
appropriate input:
  Fox = Wait (0x000E)     -> write Y press
  Fox = KneeBend (0x0018) -> write B + stickY=-1.0
  Fox = anything else     -> clear inputs / stop

The state machine handles input race: regardless of when KneeBend
actually starts, we immediately switch to B+down writes. Continuous
burst-writing keeps the input plane saturated.
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


def trial_state_machine(h, max_total_s=0.3, idle_frames=5):
    h.reset()
    h.wait_frames(3)
    pd_fox = h.player_data_ptr(2)
    glob_fox = CONTROLLER_DIGITAL + (2 - 1) * CONTROLLER_STRIDE
    pd_marth = h.player_data_ptr(1)
    fox_as_addr = pd_fox + OFF_ACTION_STATE

    samples = []
    sampler_stop = threading.Event()
    catch_event = threading.Event()
    detect_wall_t = [None]
    last_frame_seen = [h.frame()]

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

    # State machine input driver
    sy_neg = struct.pack(">f", -1.0)
    sy_neutral = struct.pack(">f", 0.0)
    mask_y_bytes = struct.pack(">I", BIT_Y)
    mask_b_bytes = struct.pack(">I", BIT_B)

    state_log = []
    t_deadline = time.time() + max_total_s
    saw_kneebend = False
    saw_shine = False
    while time.time() < t_deadline:
        try:
            s = h.read_word(fox_as_addr) & 0xFFFF
        except Exception:
            continue
        if s == 0x000E:           # Wait -> press Y
            h.write_words(pd_fox + OFF_BUTTONS, [BIT_Y])
            h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_Y])
            h.write_bytes(glob_fox, mask_y_bytes)
        elif s == 0x0018:         # KneeBend -> B + down
            if not saw_kneebend:
                saw_kneebend = True
                state_log.append(("kneebend", h.frame(), time.time()))
            h.write_words(pd_fox + OFF_BUTTONS, [BIT_B])
            h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_B])
            h.write_bytes(glob_fox, mask_b_bytes)
            h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_neg)
            h.write_bytes(glob_fox + 0x24, sy_neg)
        else:
            # Other state: stop sending inputs
            if s in (0x016D, 0x016E):
                if not saw_shine:
                    saw_shine = True
                    state_log.append(("shine", h.frame(), time.time()))
            elif s == 0x0019:
                state_log.append(("jumpf_observed", h.frame(), time.time()))
                # try one more shot of B+down (shine still triggerable in
                # the first few frames of JumpF)
                h.write_words(pd_fox + OFF_BUTTONS, [BIT_B])
                h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [BIT_B])
                h.write_bytes(glob_fox, mask_b_bytes)
                h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_neg)
                h.write_bytes(glob_fox + 0x24, sy_neg)
            if saw_shine:
                break
            # don't break -- still try to react to next state change

    # Clear inputs and let it settle
    h.write_words(pd_fox + OFF_BUTTONS, [0])
    h.write_words(pd_fox + OFF_INSTANT_BUTTONS, [0])
    h.write_bytes(glob_fox, b"\x00\x00\x00\x00")
    h.write_bytes(pd_fox + OFF_ANALOG_Y, sy_neutral)
    h.write_bytes(glob_fox + 0x24, sy_neutral)

    final_frame_target = h.frame() + 10
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

    fox_kneebend = None
    fox_shine = None
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if fox_kneebend is None and s == 0x0018:
            fox_kneebend = r["frame"]
        if fox_shine is None and s == 0x016D:
            fox_shine = r["frame"]

    return {
        "trigger_frame": trigger_frame,
        "react_frame": react_frame,
        "detect_latency_ms": (detect_wall_t[0] - trigger_wall_t) * 1000,
        "fox_kneebend_delta": (
            fox_kneebend - trigger_frame if fox_kneebend else None),
        "fox_aerial_shine_delta": (
            fox_shine - trigger_frame if fox_shine else None),
        "states_seen": [f"0x{s:04X}" for s in states_seen],
        "canonical_jc_shine": canonical_jc,
        "has_ground_shine": has_ground_shine,
        "has_jumpf": has_jumpf,
        "state_log": state_log,
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
            t = trial_state_machine(h)
            results.append(t)
            if t.get("timeout"):
                print(f"trial {i+1:>2}: TIMEOUT")
                continue
            mark = "[JC]" if t["canonical_jc_shine"] else (
                "[gnd]" if t["has_ground_shine"] else (
                "[J^]" if t["has_jumpf"] else "[??]"))
            print(f"trial {i+1:>2}: {mark} "
                  f"kn@T+{t['fox_kneebend_delta']} "
                  f"sh@T+{t['fox_aerial_shine_delta']} | "
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
        kd = [r["fox_kneebend_delta"] for r in results
              if r.get("fox_kneebend_delta") is not None]
        if kd:
            print(f"KneeBend delta: {dict(Counter(kd))}")
        sd = [r["fox_aerial_shine_delta"] for r in results
              if r.get("fox_aerial_shine_delta") is not None]
        if sd:
            print(f"Shine delta: {dict(Counter(sd))}")

        out = os.path.join(runs_dir(), "exp10_state_machine.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {out}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
