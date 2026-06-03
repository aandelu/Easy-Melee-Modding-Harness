"""
offline_cactuar_probe.py -- OFFLINE discovery rig for the Cactuar dash macro.

This script does NOT yet contain the macro's veto logic. It is a pure SELF-DRIVE
injector whose only job is to establish the empirical facts the macro design
depends on, so the next script (the real veto) is built on measurements, not
guesses. It answers:

  1. Stick-byte encoding at the offline consumer hook 0x803775B8: writing 2(r25)
     (stickX) / 3(r25) (stickY) -- what sign is "right", and which Y sign makes
     the character CROUCH. Confirmed by reading the processed stick floats
     (Player Data 0x620 = Analog Stick X, 0x624 = Analog Stick Y) and facing
     (0x2C, +1 right / -1 left).
  2. BASELINE slow turnaround: drive a Run (0x15), then SLAM the stick to the
     opposite extreme. Capture the action-state stream -> the slow turn shows up
     as Turn (0x12) / TurnRun (0x13). This is exactly what the macro must avoid.
  3. CROUCH-out-of-run sequence: drive a Run, then inject stick-DOWN. Capture the
     sequence Run -> Squat (0x27) -> SquatWait (0x28) and MEASURE how many frames
     Squat (0x27) lasts -- needed to anchor the "7 frames in crouch" timer on the
     squat state's Action State Frame Counter (0x894) without a scratch counter.

Self-drive injection (offline, consumer-side -- fine offline; no netplay): at
0x803775B8 the pad struct is at r25. We write stickX -> 2(r25), stickY -> 3(r25)
(signed bytes). Python orchestrates phases via scratch bytes; the cave just reads
them and writes the stick.

NOTE: offline only validates the mechanic; netplay-safety / the 2-frame delay are
online-only and handled in the online port.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_cactuar_probe.py
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
CAVE = 0x803FA600                 # clear of meta-flush control plane (guide §6)

# --- scratch control block (above control plane 0x803FA440-0x44C, below cave) ---
DRIVE_VAL_ADDR = 0x803FA460       # signed byte: stickX to inject during ramp
MODE_ADDR      = 0x803FA461       # 0 = slam-opposite (baseline turn), 1 = crouch-down
SLAM_ADDR      = 0x803FA462       # 0 = ramp phase, 1 = reversal/inject phase
DOWN_VAL_ADDR  = 0x803FA463       # signed byte: stickY value to inject for "down"
HOOK_FIRE_ADDR = 0x803FA464       # byte: increments every cave fire (liveness)

TARGET_PORT = 2                   # Fox = P2 in slot 2
OFF_ACTION_STATE = 0x10
OFF_FACING       = 0x2C           # float, +1 right / -1 left
OFF_POS_X        = 0xB0           # float, + right
OFF_PROC_STICK_X = 0x620         # float
OFF_PROC_STICK_Y = 0x624         # float
OFF_GROUND_VEL   = 0xEC          # float, self-induced ground velocity

# Slot 2: Fox (P2) starts grounded at posX=+60 facing LEFT, near the right ledge.
# So we ramp LEFT (toward/past center) to get room, then slam RIGHT to turn around.
DRIVE_RAMP = 0x90                 # -0x70 signed: drive stickX full LEFT for the ramp
TARGET_X   = -25.0               # ramp until past this X so the rightward run has room

S_WAIT     = 0x000E
S_TURN     = 0x0012
S_TURNRUN  = 0x0013
S_DASH     = 0x0014
S_RUN      = 0x0015
S_RUNBRAKE = 0x0017
S_SQUAT    = 0x0027              # entering crouch
S_SQUATWAIT = 0x0028            # held crouch

STATE_NAMES = {
    S_WAIT: "Wait", S_TURN: "Turn", S_TURNRUN: "TurnRun", S_DASH: "Dash",
    S_RUN: "Run", 0x0016: "RunDirect", S_RUNBRAKE: "RunBrake",
    S_SQUAT: "Squat", S_SQUATWAIT: "SquatWait", 0x0029: "SquatRv",
}


def sname(st):
    return STATE_NAMES.get(st, f"0x{st:04X}")


CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    cmpwi 24, {TARGET_PORT - 1}      # only the target port
    bne  end

    # liveness counter
    lis  9, 0x{(HOOK_FIRE_ADDR >> 16):04X}
    ori  9, 9, 0x{(HOOK_FIRE_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    addi 8, 8, 1
    stb  8, 0(9)

    # SLAM flag
    lis  9, 0x{(SLAM_ADDR >> 16):04X}
    ori  9, 9, 0x{(SLAM_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpwi 7, 0
    bne  reversal

ramp:
    # stickX = DRIVE_VAL, stickY = 0
    lis  9, 0x{(DRIVE_VAL_ADDR >> 16):04X}
    ori  9, 9, 0x{(DRIVE_VAL_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    stb  8, 2(25)
    li   8, 0
    stb  8, 3(25)
    b    end

reversal:
    lis  9, 0x{(MODE_ADDR >> 16):04X}
    ori  9, 9, 0x{(MODE_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpwi 7, 0
    bne  crouch

slam:
    # stickX = -DRIVE_VAL, stickY = 0  (smash to the opposite extreme)
    lis  9, 0x{(DRIVE_VAL_ADDR >> 16):04X}
    ori  9, 9, 0x{(DRIVE_VAL_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    extsb 8, 8
    neg  8, 8
    stb  8, 2(25)
    li   8, 0
    stb  8, 3(25)
    b    end

crouch:
    # stickX = 0, stickY = DOWN_VAL
    li   8, 0
    stb  8, 2(25)
    lis  9, 0x{(DOWN_VAL_ADDR >> 16):04X}
    ori  9, 9, 0x{(DOWN_VAL_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    stb  8, 3(25)

end:
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
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
    """read a float field of player data."""
    try:
        return struct.unpack(">f", h.read_bytes(pd + off, 4))[0]
    except Exception:
        return float("nan")


def state(h):
    return h.read_word(h.player_data_ptr(TARGET_PORT) + OFF_ACTION_STATE) & 0xFFFF


def wait_for_state(h, want, timeout_frames=180):
    for _ in range(timeout_frames):
        if state(h) == want:
            return True
        h.wait_frames(1)
    return False


def stream(h, pd, n_frames):
    """Per-frame (frame_offset, action_state) for n_frames."""
    out = []
    f0 = h.frame()
    for _ in range(n_frames):
        out.append((h.frame() - f0, h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF))
        h.wait_frames(1)
    return out


def fmt_stream(recs):
    """Compress consecutive identical states into 'Name xN'."""
    parts, prev, count = [], None, 0
    for _, st in recs:
        if st == prev:
            count += 1
        else:
            if prev is not None:
                parts.append(f"{sname(prev)}x{count}")
            prev, count = st, 1
    if prev is not None:
        parts.append(f"{sname(prev)}x{count}")
    return " -> ".join(parts)


def ramp_to_run(h):
    """Drive Fox LEFT until he is in Run AND past TARGET_X (room to turn right)."""
    pd = h.player_data_ptr(TARGET_PORT)
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DRIVE_VAL_ADDR, bytes([DRIVE_RAMP]))
    reached_run = False
    for _ in range(180):
        st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
        x = rf(h, pd, OFF_POS_X)
        if st == S_RUN:
            reached_run = True
        if reached_run and x <= TARGET_X:
            return True
        if st == 0x0000:            # died -- bail
            return False
        h.wait_frames(1)
    return reached_run


def neutralize(h):
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DRIVE_VAL_ADDR, b"\x00")    # neutral stick
    wait_for_state(h, S_WAIT, timeout_frames=120)
    h.wait_frames(3)


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED)
    print(f"[cp] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[cp] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[cp] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[cp] target port invalid -- abort", flush=True)
        return 1
    print(f"[cp] P{TARGET_PORT} pd=0x{pd:08X}", flush=True)

    # defaults
    h.write_bytes(DRIVE_VAL_ADDR, bytes([DRIVE_RAMP]))
    h.write_bytes(MODE_ADDR, b"\x00")
    h.write_bytes(SLAM_ADDR, b"\x00")
    h.write_bytes(DOWN_VAL_ADDR, bytes([0x90]))   # -0x70 as a signed byte
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    print(f"[cp] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(HOOK_FIRE_ADDR, 1)[0]
    print(f"[cp] hook fires: {fires} (must be > 0)", flush=True)
    if fires == 0:
        print("[cp] cave not firing -- abort", flush=True)
        return 1

    # ---------------------------------------------------------------
    # Q1 + baseline run: drive a run-right, confirm stick encoding
    # ---------------------------------------------------------------
    print("\n" + "=" * 64 + "\nQ1: stick encoding while running LEFT (DRIVE_RAMP=0x90)\n"
          + "=" * 64, flush=True)
    neutralize(h)
    if not ramp_to_run(h):
        print(f"[cp] never reached Run/target; current state = {sname(state(h))} "
              f"posX={rf(h, pd, OFF_POS_X):+.1f}", flush=True)
    facing = rf(h, pd, OFF_FACING)
    sx = rf(h, pd, OFF_PROC_STICK_X)
    sy = rf(h, pd, OFF_PROC_STICK_Y)
    print(f"  running: state={sname(state(h))} posX={rf(h, pd, OFF_POS_X):+.1f} "
          f"facing={facing:+.2f} proc_stickX={sx:+.3f} proc_stickY={sy:+.3f}", flush=True)
    print(f"  => DRIVE_RAMP=0x90 (left) produces facing {facing:+.0f}, proc stickX {sx:+.2f} "
          f"(expect both NEGATIVE = running left)", flush=True)

    # ---------------------------------------------------------------
    # Q2: BASELINE slow turnaround (slam stick opposite)
    # ---------------------------------------------------------------
    print("\n" + "=" * 64 + "\nQ2: baseline slow turn (Run -> slam opposite)\n"
          + "=" * 64, flush=True)
    neutralize(h)
    ramp_to_run(h)
    h.write_bytes(MODE_ADDR, b"\x00")     # slam-opposite
    h.write_bytes(SLAM_ADDR, b"\x01")
    recs = stream(h, pd, 35)
    print(f"  stream: {fmt_stream(recs)}", flush=True)
    turn_frames = sum(1 for _, st in recs if st in (S_TURN, S_TURNRUN))
    print(f"  Turn/TurnRun frames seen: {turn_frames} "
          f"(this is the slow animation the macro must replace)", flush=True)
    print(f"  end state={sname(state(h))} facing={rf(h, pd, OFF_FACING):+.0f} "
          f"posX={rf(h, pd, OFF_POS_X):+.1f}", flush=True)

    # ---------------------------------------------------------------
    # Q3: crouch-out-of-run + Squat(0x27) duration (sweep down sign)
    # ---------------------------------------------------------------
    print("\n" + "=" * 64 + "\nQ3: crouch out of run (sweep stickY-down sign)\n"
          + "=" * 64, flush=True)
    for down_val in (0x90, 0x70):       # -0x70 then +0x70
        signed = down_val - 256 if down_val >= 128 else down_val
        print(f"\n  -- DOWN_VAL=0x{down_val:02X} ({signed:+d}) --", flush=True)
        neutralize(h)
        ramp_to_run(h)
        h.write_bytes(MODE_ADDR, b"\x01")     # crouch
        h.write_bytes(DOWN_VAL_ADDR, bytes([down_val]))
        h.write_bytes(SLAM_ADDR, b"\x01")
        recs = stream(h, pd, 45)
        print(f"    stream: {fmt_stream(recs)}", flush=True)
        proc_y = rf(h, pd, OFF_PROC_STICK_Y)
        squat_frames = sum(1 for _, st in recs if st == S_SQUAT)
        reached_squatwait = any(st == S_SQUATWAIT for _, st in recs)
        print(f"    proc_stickY now={proc_y:+.3f}  Squat(0x27) frames={squat_frames}  "
              f"reached SquatWait(0x28)={reached_squatwait}", flush=True)
        if squat_frames or reached_squatwait:
            print(f"    => DOWN_VAL=0x{down_val:02X} CROUCHES. Squat(0x27) lasts "
                  f"~{squat_frames}f before SquatWait; anchor the 7-frame timer "
                  f"as: hold while Squat(0x27), then SquatWait(0x28) 0x894 < {7 - squat_frames}.",
                  flush=True)

    neutralize(h)
    print("\n[cp] discovery complete. Leaving Dolphin running.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
