"""
auto_lcancel/discover_lcancel.py

Fully automated probe of the L-cancel mechanic. No controller required.

Runs five tests sequentially against a fresh slot-2 load:

  A. Idle baseline                  -- confirms savestate + addresses are right.
  B. Direct 0x680 manipulation      -- can we write the L/R timer, and how
                                       does the engine respond?
  C. Controller L bit propagation   -- does writing L=0x40 into the controller
                                       region race the input pipeline well
                                       enough to reset 0x680?
  D. Full nair WITHOUT any L press  -- expected lag_div = 1.0 at landing.
  E. Full nair WITH L on controller -- expected lag_div = 2.0 at landing if
                                       the controller-write path works.

The actuation of jumps and aerials is via action-state forcing (not controller
writes) so the test of the L-cancel mechanic is isolated from the question of
whether we can drive a jump via controller. The macro itself will be all
controller writes; test E is the one that proves that path works.

Run:
    python3 auto_lcancel/discover_lcancel.py
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import Harness, CONTROLLER_DIGITAL, CONTROLLER_STRIDE

# Player-Data offsets.
OFF_ACTION_STATE = 0x0010
OFF_DIGITAL_BUTTONS = 0x065C
OFF_ANALOG_TRIGGER_TIMER = 0x0678
OFF_Z_TIMER = 0x067F
OFF_LR_TIMER = 0x0680
OFF_LANDING_LAG_DIVISOR = 0x2354
OFF_ACT_OUT_OF_LANDING = 0x2358

# Action states.
S_WAIT = 0x000E
S_KNEEBEND = 0x0018
S_JUMPF = 0x0019
S_FALL = 0x001D
S_FALL_AERIAL = 0x0020
S_AIR_N = 0x0041
S_LANDING = 0x002A
S_LANDING_AIR_N = 0x0046
AERIAL_STATES = set(range(0x41, 0x46))
LANDING_AERIAL_STATES = set(range(0x46, 0x4B))

# Controller button mask.
L_BIT = 0x00000040

TARGET_PORT = 2


def read_lr_timer(h, pd):
    return h.read_bytes(pd + OFF_LR_TIMER, 1)[0]


def read_lag_divisor(h, pd):
    return struct.unpack(">f", h.read_bytes(pd + OFF_LANDING_LAG_DIVISOR, 4))[0]


def read_act_out(h, pd):
    return h.read_bytes(pd + OFF_ACT_OUT_OF_LANDING, 1)[0]


def read_astate(h, pd):
    return h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF


def force_astate(h, pd, state):
    h.write_words(pd + OFF_ACTION_STATE, [state])


def write_lr_timer(h, pd, value):
    cur4 = h.read_bytes(pd + (OFF_LR_TIMER & ~3), 4)
    b = bytearray(cur4)
    b[OFF_LR_TIMER & 3] = value & 0xFF
    h.write_bytes(pd + (OFF_LR_TIMER & ~3), bytes(b))


def press_l_on_controller(h, controller_addr, hold=True):
    cur = h.read_word(controller_addr) & 0xFFFFFFFF
    new = (cur | L_BIT) if hold else (cur & ~L_BIT)
    h.write_bytes(controller_addr, struct.pack(">I", new & 0xFFFFFFFF))


def banner(msg):
    print(f"\n{'=' * 70}\n  {msg}\n{'=' * 70}", flush=True)


def sample(h, pd, controller_addr, n_frames, label=None,
           per_frame_action=None, print_each=True):
    """Sample n_frames, optionally invoking per_frame_action(h, pd, i) on each
    frame BEFORE the read. Returns list of (frame, astate, lr, lag_div, act_out).
    """
    out = []
    for i in range(n_frames):
        if per_frame_action is not None:
            per_frame_action(h, pd, controller_addr, i)
        f = h.frame()
        a = read_astate(h, pd)
        lr = read_lr_timer(h, pd)
        ld = read_lag_divisor(h, pd)
        ao = read_act_out(h, pd)
        raw = h.read_word(controller_addr) & 0xFFFFFFFF
        out.append((f, a, lr, ld, ao, raw))
        if print_each:
            tag = ""
            if a in AERIAL_STATES:
                tag = "  [AERIAL]"
            elif a in LANDING_AERIAL_STATES:
                tag = "  [LANDING-AERIAL]"
            elif a == S_LANDING:
                tag = "  [LANDING]"
            elif a == S_KNEEBEND:
                tag = "  [KNEEBEND]"
            elif a == S_JUMPF:
                tag = "  [JUMP]"
            elif a == S_FALL or a == S_FALL_AERIAL:
                tag = "  [FALL]"
            print(f"    f={f}  state=0x{a:04X}  lr={lr:3d}  "
                  f"lag_div={ld:5.3f}  act_out={ao}  ctrl=0x{raw:08X}{tag}",
                  flush=True)
        try:
            h.wait_frames(1, timeout_s=2.0)
        except TimeoutError:
            print("    [warn] frame counter stuck, sleeping", flush=True)
            time.sleep(0.05)
    return out


def reload_slot2(h):
    """Reset to slot 2 between tests."""
    print("  [reset] reloading slot 2 ...", flush=True)
    h.load_savestate(slot=2)
    time.sleep(0.5)


def test_a_baseline(h, pd, controller_addr):
    banner("TEST A: idle baseline (savestate + addresses sanity check)")
    rows = sample(h, pd, controller_addr, 10)
    a0 = rows[0]
    ld0 = a0[3]
    print(f"  baseline: state=0x{a0[1]:04X}  lr={a0[2]}  lag_div={ld0:.3f}", flush=True)
    ok = (a0[1] == S_WAIT or a0[1] in {S_FALL, S_FALL_AERIAL})
    return {"ok": ok, "baseline_lag_div": ld0, "baseline_state": a0[1]}


def test_b_direct_lr_write(h, pd, controller_addr):
    banner("TEST B: direct write to 0x680 (does engine overwrite each frame?)")
    write_lr_timer(h, pd, 50)
    print(f"  wrote 0x680 = 50", flush=True)
    rows = sample(h, pd, controller_addr, 8)
    progression = [r[2] for r in rows]
    print(f"  observed lr_timer over 8 frames: {progression}", flush=True)
    # Expected: each frame increments by 1, so 50, 51, 52, ... clamped at 255.
    incrementing = all(progression[i+1] - progression[i] in (0, 1, 2)
                       for i in range(len(progression) - 1))
    return {"progression": progression, "monotonic_increment": incrementing}


def test_c_controller_l_propagation(h, pd, controller_addr):
    banner("TEST C: controller L=0x40 -- does it reset 0x680?")
    # First clear L and let the timer climb.
    press_l_on_controller(h, controller_addr, hold=False)
    h.wait_frames(20)
    before = read_lr_timer(h, pd)
    print(f"  before: lr_timer = {before}", flush=True)
    # Now hold L for 5 frames and watch.
    print("  holding L for 5 frames ...", flush=True)
    def hold_l(h, pd, ctrl, i):
        press_l_on_controller(h, ctrl, hold=True)
    rows = sample(h, pd, controller_addr, 5, per_frame_action=hold_l)
    held_progression = [r[2] for r in rows]
    print(f"  lr_timer during hold: {held_progression}", flush=True)
    # Release L for 5 frames.
    print("  releasing L for 5 frames ...", flush=True)
    def release_l(h, pd, ctrl, i):
        press_l_on_controller(h, ctrl, hold=False)
    rows2 = sample(h, pd, controller_addr, 5, per_frame_action=release_l)
    release_progression = [r[2] for r in rows2]
    print(f"  lr_timer after release: {release_progression}", flush=True)
    propagated = (min(held_progression) <= 2)
    return {"before": before,
            "held": held_progression,
            "released": release_progression,
            "propagated": propagated}


def _trigger_jump_and_nair(h, pd, controller_addr, do_l_cancel):
    """Common scenario: force Fox into KneeBend, wait for jump, force NAIR,
    wait for landing. If do_l_cancel, hold L on controller from nair onward."""
    # Make sure he's grounded in Wait first.
    if read_astate(h, pd) != S_WAIT:
        print("  [warn] not in Wait at start of trial -- waiting up to 60f",
              flush=True)
        for _ in range(60):
            if read_astate(h, pd) == S_WAIT:
                break
            h.wait_frames(1)

    # Step 1: force KneeBend. Engine should transition to a jump state.
    print("  forcing Fox -> KneeBend (0x18)", flush=True)
    force_astate(h, pd, S_KNEEBEND)
    sample(h, pd, controller_addr, 6, print_each=True)

    # Step 2: if he's airborne (action state changed away from KneeBend/Wait),
    # force AttackAirN. Otherwise, force again.
    a_now = read_astate(h, pd)
    print(f"  after 6 frames: state=0x{a_now:04X}", flush=True)

    # If still in KneeBend / Wait / Landing, try forcing aerial state anyway.
    print("  forcing Fox -> AttackAirN (0x41)", flush=True)
    force_astate(h, pd, S_AIR_N)

    # Step 3: hold L for the entire descent if do_l_cancel.
    def per_frame(h, pd, ctrl, i):
        if do_l_cancel:
            press_l_on_controller(h, ctrl, hold=True)
        else:
            press_l_on_controller(h, ctrl, hold=False)

    # Watch up to 90 frames for landing.
    print(f"  watching nair (l_cancel={do_l_cancel}) for up to 90 frames", flush=True)
    rows = []
    landing_idx = None
    landing_lag_div = None
    landing_lr = None
    for i in range(90):
        per_frame(h, pd, controller_addr, i)
        f = h.frame()
        a = read_astate(h, pd)
        lr = read_lr_timer(h, pd)
        ld = read_lag_divisor(h, pd)
        ao = read_act_out(h, pd)
        raw = h.read_word(controller_addr) & 0xFFFFFFFF
        rows.append((f, a, lr, ld, ao, raw))
        tag = ""
        if a in LANDING_AERIAL_STATES:
            tag = "  [LANDING-AERIAL]"
            if landing_idx is None:
                landing_idx = i
                landing_lag_div = ld
                landing_lr = lr
        elif a == S_LANDING:
            tag = "  [LANDING (plain)]"
            if landing_idx is None:
                landing_idx = i
                landing_lag_div = ld
                landing_lr = lr
        elif a in AERIAL_STATES:
            tag = "  [AERIAL]"
        print(f"    f={f}  state=0x{a:04X}  lr={lr:3d}  "
              f"lag_div={ld:5.3f}  act_out={ao}  ctrl=0x{raw:08X}{tag}", flush=True)
        # Stop early once back to a stable non-aerial state for a few frames
        if a == S_WAIT and landing_idx is not None:
            break
        try:
            h.wait_frames(1, timeout_s=2.0)
        except TimeoutError:
            time.sleep(0.05)
    # Always release L at end.
    press_l_on_controller(h, controller_addr, hold=False)
    return {"rows": rows,
            "landing_index": landing_idx,
            "landing_lag_div": landing_lag_div,
            "landing_lr": landing_lr,
            "final_state": rows[-1][1] if rows else None}


def test_d_nair_no_lcancel(h, pd, controller_addr):
    banner("TEST D: nair WITHOUT L press (expect lag_div = 1.0 at landing)")
    return _trigger_jump_and_nair(h, pd, controller_addr, do_l_cancel=False)


def test_e_nair_lcancel_via_controller(h, pd, controller_addr):
    banner("TEST E: nair WITH L on controller (expect lag_div = 2.0 if it works)")
    return _trigger_jump_and_nair(h, pd, controller_addr, do_l_cancel=True)


def main():
    h = Harness()
    print("[discover] launching Dolphin + hooking dme ...", flush=True)
    h.launch()
    h.hook_dme()
    print("[discover] seeding scenario (auto-loading slot 2) ...", flush=True)
    h.seed_snapshot()

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print(f"[FAIL] P{TARGET_PORT} player data ptr invalid", flush=True)
        return 1
    controller_addr = CONTROLLER_DIGITAL + (TARGET_PORT - 1) * CONTROLLER_STRIDE
    print(f"[discover] P{TARGET_PORT} pd=0x{pd:08X}  controller=0x{controller_addr:08X}",
          flush=True)

    results = {}
    results["A"] = test_a_baseline(h, pd, controller_addr)

    reload_slot2(h)
    pd = h.player_data_ptr(TARGET_PORT)
    results["B"] = test_b_direct_lr_write(h, pd, controller_addr)

    reload_slot2(h)
    pd = h.player_data_ptr(TARGET_PORT)
    results["C"] = test_c_controller_l_propagation(h, pd, controller_addr)

    reload_slot2(h)
    pd = h.player_data_ptr(TARGET_PORT)
    results["D"] = test_d_nair_no_lcancel(h, pd, controller_addr)

    reload_slot2(h)
    pd = h.player_data_ptr(TARGET_PORT)
    results["E"] = test_e_nair_lcancel_via_controller(h, pd, controller_addr)

    banner("SUMMARY")
    print(f"  A baseline:           state=0x{results['A']['baseline_state']:04X}  "
          f"lag_div={results['A']['baseline_lag_div']:.3f}", flush=True)
    print(f"  B direct 0x680 write: progression={results['B']['progression']}  "
          f"monotonic_inc={results['B']['monotonic_increment']}", flush=True)
    c = results["C"]
    print(f"  C ctrl L propagation: before={c['before']}  held={c['held']}  "
          f"released={c['released']}  propagated={c['propagated']}", flush=True)
    d = results["D"]
    print(f"  D no-L nair:    landing_idx={d['landing_index']}  "
          f"landing_lr={d['landing_lr']}  landing_lag_div="
          f"{d['landing_lag_div']}", flush=True)
    e = results["E"]
    print(f"  E ctrl-L nair:  landing_idx={e['landing_index']}  "
          f"landing_lr={e['landing_lr']}  landing_lag_div="
          f"{e['landing_lag_div']}", flush=True)

    print(flush=True)
    if d["landing_lag_div"] is not None and e["landing_lag_div"] is not None:
        if (e["landing_lag_div"] > 1.5 > d["landing_lag_div"]
                and c["propagated"]):
            print("  >>> MECHANIC CONFIRMED: L on controller -> 0x680 reset "
                  "-> lag_div=2.0 at landing. Macro path is viable.",
                  flush=True)
        elif d["landing_lag_div"] == e["landing_lag_div"]:
            print("  >>> INCONCLUSIVE: lag_div didn't differ between trials. "
                  "Either both trials L-cancelled, neither did, or the "
                  "controller writes were overwritten by Dolphin's input pipe.",
                  flush=True)
        else:
            print(f"  >>> UNEXPECTED: D lag={d['landing_lag_div']} "
                  f"E lag={e['landing_lag_div']} -- inspect logs above.",
                  flush=True)
    else:
        print("  >>> NO LANDING DETECTED in one or both trials. "
              "Action-state forcing may not produce a clean jump+landing -- "
              "may need to drive jump via controller writes instead.",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
