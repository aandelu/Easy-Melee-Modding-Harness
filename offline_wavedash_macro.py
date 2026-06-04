"""
offline_wavedash_macro.py -- OFFLINE validation of the REAL wavedash macro logic.

Unlike offline_wavedash_probe.py (count-based discovery), this is the actual
state-gated logic that will become the online gecko, validated offline (slot 2,
Fox P2, delay=0). It:

  * SIMULATES the human holding up+direction (a preamble that forces stickY=up
    and stickX=SIM_DIR every frame -- online this is removed; the human provides
    it). This is the only "fake" part; the macro logic below is the real thing.
  * TRIGGER: stickY >= UP_THRESH (up held).
  * JUMP: injects Y in Wait (drives the jump; held-up gives no fresh tap-jump
    edge, so the macro owns the jump -- covers first + repeat uniformly).
  * AIRDODGE: when state==KneeBend(0x18) AND the action-state frame counter
    (0x894, decoded to int without FPU) == LJF, override the stick to the angle
    chosen by held direction (right=(0x6A,0xE0) / left=(0x96,0xE0) /
    down=(0,0x90)) and press digital L (0x40) -> wavedash (LandingFallSpecial).
  * REPEAT falls out for free: LandingFallSpecial -> Wait -> Y again -> ...

Sweeps LJF to find the asfc that lands the tight (frame-perfect, no air frames)
wavedash off a FULL hop (held-up tap-jump), then confirms left/right/down + that
it repeats while up is held.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_wavedash_macro.py
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

ENABLE_ADDR    = 0x803FA460       # byte: 0 = pass through, 1 = drive (sim human up)
SIMDIR_ADDR    = 0x803FA461       # signed byte: simulated held stickX (dir)
LJF_ADDR       = 0x803FA462       # byte: airdodge asfc gate (last jumpsquat frame)
UPTHRESH_ADDR  = 0x803FA463       # byte: up-trigger threshold
FIRE_ADDR      = 0x803FA464       # byte: liveness

TARGET_PORT = 2                   # Fox = P2 (port index 1)

OFF_ACTION_STATE = 0x10
OFF_ASFC = 0x894
OFF_POS_X = 0xB0                  # float, + right
OFF_GROUND_VEL = 0xEC            # float, self-induced ground velocity (+right)

S_WAIT = 0x000E
S_KNEEBEND = 0x0018
S_JUMPF = 0x0019
S_FALLAERIAL = 0x0020
S_LANDING = 0x002A
S_LANDINGFALLSPECIAL = 0x002B
S_ESCAPEAIR = 0x00EC

STATE_NAMES = {
    S_WAIT: "Wait", 0x0F: "WalkSlow", 0x12: "Turn", 0x14: "Dash", 0x15: "Run",
    S_KNEEBEND: "KneeBend", S_JUMPF: "JumpF", 0x1A: "JumpB", 0x1D: "Fall",
    S_FALLAERIAL: "FallAerial", 0x27: "Squat", 0x28: "SquatWait",
    S_LANDING: "Landing", S_LANDINGFALLSPECIAL: "LandingFallSpecial(WD)",
    S_ESCAPEAIR: "EscapeAir(AD)",
}


def sname(st):
    return STATE_NAMES.get(st, f"0x{st:04X}")


CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)

    cmpwi 24, {TARGET_PORT - 1}        # Fox port only
    bne  done

    # liveness
    lis  9, 0x{(FIRE_ADDR >> 16):04X}
    ori  9, 9, 0x{(FIRE_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    addi 8, 8, 1
    stb  8, 0(9)

    # ENABLE?
    lis  9, 0x{(ENABLE_ADDR >> 16):04X}
    ori  9, 9, 0x{(ENABLE_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpwi 7, 0
    beq  done

    # resolve Fox Player Data via r24 (the port being pad-read)
    lis  5, 0x8045
    ori  5, 5, 0x3130
    mulli 9, 24, 0xE90
    add  5, 5, 9
    lwz  5, 0(5)              # GObj
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)           # Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done

    # --- SIM human (offline only): force stickY=up, stickX=SIM_DIR ---
    li   8, 0x70
    stb  8, 3(25)
    lis  9, 0x{(SIMDIR_ADDR >> 16):04X}
    ori  9, 9, 0x{(SIMDIR_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    stb  8, 2(25)

    # --- TRIGGER: up held? stickY >= UP_THRESH ---
    lbz  6, 3(25)
    extsb 6, 6
    lis  9, 0x{(UPTHRESH_ADDR >> 16):04X}
    ori  9, 9, 0x{(UPTHRESH_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpw 6, 7
    blt  done

    # --- AIRDODGE gate: KneeBend(0x18) AND asfc == LJF ---
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x18
    bne  chk_repeat
    lwz  6, 0x894(5)            # asfc float bits
    rlwinm 10, 6, 9, 24, 31     # exp
    rlwinm 6, 6, 0, 9, 31       # mantissa
    oris 6, 6, 0x0080           #   | 0x800000
    subfic 10, 10, 150          # 150 - exp
    srw  6, 6, 10               # r6 = asfc int
    lis  9, 0x{(LJF_ADDR >> 16):04X}
    ori  9, 9, 0x{(LJF_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpw 6, 7
    bne  done
    # press digital L (0x40) + directional airdodge stick
    lhz  9, 0(25)
    ori  9, 9, 0x0040
    sth  9, 0(25)
    lbz  10, 2(25)
    extsb 10, 10               # stickX (held direction)
    cmpwi 10, 0x30
    bge  ad_right
    cmpwi 10, -0x30
    ble  ad_left
    li   8, 0                  # down: (0, -0x70)
    li   6, -112
    b    ad_set
ad_right:
    li   8, 0x6A               # right: (+0x6A, -0x20)
    li   6, -32
    b    ad_set
ad_left:
    li   8, -0x6A              # left: (-0x6A, -0x20)
    li   6, -32
ad_set:
    stb  8, 2(25)
    stb  6, 3(25)
    b    done

chk_repeat:
    # JUMP: any grounded-actionable state (Wait..RunBrake = 0x0E..0x17) + up held
    # -> inject Y. Range (not just Wait) so a held horizontal stick that lands you
    # in WalkFast/Dash after a wavedash still gets the jump edge -> repeat works.
    cmpwi 7, 0x0E
    blt  done
    cmpwi 7, 0x17
    bgt  done
    lhz  9, 0(25)
    ori  9, 9, 0x0800
    sth  9, 0(25)

done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
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
    try:
        return struct.unpack(">f", h.read_bytes(pd + off, 4))[0]
    except Exception:
        return float("nan")


def state(h, pd):
    return h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF


def wait_for_state(h, pd, want, timeout_frames=180):
    for _ in range(timeout_frames):
        if state(h, pd) == want:
            return True
        h.wait_frames(1)
    return False


def stream(h, pd, n):
    """Per-frame (state, asfc-int) for n frames."""
    out = []
    for _ in range(n):
        st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
        af = rf(h, pd, OFF_ASFC)
        out.append((st, int(af) if af == af else -1))
        h.wait_frames(1)
    return out


def fmt(recs):
    parts, prev, c = [], None, 0
    for st, _ in recs:
        if st == prev:
            c += 1
        else:
            if prev is not None:
                parts.append(f"{sname(prev)}x{c}")
            prev, c = st, 1
    if prev is not None:
        parts.append(f"{sname(prev)}x{c}")
    return " -> ".join(parts)


def sb(v):
    return bytes([v & 0xFF])


def neutralize(h, pd):
    h.write_bytes(ENABLE_ADDR, b"\x00")
    wait_for_state(h, pd, S_WAIT, timeout_frames=180)
    h.wait_frames(3)


def run_drive_from_here(h, pd, sim_dir, ljf, frames):
    """Drive WITHOUT neutralizing first (caller already in a known state)."""
    h.write_bytes(SIMDIR_ADDR, sb(sim_dir))
    h.write_bytes(LJF_ADDR, bytes([ljf]))
    h.write_bytes(ENABLE_ADDR, b"\x01")
    recs = stream(h, pd, frames)
    h.write_bytes(ENABLE_ADDR, b"\x00")
    return recs


def run_drive(h, pd, sim_dir, ljf, frames):
    neutralize(h, pd)
    if state(h, pd) != S_WAIT:
        return None
    return run_drive_from_here(h, pd, sim_dir, ljf, frames)


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED)
    print(f"[wm] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[wm] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[wm] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[wm] target port invalid -- abort", flush=True)
        return 1
    print(f"[wm] P{TARGET_PORT} pd=0x{pd:08X} state={sname(state(h, pd))}", flush=True)

    h.write_bytes(ENABLE_ADDR, b"\x00")
    h.write_bytes(SIMDIR_ADDR, sb(0x70))
    h.write_bytes(LJF_ADDR, bytes([3]))
    h.write_bytes(UPTHRESH_ADDR, bytes([0x40]))
    h.write_bytes(FIRE_ADDR, b"\x00")

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    print(f"[wm] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(FIRE_ADDR, 1)[0]
    print(f"[wm] hook fires: {fires} (>0)", flush=True)
    if fires == 0:
        print("[wm] cave not firing -- abort", flush=True)
        return 1

    # =================================================================
    # Sweep LJF (right wavedash) to find the tight wavedash asfc
    # =================================================================
    print("\n" + "=" * 64 + "\nLJF sweep (SIM_DIR=right) -- find tight wavedash\n"
          + "=" * 64, flush=True)
    best = None
    for ljf in (1, 2, 3, 4):
        recs = run_drive(h, pd, 0x70, ljf, 26)
        if recs is None:
            print(f"  LJF={ljf}: not in Wait, skip", flush=True)
            continue
        got_wd = any(st == S_LANDINGFALLSPECIAL for st, _ in recs)
        air = sum(1 for st, _ in recs if st in (S_JUMPF, S_FALLAERIAL, S_ESCAPEAIR))
        kb_asfc = [af for st, af in recs if st == S_KNEEBEND]
        tag = "  <<< WAVEDASH" if got_wd else ""
        tight = " TIGHT(no air)" if got_wd and air == 0 else ""
        print(f"  LJF={ljf}: {fmt(recs)}{tag}{tight}", flush=True)
        print(f"      KneeBend asfc values seen={kb_asfc}  air-frames={air}",
              flush=True)
        if got_wd and air == 0 and best is None:
            best = ljf
    if best is None:
        # fall back to any LJF that wavedashed
        best = 3
    print(f"\n[wm] using LJF={best} for direction + repeat tests", flush=True)

    # =================================================================
    # Direction tests: right / left / down (single wavedash each)
    # =================================================================
    print("\n" + "=" * 64 + "\nDirection tests (LJF=%d)\n" % best + "=" * 64,
          flush=True)
    for label, d, want in [("RIGHT (+0x70)", 0x70, +1), ("LEFT  (-0x70)", 0x90, -1),
                           ("DOWN  ( 0x00)", 0x00, 0)]:
        neutralize(h, pd)
        if state(h, pd) != S_WAIT:
            print(f"  {label}: not in Wait, skip", flush=True)
            continue
        x0 = rf(h, pd, OFF_POS_X)
        recs = run_drive_from_here(h, pd, d, best, 22)
        x1 = rf(h, pd, OFF_POS_X)
        got_wd = any(st == S_LANDINGFALLSPECIAL for st, _ in recs)
        dx = x1 - x0
        ok = "OK" if (want == 0 or (dx > 0) == (want > 0)) else "WRONG-DIR"
        print(f"  {label}: {fmt(recs)}  {'WAVEDASH' if got_wd else 'NO WD'}  "
              f"posX {x0:+.1f}->{x1:+.1f} (dx={dx:+.1f}) {ok if got_wd else ''}",
              flush=True)

    # =================================================================
    # Repeat test: hold ENABLE longer, count wavedash cycles
    # =================================================================
    print("\n" + "=" * 64 + "\nRepeat test (hold up, LJF=%d, 80 frames)\n" % best
          + "=" * 64, flush=True)
    recs = run_drive(h, pd, 0x70, best, 80)
    if recs is not None:
        # count LandingFallSpecial onsets (state transitions into 0x2B)
        cycles, prev = 0, None
        for st, _ in recs:
            if st == S_LANDINGFALLSPECIAL and prev != S_LANDINGFALLSPECIAL:
                cycles += 1
            prev = st
        print(f"  sequence: {fmt(recs)}", flush=True)
        print(f"  >>> wavedash cycles in 80 frames = {cycles}", flush=True)

    neutralize(h, pd)
    print("\n[wm] done. Dolphin left running.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
