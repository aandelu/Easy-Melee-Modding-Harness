"""
offline_wavedash_probe.py -- OFFLINE discovery rig for the wavedash macro.

Same harness pattern as offline_cactuar_probe.py: a self-drive injector at the
OFFLINE consumer pad-read hook 0x803775B8 (pad struct at r25: buttons 0(r25),
stickX 2(r25), stickY 3(r25), analog L 6(r25)). Fox = P2 (port index 1) on slot
2. Establishes the empirical facts the wavedash macro needs, frame-exact, delay=0:

  Q1 (calibration): byte -> processed-float map at this hook. Hold a stick byte,
     read Player Data 0x620 (stickX float) / 0x624 (stickY float). Find the bytes
     that reproduce the user's airdodge angle (+0.95,-0.29), straight-down (0,-1),
     and full up (0,+1).

  Q2 (the wavedash): time-based sequence from Wait -- inject X (jump) on count 0,
     let jumpsquat run, then inject L + diagonal stick starting at count==ADF.
     Sweep ADF to find which post-jump frame lands LandingFallSpecial(0x2B) = the
     wavedash (vs a full jump if the airdodge is too late, or a grounded shield if
     too early). Also measures the wavedash's own landing-lag duration (for the
     repeat timing later).

The gecko sequences purely off a frame COUNT since ENABLE (no asfc float-decode
needed offline) -- Python sets ENABLE=1 from a known Wait, the cave drives the
rest. MODE 0 = hold-stick (calibration); MODE 1 = wavedash sequence.

Offline only: validates the MECHANIC. Netplay-safety + delay-comp are the online
port (different hooks 0x8034E2AC / 0x8034E680).

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_wavedash_probe.py
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
ENABLE_ADDR = 0x803FA460          # byte: 0 = cave passes through, 1 = drive
MODE_ADDR   = 0x803FA461          # 0 = hold-stick (calib), 1 = wavedash sequence
COUNT_ADDR  = 0x803FA462          # byte: frames since enable (cave increments)
ADF_ADDR    = 0x803FA463          # byte: airdodge frame = count at which L+diag starts
DIAGX_ADDR  = 0x803FA464          # signed byte: stickX to inject (diagonal / hold)
DIAGY_ADDR  = 0x803FA465          # signed byte: stickY to inject (diagonal / hold)
FIRE_ADDR   = 0x803FA466          # byte: increments every cave fire (liveness)

TARGET_PORT = 2                   # Fox = P2 in slot 2 (0-indexed port 1)

OFF_ACTION_STATE = 0x10
OFF_FACING       = 0x2C           # float, +1 right / -1 left
OFF_PROC_STICK_X = 0x620          # float
OFF_PROC_STICK_Y = 0x624          # float
OFF_ASFC         = 0x894          # action-state frame counter (float, resets 1.0)

S_WAIT = 0x000E
S_KNEEBEND = 0x0018
S_JUMPF = 0x0019
S_JUMPB = 0x001A
S_FALL = 0x001D
S_FALLAERIAL = 0x0020
S_LANDING = 0x002A
S_LANDINGFALLSPECIAL = 0x002B     # <-- the wavedash
S_ESCAPEAIR = 0x00EC              # airdodge

STATE_NAMES = {
    S_WAIT: "Wait", 0x0F: "WalkSlow", 0x12: "Turn", 0x14: "Dash", 0x15: "Run",
    S_KNEEBEND: "KneeBend", S_JUMPF: "JumpF", S_JUMPB: "JumpB", S_FALL: "Fall",
    0x1F: "FallB", S_FALLAERIAL: "FallAerial", 0x23: "FallSpecial",
    0x27: "Squat", 0x28: "SquatWait", S_LANDING: "Landing",
    S_LANDINGFALLSPECIAL: "LandingFallSpecial(WD)", S_ESCAPEAIR: "EscapeAir(AD)",
}


def sname(st):
    return STATE_NAMES.get(st, f"0x{st:04X}")


# Cave: gated on Fox's port, drives off a frame counter since ENABLE.
#   r24 = 0-indexed port (preserve), r25 = pad struct ptr (preserve).
#   scratch in r9 (addr), r7/r8/r10 scratch values.
CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    stw  10, 0x18(1)

    cmpwi 24, {TARGET_PORT - 1}       # only Fox's port
    bne  end

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
    beq  end

    # MODE
    lis  9, 0x{(MODE_ADDR >> 16):04X}
    ori  9, 9, 0x{(MODE_ADDR & 0xFFFF):04X}
    lbz  7, 0(9)
    cmpwi 7, 0
    bne  wavedash

hold:
    # MODE 0: write DIAGX/DIAGY to the stick every frame (calibration)
    lis  9, 0x{(DIAGX_ADDR >> 16):04X}
    ori  9, 9, 0x{(DIAGX_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    stb  8, 2(25)
    lis  9, 0x{(DIAGY_ADDR >> 16):04X}
    ori  9, 9, 0x{(DIAGY_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    stb  8, 3(25)
    b    end

wavedash:
    # r7 = count, r8 = ADF
    lis  10, 0x{(COUNT_ADDR >> 16):04X}
    ori  10, 10, 0x{(COUNT_ADDR & 0xFFFF):04X}
    lbz  7, 0(10)
    lis  9, 0x{(ADF_ADDR >> 16):04X}
    ori  9, 9, 0x{(ADF_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)

    cmpwi 7, 0
    bne  not_jump

jump:
    # count==0: press X (0x400), neutral stick
    lhz  9, 0(25)
    ori  9, 9, 0x0400
    sth  9, 0(25)
    li   9, 0
    stb  9, 2(25)
    stb  9, 3(25)
    b    incr

not_jump:
    cmpw 7, 8
    blt  jumpsquat

    # count >= ADF: airdodge window [ADF, ADF+3] -> L (0x40) + diagonal stick
    addi 9, 8, 4
    cmpw 7, 9
    bge  incr            # past window: pass through (let it land), just count

    lhz  9, 0(25)
    ori  9, 9, 0x0040
    sth  9, 0(25)
    lis  9, 0x{(DIAGX_ADDR >> 16):04X}
    ori  9, 9, 0x{(DIAGX_ADDR & 0xFFFF):04X}
    lbz  9, 0(9)
    stb  9, 2(25)
    lis  9, 0x{(DIAGY_ADDR >> 16):04X}
    ori  9, 9, 0x{(DIAGY_ADDR & 0xFFFF):04X}
    lbz  9, 0(9)
    stb  9, 3(25)
    b    incr

jumpsquat:
    # count in [1, ADF): neutral stick, no extra buttons (let jumpsquat run)
    li   9, 0
    stb  9, 2(25)
    stb  9, 3(25)

incr:
    addi 7, 7, 1
    stb  7, 0(10)

end:
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    lwz  10, 0x18(1)
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


def stream(h, pd, n_frames):
    """Per-frame (action_state, asfc) for n_frames."""
    out = []
    for _ in range(n_frames):
        st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
        af = rf(h, pd, OFF_ASFC)
        out.append((st, af))
        h.wait_frames(1)
    return out


def fmt_stream(recs):
    parts, prev, count = [], None, 0
    for st, _ in recs:
        if st == prev:
            count += 1
        else:
            if prev is not None:
                parts.append(f"{sname(prev)}x{count}")
            prev, count = st, 1
    if prev is not None:
        parts.append(f"{sname(prev)}x{count}")
    return " -> ".join(parts)


def sbyte(v):
    return bytes([v & 0xFF])


def neutralize(h, pd):
    h.write_bytes(ENABLE_ADDR, b"\x00")
    h.write_bytes(COUNT_ADDR, b"\x00")
    wait_for_state(h, pd, S_WAIT, timeout_frames=180)
    h.wait_frames(3)


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED)
    print(f"[wp] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[wp] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[wp] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[wp] target port invalid -- abort", flush=True)
        return 1
    print(f"[wp] P{TARGET_PORT} pd=0x{pd:08X} state={sname(state(h, pd))}", flush=True)

    # defaults
    h.write_bytes(ENABLE_ADDR, b"\x00")
    h.write_bytes(MODE_ADDR, b"\x00")
    h.write_bytes(COUNT_ADDR, b"\x00")
    h.write_bytes(ADF_ADDR, bytes([3]))
    h.write_bytes(DIAGX_ADDR, b"\x00")
    h.write_bytes(DIAGY_ADDR, b"\x00")
    h.write_bytes(FIRE_ADDR, b"\x00")

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    print(f"[wp] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(FIRE_ADDR, 1)[0]
    print(f"[wp] hook fires: {fires} (must be > 0)", flush=True)
    if fires == 0:
        print("[wp] cave not firing -- abort", flush=True)
        return 1

    # =================================================================
    # Q1: byte -> processed-float calibration (MODE 0 hold)
    # =================================================================
    print("\n" + "=" * 64 + "\nQ1: stick byte -> processed float (hold)\n" + "=" * 64,
          flush=True)
    h.write_bytes(MODE_ADDR, b"\x00")
    # candidate bytes to characterize full/diagonal magnitudes
    for label, xb, yb in [
        ("full up      ", 0x00, 0x70),
        ("full down     ", 0x00, 0x90),   # -0x70
        ("full down(80) ", 0x00, 0x80),   # -0x80
        ("airdodge R    ", 0x6A, 0xE0),   # +0.95ish, -0.29ish  (0xE0 = -0x20)
        ("airdodge R(7F)", 0x7F, 0xE0),
        ("right         ", 0x70, 0x00),
    ]:
        neutralize(h, pd)
        h.write_bytes(DIAGX_ADDR, sbyte(xb))
        h.write_bytes(DIAGY_ADDR, sbyte(yb))
        h.write_bytes(COUNT_ADDR, b"\x00")
        h.write_bytes(ENABLE_ADDR, b"\x01")
        h.wait_frames(4)
        sx = rf(h, pd, OFF_PROC_STICK_X)
        sy = rf(h, pd, OFF_PROC_STICK_Y)
        print(f"  {label} Xb=0x{xb:02X} Yb=0x{yb:02X} -> procX={sx:+.3f} "
              f"procY={sy:+.3f}  state={sname(state(h, pd))}", flush=True)
        h.write_bytes(ENABLE_ADDR, b"\x00")
    neutralize(h, pd)

    # =================================================================
    # Q2: the wavedash -- sweep the airdodge frame (ADF)
    # =================================================================
    print("\n" + "=" * 64 + "\nQ2: wavedash sequence -- sweep airdodge frame\n"
          + "=" * 64, flush=True)
    print("  (X jump on count 0; L+diagonal on count>=ADF. Looking for "
          "LandingFallSpecial(WD).)", flush=True)
    # use the airdodge-R bytes that calibration says are right (default guess)
    AD_X, AD_Y = 0x6A, 0xE0
    h.write_bytes(MODE_ADDR, b"\x01")
    h.write_bytes(DIAGX_ADDR, sbyte(AD_X))
    h.write_bytes(DIAGY_ADDR, sbyte(AD_Y))
    for adf in (2, 3, 4, 5):
        neutralize(h, pd)
        if state(h, pd) != S_WAIT:
            print(f"  ADF={adf}: not in Wait (state={sname(state(h, pd))}), skip",
                  flush=True)
            continue
        h.write_bytes(ADF_ADDR, bytes([adf]))
        h.write_bytes(COUNT_ADDR, b"\x00")
        h.write_bytes(ENABLE_ADDR, b"\x01")
        recs = stream(h, pd, 40)
        h.write_bytes(ENABLE_ADDR, b"\x00")
        seq = fmt_stream(recs)
        got_wd = any(st == S_LANDINGFALLSPECIAL for st, _ in recs)
        got_ad = any(st == S_ESCAPEAIR for st, _ in recs)
        # measure WD landing-lag length: frames in LandingFallSpecial
        wd_frames = sum(1 for st, _ in recs if st == S_LANDINGFALLSPECIAL)
        tag = "  <<< WAVEDASH" if got_wd else (" (airdodge, no WD land)" if got_ad else "")
        print(f"\n  ADF={adf}: {seq}{tag}", flush=True)
        print(f"      EscapeAir seen={got_ad}  LandingFallSpecial seen={got_wd}"
              f"  WD-landing-lag frames={wd_frames}", flush=True)

    neutralize(h, pd)
    print("\n[wp] discovery complete. Leaving Dolphin running.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
