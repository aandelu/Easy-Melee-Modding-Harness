"""
offline_cactuar_macro.py -- OFFLINE Cactuar dash veto macro + A/B test.

Builds the real veto state machine and proves it offline (consumer hook
0x803775B8; netplay-safety/2-frame delay are handled in the online port).

ONE cave does two jobs (offline test rig):
  (1) SELF-DRIVE the stimulus: ramp Fox LEFT to room (posX <= -25), then SLAM the
      stick RIGHT -- i.e. the player "tries to turn around" out of a run.
  (2) The MACRO (gated by MACRO_ON, so we can A/B in one session):
        phase TRIGGER: state in {Run 0x15, RunBrake 0x17} AND fresh stick X is the
                       extreme OPPOSITE to facing (|X| >= XTHRESH) AND |Y| <= YDEAD
                       -> override stick to (X=0, Y=down).
        phase HOLD:    state == Squat 0x27 (the 7-frame enter-crouch transition)
                       -> keep overriding to down.
        phase RELEASE: anything else (incl. SquatWait 0x28) -> do nothing; the
                       player's still-held opposite stick now dashes them out.
      Net: Run -> (veto) -> Squat(7f) -> release -> Dash the new way, skipping the
      ~21f TurnRun (0x13) baseline.

Findings baked in from offline_cactuar_probe.py:
  +X=right -X=left ; -Y=down ; Fox(P2) starts posX=+60 facing left ; Squat(0x27)=7f ;
  baseline turn = TurnRun(0x13) ~21f.

A/B:
  MACRO_ON=0 -> expect TurnRun(0x13) (slow turn).
  MACRO_ON=1 -> expect Squat(0x27)->SquatWait/Dash, NO TurnRun.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_cactuar_macro.py
"""
import struct
import subprocess
import sys
import time

import keystone

from melee_harness import finalize_payload, Harness
import instr_writer as iw

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000            # lhz r0, 0(r25)
CAVE = 0x803FA600

# --- scratch control block ---
DRIVE_VAL_ADDR = 0x803FA460       # signed byte: ramp stickX (0x90 = left)
SLAM_ADDR      = 0x803FA462       # 0 = ramp, 1 = slam-opposite
DOWN_VAL_ADDR  = 0x803FA463       # signed byte: crouch stickY (0x80 = full down)
HOOK_FIRE_ADDR = 0x803FA464       # liveness counter
MACRO_ON_ADDR  = 0x803FA465       # 0 = veto off (baseline), 1 = veto on
XTHRESH_ADDR   = 0x803FA466       # |stickX| opposite-extreme threshold (e.g. 0x60)
YDEAD_ADDR     = 0x803FA467       # |stickY| max to count as "Y=0" (e.g. 0x18)

TARGET_PORT = 2
GOBJ_ADDR = 0x80453130 + (TARGET_PORT - 1) * 0xE90
OFF_ACTION_STATE = 0x10
OFF_FACING = 0x2C
OFF_POS_X  = 0xB0

DRIVE_RAMP = 0x90                 # left
TARGET_X   = -25.0

S_WAIT, S_TURN, S_TURNRUN = 0x000E, 0x0012, 0x0013
S_DASH, S_RUN, S_RUNDIR, S_RUNBRAKE = 0x0014, 0x0015, 0x0016, 0x0017
S_SQUAT, S_SQUATWAIT, S_SQUATRV = 0x0027, 0x0028, 0x0029
STATE_NAMES = {
    S_WAIT: "Wait", S_TURN: "Turn", S_TURNRUN: "TurnRun", S_DASH: "Dash",
    S_RUN: "Run", S_RUNDIR: "RunDirect", S_RUNBRAKE: "RunBrake",
    S_SQUAT: "Squat", S_SQUATWAIT: "SquatWait", S_SQUATRV: "SquatRv",
    0x001D: "Fall", 0x0000: "(0/dead)",
}


def sname(st):
    return STATE_NAMES.get(st, f"0x{st:04X}")


def hi(a):
    return (a >> 16) & 0xFFFF


def lo(a):
    return a & 0xFFFF


CAVE_ASM = f"""
    stwu 1, -0x30(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)

    cmpwi 24, {TARGET_PORT - 1}
    bne  end

    # liveness
    lis  5, 0x{hi(HOOK_FIRE_ADDR):04X}
    ori  5, 5, 0x{lo(HOOK_FIRE_ADDR):04X}
    lbz  8, 0(5)
    addi 8, 8, 1
    stb  8, 0(5)

    # ---------------- self-drive stimulus ----------------
    lis  5, 0x{hi(SLAM_ADDR):04X}
    ori  5, 5, 0x{lo(SLAM_ADDR):04X}
    lbz  8, 0(5)
    cmpwi 8, 0
    bne  sd_slam
sd_ramp:
    lis  5, 0x{hi(DRIVE_VAL_ADDR):04X}
    ori  5, 5, 0x{lo(DRIVE_VAL_ADDR):04X}
    lbz  8, 0(5)
    stb  8, 2(25)
    li   8, 0
    stb  8, 3(25)
    b    macro_check
sd_slam:
    lis  5, 0x{hi(DRIVE_VAL_ADDR):04X}
    ori  5, 5, 0x{lo(DRIVE_VAL_ADDR):04X}
    lbz  8, 0(5)
    extsb 8, 8
    neg  8, 8
    stb  8, 2(25)
    li   8, 0
    stb  8, 3(25)

    # ---------------- macro veto (if MACRO_ON) ----------------
macro_check:
    lis  5, 0x{hi(MACRO_ON_ADDR):04X}
    ori  5, 5, 0x{lo(MACRO_ON_ADDR):04X}
    lbz  5, 0(5)
    cmpwi 5, 0
    beq  end

    # compute player data ptr with MEM1 checks
    lis  6, 0x{hi(GOBJ_ADDR):04X}
    ori  6, 6, 0x{lo(GOBJ_ADDR):04X}
    lwz  6, 0(6)
    cmpwi 6, 0
    beq  end
    srwi 5, 6, 24
    cmplwi 5, 0x80
    bne  end
    lwz  6, 0x2C(6)
    cmpwi 6, 0
    beq  end
    srwi 5, 6, 24
    cmplwi 5, 0x80
    bne  end

    lwz  7, 0x{OFF_ACTION_STATE:02X}(6)
    rlwinm 7, 7, 0, 16, 31              # r7 = action state

    # phase HOLD: already in Squat(0x27) -> keep crouching
    cmpwi 7, 0x27
    beq  do_override

    # phase TRIGGER: state in Run 0x15 / RunBrake 0x17 ?
    cmpwi 7, 0x15
    beq  trig_state
    cmpwi 7, 0x17
    bne  end
trig_state:
    # |stickY| <= YDEAD ?
    lbz  9, 3(25)
    extsb 9, 9
    srawi 5, 9, 31                     # sign mask
    xor  9, 9, 5
    subf 9, 5, 9                       # r9 = abs(stickY)
    lis  5, 0x{hi(YDEAD_ADDR):04X}
    ori  5, 5, 0x{lo(YDEAD_ADDR):04X}
    lbz  5, 0(5)
    cmpw 9, 5
    bgt  end                           # Y not near 0 -> not a clean turnaround

    # stickX opposite-extreme relative to facing?
    lwz  10, 0x{OFF_FACING:02X}(6)      # facing float bits (sign = direction)
    lbz  8, 2(25)
    extsb 8, 8                         # r8 = stickX signed
    lis  5, 0x{hi(XTHRESH_ADDR):04X}
    ori  5, 5, 0x{lo(XTHRESH_ADDR):04X}
    lbz  5, 0(5)                       # r5 = XTHRESH (positive)
    cmpwi 10, 0
    blt  fac_left
    # facing right -> opposite is LEFT: need stickX <= -XTHRESH
    neg  5, 5
    cmpw 8, 5
    bgt  end                           # stickX > -XTHRESH -> not far enough left
    b    do_override
fac_left:
    # facing left -> opposite is RIGHT: need stickX >= +XTHRESH
    cmpw 8, 5
    blt  end                           # stickX < XTHRESH -> not far enough right

do_override:
    li   5, 0
    stb  5, 2(25)                      # stickX = 0
    lis  5, 0x{hi(DOWN_VAL_ADDR):04X}
    ori  5, 5, 0x{lo(DOWN_VAL_ADDR):04X}
    lbz  5, 0(5)
    stb  5, 3(25)                      # stickY = down

end:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    addi 1, 1, 0x30
"""


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"],
                      capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def rf(h, pd, off):
    try:
        return struct.unpack(">f", h.read_bytes(pd + off, 4))[0]
    except Exception:
        return float("nan")


def state(h, pd):
    return h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF


def stream(h, pd, n):
    out = []
    f0 = h.frame()
    for _ in range(n):
        out.append((h.frame() - f0, h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF))
        h.wait_frames(1)
    return out


def fmt_stream(recs):
    parts, prev, c = [], None, 0
    for _, st in recs:
        if st == prev:
            c += 1
        else:
            if prev is not None:
                parts.append(f"{sname(prev)}x{c}")
            prev, c = st, 1
    if prev is not None:
        parts.append(f"{sname(prev)}x{c}")
    return " -> ".join(parts)


def ramp_to_run(h, pd):
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DRIVE_VAL_ADDR, bytes([DRIVE_RAMP]))
    reached = False
    for _ in range(180):
        st = state(h, pd)
        if st == S_RUN:
            reached = True
        if reached and rf(h, pd, OFF_POS_X) <= TARGET_X:
            return True
        if st == 0x0000:
            return False
        h.wait_frames(1)
    return reached


def neutralize(h, pd):
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DRIVE_VAL_ADDR, b"\x00")
    for _ in range(120):
        if state(h, pd) == S_WAIT:
            break
        h.wait_frames(1)
    h.wait_frames(3)


def episode(h, pd, macro_on):
    h.write_bytes(MACRO_ON_ADDR, bytes([1 if macro_on else 0]))
    neutralize(h, pd)
    if not ramp_to_run(h, pd):
        return None
    start_face = rf(h, pd, OFF_FACING)
    h.write_bytes(SLAM_ADDR, b"\x01")     # slam right (turnaround attempt)
    recs = stream(h, pd, 40)
    h.write_bytes(SLAM_ADDR, b"\x00")
    end_face = rf(h, pd, OFF_FACING)
    end_x = rf(h, pd, OFF_POS_X)
    states = [st for _, st in recs]
    return {
        "stream": fmt_stream(recs),
        "turnrun_frames": sum(1 for s in states if s == S_TURNRUN),   # 0x13 slow turn
        "turn_frames": sum(1 for s in states if s == S_TURN),         # 0x12 pivot (ok)
        "squat": any(s == S_SQUAT for s in states),
        "squatwait": any(s == S_SQUATWAIT for s in states),
        "dash_after": any(s == S_DASH for s in states),
        "turned": start_face != end_face,
        "start_face": start_face, "end_face": end_face, "end_x": end_x,
    }


def report(label, r):
    print("\n" + "=" * 64 + f"\n{label}\n" + "=" * 64, flush=True)
    if r is None:
        print("  (could not set up run -- Fox didn't reach Run/target)", flush=True)
        return
    print(f"  stream: {r['stream']}", flush=True)
    print(f"  TurnRun(0x13,slow) frames: {r['turnrun_frames']}   "
          f"Turn(0x12,pivot) frames: {r['turn_frames']}   "
          f"Squat: {r['squat']}   SquatWait: {r['squatwait']}   Dash after: {r['dash_after']}",
          flush=True)
    print(f"  facing {r['start_face']:+.0f} -> {r['end_face']:+.0f} "
          f"(turned around: {r['turned']})   end posX={r['end_x']:+.1f}", flush=True)


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED)
    print(f"[cm] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[cm] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[cm] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[cm] target port invalid -- abort", flush=True)
        return 1
    print(f"[cm] P{TARGET_PORT} pd=0x{pd:08X}", flush=True)

    h.write_bytes(DRIVE_VAL_ADDR, bytes([DRIVE_RAMP]))
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DOWN_VAL_ADDR, bytes([0x80]))   # full down
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")
    h.write_bytes(MACRO_ON_ADDR, b"\x00")
    h.write_bytes(XTHRESH_ADDR, bytes([0x60]))
    h.write_bytes(YDEAD_ADDR, bytes([0x18]))

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    print(f"[cm] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(HOOK_FIRE_ADDR, 1)[0]
    print(f"[cm] hook fires: {fires} (must be > 0)", flush=True)
    if fires == 0:
        return 1

    # A/B: two runs each to confirm reproducibility
    base = episode(h, pd, macro_on=False)
    report("BASELINE (MACRO_ON=0): expect slow TurnRun(0x13)", base)
    base2 = episode(h, pd, macro_on=False)
    report("BASELINE (repeat)", base2)

    cac = episode(h, pd, macro_on=True)
    report("CACTUAR (MACRO_ON=1): expect Squat->dash, NO TurnRun", cac)
    cac2 = episode(h, pd, macro_on=True)
    report("CACTUAR (repeat)", cac2)

    print("\n" + "=" * 64 + "\nVERDICT\n" + "=" * 64, flush=True)
    ok = (base and cac and base["turnrun_frames"] > 0
          and cac["turnrun_frames"] == 0 and cac["squat"]
          and cac["dash_after"] and cac["turned"])
    if ok:
        print(f"  [PASS] baseline slow TurnRun(0x13)={base['turnrun_frames']}f "
              f"ELIMINATED by Cactuar (TurnRun={cac['turnrun_frames']}f). "
              f"Replaced by crouch (Squat 0x27, 7f) -> dash the new way "
              f"(Dash seen, turned={cac['turned']}, posX {cac['end_x']:+.1f}). "
              f"Harmless 1f pivot Turn(0x12)={cac['turn_frames']}f.", flush=True)
        return 0
    print("  [?] inspect streams above -- veto not cleanly replacing the turn.",
          flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
