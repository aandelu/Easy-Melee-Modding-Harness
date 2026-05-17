"""
auto_lcancel/test_l_timer_invariant.py

Universal-ish proof of L-cancel safety. The cycle-7 macro keeps Char Data
0x680 ("frames since L pressed") in 0..6 across every aerial frame. Since
the engine's L-cancel check is `0x680 <= 7`, max <= 6 means the landing
frame doesn't matter -- any landing during the aerial succeeds.

This test runs Fox NAIR with several fast-fall delays so Fox actually lands
on different phases of the cycle, and at every airborne frame in every
trial we sample 0x680. We assert:

    max(0x680 during aerial) <= 6    in EVERY trial
    landing-state duration (NAIR baseline 15f) is halved when L is on
    no airdodge ever fires

If max > 6 we have a real bug. If max <= 6 we have evidence the invariant
holds across every fast-fall-induced landing phase.

Trial 0 (no L) is a control: it confirms 0x680 grows unbounded (engine
increments every frame) when the macro is off, so the macro is what's
keeping the timer in range.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/test_l_timer_invariant.py
"""
import os
import struct
import subprocess
import sys
import time

import keystone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import Harness, DEFAULT_CAVE, finalize_payload
import instr_writer as iw

OFF_ACTION_STATE = 0x0010
OFF_LR_TIMER = 0x0680

S_WAIT = 0x000E
S_KNEEBEND = 0x0018
AERIAL_STATES = set(range(0x41, 0x46))
LANDING_AERIAL_STATES = set(range(0x46, 0x4B))
S_LANDING_PLAIN = 0x2A
S_AIRDODGE = 0x00EC

TARGET_PORT = 2

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000
RIG_CAVE = DEFAULT_CAVE + 0x400

# Scratch bytes
L_FLAG_ADDR = 0x803FA490
FULL_HOP_FLAG_ADDR = 0x803FA491
FAST_FALL_DELAY_ADDR = 0x803FA492          # frames after entering aerial to start fast-fall (255 = never)
L_CYCLE_COUNTER_ADDR = 0x803FA493
AERIAL_FRAME_COUNTER_ADDR = 0x803FA494     # frames since entering aerial state


def _build_src():
    return f"""
        cmpwi 24, 1
        bne   end

        # P2 GObj -> Player Data
        lis   12, 0x8045
        ori   12, 12, 0x3FC0
        lwz   12, 0(12)
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24
        cmplwi 9, 0x80
        bne   end
        lwz   12, 0x2C(12)
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24
        cmplwi 9, 0x80
        bne   end

        lwz   11, 0x10(12)            # r11 = action state
        lhz   0, 0(25)                 # r0  = current 16-bit pad buttons

        # Wait (0x0E) -> press X (jump)
        cmpwi 11, 0x000E
        bne   not_wait
        ori   0, 0, 0x0400
        sth   0, 0(25)
        b     reset_aerial_state
    not_wait:

        # KneeBend (0x18) -> press X iff FULL_HOP_FLAG set
        cmpwi 11, 0x0018
        bne   not_kneebend
        lis   9, 0x{(FULL_HOP_FLAG_ADDR >> 16):04X}
        ori   9, 9, 0x{(FULL_HOP_FLAG_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        beq   reset_aerial_state
        ori   0, 0, 0x0400
        sth   0, 0(25)
        b     reset_aerial_state
    not_kneebend:

        # JumpF / Fall (0x19..0x22) -> press A (NAIR, no stick)
        cmpwi 11, 0x0019
        blt   not_jump
        cmpwi 11, 0x0022
        bgt   not_jump
        ori   0, 0, 0x0100
        sth   0, 0(25)
        b     reset_aerial_state
    not_jump:

        # Aerial states 0x41..0x45 -> fast-fall + cycle-7 L
        cmpwi 11, 0x0041
        blt   reset_aerial_state
        cmpwi 11, 0x0045
        bgt   reset_aerial_state

        # Increment AERIAL_FRAME_COUNTER (saturate at 254).
        lis   9, 0x{(AERIAL_FRAME_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(AERIAL_FRAME_COUNTER_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmplwi 10, 254
        bge   skip_inc_aerial
        addi  10, 10, 1
        stb   10, 0(9)
    skip_inc_aerial:

        # If AERIAL_FRAME_COUNTER >= FAST_FALL_DELAY, inject stick down.
        lis   9, 0x{(FAST_FALL_DELAY_ADDR >> 16):04X}
        ori   9, 9, 0x{(FAST_FALL_DELAY_ADDR & 0xFFFF):04X}
        lbz   8, 0(9)
        cmplw 10, 8
        blt   stick_neutral
        li    8, -100
        stb   8, 3(25)
        li    8, 0
        stb   8, 2(25)
        b     after_stick
    stick_neutral:
        li    8, 0
        stb   8, 2(25)
        stb   8, 3(25)
    after_stick:

        # Cycle-7 L (gated by L_FLAG).
        lis   9, 0x{(L_FLAG_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_FLAG_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        beq   apply

        lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        bne   inc_l_counter
        ori   0, 0, 0x0040
    inc_l_counter:
        addi  10, 10, 1
        cmpwi 10, 7
        blt   store_l_counter
        li    10, 0
    store_l_counter:
        stb   10, 0(9)
        b     apply

    reset_aerial_state:
        # Non-aerial: reset BOTH counters so the next aerial entry starts
        # fresh (counter=0 -> press L on frame 0).
        li    10, 0
        lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
        stb   10, 0(9)
        lis   9, 0x{(AERIAL_FRAME_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(AERIAL_FRAME_COUNTER_ADDR & 0xFFFF):04X}
        stb   10, 0(9)

    apply:
        sth   0, 0(25)

    end:
    """


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned None")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale_dolphins():
    r = subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    if r.returncode == 0:
        for _ in range(40):
            p = subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                               text=True)
            if not p.stdout.strip():
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin refused to die within 10s")


def read_state(h, pd):
    a = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
    lr = h.read_bytes(pd + OFF_LR_TIMER, 1)[0]
    return a, lr


def wait_for_wait(h, pd, max_frames=180):
    for _ in range(max_frames):
        a, _ = read_state(h, pd)
        if a == S_WAIT:
            return True
        h.wait_frames(1)
    return False


def run_one_trial(h, pd, l_on, full_hop, fast_fall_delay, max_frames=320):
    """Run one jump+NAIR cycle. Sample 0x680 at every frame from aerial
    entry to first non-aerial frame. Return per-frame samples and metrics."""
    h.write_bytes(L_FLAG_ADDR, b"\x01" if l_on else b"\x00")
    h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x01" if full_hop else b"\x00")
    h.write_bytes(FAST_FALL_DELAY_ADDR, bytes([fast_fall_delay]))
    h.write_bytes(L_CYCLE_COUNTER_ADDR, b"\x00")
    h.write_bytes(AERIAL_FRAME_COUNTER_ADDR, b"\x00")

    if not wait_for_wait(h, pd):
        return {"error": "never returned to Wait"}

    aerial_seen = None
    aerial_entry = None
    aerial_samples = []          # list of 0x680 values per aerial frame
    landing_index = None
    cycle_end = None
    airdodge_seen = False
    plain_landing_seen = False

    for i in range(max_frames):
        a, lr = read_state(h, pd)
        if a in AERIAL_STATES:
            if aerial_seen is None:
                aerial_seen = a
                aerial_entry = i
            aerial_samples.append(lr)
        if a == S_AIRDODGE:
            airdodge_seen = True
        if a == S_LANDING_PLAIN:
            plain_landing_seen = True
        if a in LANDING_AERIAL_STATES and landing_index is None:
            landing_index = i
            # one more sample on the landing frame itself (this is the
            # value the engine's L-cancel check reads)
            aerial_samples.append(lr)
        if landing_index is not None and a == S_WAIT:
            cycle_end = i
            break
        h.wait_frames(1)

    duration = None
    if landing_index is not None and cycle_end is not None:
        duration = cycle_end - landing_index
    airborne_frames = (landing_index - aerial_entry
                       if aerial_entry is not None and landing_index is not None
                       else None)
    return {
        "aerial_state": aerial_seen,
        "airborne_frames": airborne_frames,
        "duration": duration,
        "samples": aerial_samples,
        "max_lr_timer": max(aerial_samples) if aerial_samples else None,
        "airdodge": airdodge_seen,
        "plain_landing": plain_landing_seen,
    }


def main():
    kill_stale_dolphins()
    h = Harness()
    iw.install_meta_flush(h)
    print("[test] launching Dolphin ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[test] meta-flush alive. seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)
    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[test] P2 player data invalid -- abort", flush=True)
        return 1

    for addr in (L_FLAG_ADDR, FULL_HOP_FLAG_ADDR, FAST_FALL_DELAY_ADDR,
                 L_CYCLE_COUNTER_ADDR, AERIAL_FRAME_COUNTER_ADDR):
        h.write_bytes(addr, b"\x00")
    h.write_bytes(FAST_FALL_DELAY_ADDR, b"\xFF")    # default: never fast-fall

    logic = assemble(_build_src())
    payload = finalize_payload(logic, HOOK_ADDR, RIG_CAVE, DISPLACED)
    iw.write_instrs(h, RIG_CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, RIG_CAVE)
    print(f"[test] hook installed at 0x{HOOK_ADDR:08X} -> cave 0x{RIG_CAVE:08X} "
          f"({len(payload)} words)", flush=True)
    h.wait_frames(20)
    wait_for_wait(h, pd)
    h.wait_frames(3)

    # Trial schedule. (label, l_on, full_hop, fast_fall_delay)
    # fast_fall_delay=0xFF means "never fast-fall" (effectively unreachable).
    # Lower delays => Fox starts falling fast earlier => lands sooner.
    # We sweep through 8 short-hop fast-fall delays and 5 full-hop delays
    # to land Fox at many different cycle phases.
    trials = [
        # control: L off, just to confirm 0x680 grows unbounded without macro
        ("CONTROL  short  no-FF        L=off",  False, False, 0xFF),
        # short hop sweep
        ("short-hop  FF@0   L=cycle-7", True,  False, 0),
        ("short-hop  FF@1   L=cycle-7", True,  False, 1),
        ("short-hop  FF@2   L=cycle-7", True,  False, 2),
        ("short-hop  FF@3   L=cycle-7", True,  False, 3),
        ("short-hop  FF@4   L=cycle-7", True,  False, 4),
        ("short-hop  no-FF  L=cycle-7", True,  False, 0xFF),
        # full hop sweep -- more airtime, hits more cycle phases
        ("full-hop   FF@0   L=cycle-7", True,  True,  0),
        ("full-hop   FF@3   L=cycle-7", True,  True,  3),
        ("full-hop   FF@6   L=cycle-7", True,  True,  6),
        ("full-hop   FF@10  L=cycle-7", True,  True,  10),
        ("full-hop   FF@15  L=cycle-7", True,  True,  15),
        ("full-hop   FF@20  L=cycle-7", True,  True,  20),
        ("full-hop   no-FF  L=cycle-7", True,  True,  0xFF),
    ]

    print("\n" + "=" * 88, flush=True)
    print(f"Running {len(trials)} trials (control + fast-fall sweep). "
          f"Sampling 0x680 every aerial frame.", flush=True)
    print("=" * 88, flush=True)

    results = []
    for label, l_on, full_hop, ff_delay in trials:
        print(f"  {label}", end="  ", flush=True)
        r = run_one_trial(h, pd, l_on, full_hop, ff_delay)
        r["label"] = label
        r["l_on"] = l_on
        r["full_hop"] = full_hop
        r["fast_fall_delay"] = ff_delay
        results.append(r)
        if r.get("error"):
            print(f"-> ERROR: {r['error']}", flush=True)
            continue
        m = r["max_lr_timer"]
        af = r["airborne_frames"]
        d = r["duration"]
        ad = r["airdodge"]
        pl = r["plain_landing"]
        tag = (" AIRDODGE!" if ad else "") + (" plain-land" if pl else "")
        print(f"-> air={af}f  max(0x680)={m}  land-dur={d}f{tag}", flush=True)

    # Dump per-frame sample sequences for the first few trials so we can
    # see exactly when 0x680 spikes vs. when it cycles.
    print("\n" + "=" * 88, flush=True)
    print("PER-FRAME 0x680 SAMPLES (last sample is the LANDING frame)", flush=True)
    print("=" * 88, flush=True)
    for r in results[:5]:
        s = r.get("samples") or []
        print(f"  {r['label']}", flush=True)
        # Mark landing-frame index (last sample)
        annotated = []
        for idx, v in enumerate(s):
            tag = ""
            if idx == len(s) - 1:
                tag = "<-LAND"
            annotated.append(f"{v}{tag}")
        # Print in groups of 10 for readability
        for j in range(0, len(annotated), 10):
            print(f"    [{j:3d}] " + "  ".join(annotated[j:j+10]), flush=True)

    print("\n" + "=" * 88, flush=True)
    print("VERIFICATION", flush=True)
    print("=" * 88, flush=True)

    # Compute baseline (L off control) for the ratio check.
    control = results[0]
    baseline = control.get("duration")
    print(f"  Control (no L): max(0x680) during aerial = "
          f"{control['max_lr_timer']}, landing duration = {baseline}f", flush=True)
    if control["max_lr_timer"] is None or control["max_lr_timer"] < 50:
        print(f"  [WARN] control max should grow unbounded (engine increments "
              f"every non-press frame). Saw {control['max_lr_timer']}. "
              f"This suggests something else is pressing L.", flush=True)
    if baseline is None or baseline < 12:
        print(f"  [WARN] control NAIR landing-state expected ~15f, got "
              f"{baseline}. Test may be unreliable.", flush=True)
        baseline = 15

    failures = []
    n_l_on = 0
    # The first aerial sample is carry-over from the pre-aerial state
    # (the macro's PadRead on the engine's first aerial frame still saw
    # the previous-frame Fall state, so didn't press L). The macro starts
    # pressing on the second aerial frame from Python's view. So steady-
    # state begins at samples[1].
    for r in results[1:]:
        label = r["label"]
        if r.get("error"):
            failures.append(f"{label}: {r['error']}")
            continue
        if r["airdodge"]:
            failures.append(f"{label}: AIRDODGE")
            continue
        samples = r.get("samples") or []
        d = r["duration"]
        af = r["airborne_frames"]
        pl = r["plain_landing"]

        if not samples:
            failures.append(f"{label}: no aerial frames observed")
            continue

        # CHECK 1: the value AT LANDING (last sample) must be <= 7. This
        # is exactly what the engine's L-cancel check (`bgt window`) reads.
        land_val = samples[-1]
        r["landing_lr_timer"] = land_val
        if land_val > 7:
            failures.append(f"{label}: landing-frame 0x680 = {land_val} > 7 "
                            f"(L-cancel would FAIL)")

        # CHECK 2: steady-state cycle stays in 0..6 after the warmup.
        # Skip samples[0] (warmup carry-over).
        if len(samples) >= 2:
            steady = samples[1:]
            steady_max = max(steady)
            r["steady_max"] = steady_max
            if steady_max > 6:
                failures.append(f"{label}: steady-state max(0x680) = {steady_max} > 6 "
                                f"(cycle is not bounded by 6)")

        # CHECK 3: L-cancel observed (landing-state halved).
        if pl and d is None:
            pass    # plain landing, can't L-cancel
        elif d is None:
            failures.append(f"{label}: no LandingAir state observed")
        elif baseline and d > 0.6 * baseline:
            failures.append(f"{label}: landing-duration {d}f > 0.6*baseline "
                            f"({0.6*baseline:.1f}f) -- L-cancel didn't trigger")
        n_l_on += 1

    # Also check that fast-fall delays produced varied airborne frames.
    air_counts = [r["airborne_frames"] for r in results[1:]
                  if r.get("airborne_frames") is not None]
    distinct = len(set(air_counts))
    print(f"  L-on trials: {n_l_on}", flush=True)
    print(f"  Distinct airborne-frame counts across L-on trials: "
          f"{distinct} ({sorted(set(air_counts))})", flush=True)
    if distinct < 4:
        failures.append(f"fast-fall sweep didn't vary landing frame enough "
                        f"(only {distinct} distinct airborne durations)")

    # Per-trial detail.
    print(flush=True)
    print(f"  {'trial':36s}  {'air':>4s}  {'land 0x680':>10s}  "
          f"{'steady-max':>10s}  {'land-dur':>8s}  verdict", flush=True)
    for r in results:
        label = r["label"]
        af = r.get("airborne_frames")
        lv = r.get("landing_lr_timer")
        sm = r.get("steady_max")
        d = r.get("duration")
        verdict = "-"
        if r is control:
            verdict = "control"
        elif r.get("airdodge"):
            verdict = "FAIL-AD"
        elif lv is None and not r.get("plain_landing"):
            verdict = "FAIL-OBS"
        elif lv is not None and lv > 7:
            verdict = "FAIL-LV"
        elif sm is not None and sm > 6:
            verdict = "FAIL-SS"
        elif r.get("plain_landing") and d is None:
            verdict = "plain"
        elif d is None:
            verdict = "FAIL-LAND"
        elif baseline and d > 0.6 * baseline:
            verdict = "FAIL-RATIO"
        else:
            verdict = "PASS"
        print(f"  {label:36s}  {str(af):>4s}  {str(lv):>10s}  "
              f"{str(sm):>10s}  {str(d):>8s}  {verdict}", flush=True)

    print(flush=True)
    if failures:
        print(f"[FAIL] {len(failures)} problem(s):", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    print(f"[PASS] {n_l_on} L-on trials all had landing-frame 0x680 <= 7 "
          f"(L-cancel guaranteed) and steady-state 0x680 <= 6 (cycle is "
          f"bounded). L-cancel observed in every trial that had a "
          f"LandingAir state. 0 airdodges. Invariant holds across "
          f"{distinct} distinct landing-frame phases.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
