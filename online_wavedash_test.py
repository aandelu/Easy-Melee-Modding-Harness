"""
online_wavedash_test.py -- ONLINE (netplay) bring-up + airdodge-frame sweep for the
wavedash macro. SELF-DRIVES the LOCAL player (no human at the harness) so we can
validate, on a live match vs the user's 2nd machine:
  1. producer-side injection (stick @ 0x8034E680 + digital buttons @ 0x8034E2AC)
     transfers and does NOT desync (the user watches their peer screen),
  2. the wavedash actually comes out (state -> LandingFallSpecial 0x2B), and
  3. the correct airdodge target frame for THIS connection (delay-comp), found by
     sweeping BASE in target = jumpsquat - BASE - delay.

TWO producer-side caves (each an individually-proven path; ONLINE_MACRO_GUIDE):
  * CAVE_B @ 0x8034E2AC (digital buttons via `oris r0,r0,BIT`; displaced
    rlwinm 0x540084BE). Preserves r0(working)/r4/r5/r13.
      - grounded-actionable (0x0E..0x17) -> oris Y (0x800)   [self-drive jump; loops]
      - KneeBend(0x18) AND asfc==target  -> oris L (0x40)     [airdodge]
  * CAVE_A @ 0x8034E680 (stick bytes 2/3(r4) after calibration; displaced
    lbz r0,7(r3) 0x88030007). Preserves r3/r4/r13.
      - KneeBend(0x18) AND asfc==target  -> override stick to airdodge angle
        (right=(0x6A,0xE0)/left=(0x96,0xE0)/down=(0,0x90) from LIVE stickX 2(r4)).

Both caves resolve the LOCAL player via the ODB (port = *(*(r13-0x49E4)+0)) and
read the connection's input delay (ODB+0x21). target = jumpsquat(0x148) - BASE -
delay; jumpsquat & asfc(0x894) are floats decoded to int without FPU. BASE is a
patchable immediate (the `addi r8,r8,-1`) we sweep to find the tight wavedash.

Self-drive needs NO fake stick: Y(jump) is a button, released during KneeBend/air/
landing, so each return to Wait is a fresh rising edge -> the loop jump-wavedashes.
A wrong target -> the player just JUMPS (no 0x2B); the right target -> 0x2B appears.

Online rules honored: one dme process; F4/Enter entry; meta-flush baked in slot 4;
caves at 0x803FA600 / 0x803FA800 (clear of the meta-flush control plane); throttled,
majority-vote, re-hook-on-fail reads; code-only A/B (no rollback-fragile data flags).

Run (peer must be in an ACTIVE in-game match; slot 4 baked with meta-flush):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_wavedash_test.py
"""
import struct
import subprocess
import sys
import time
from collections import Counter

import capstone
import keystone
import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness, finalize_payload
import instr_writer as iw

VK_F4, VK_RETURN = 118, 36
SCENE_WORD = 0x80479D30
FRAME = 0x80479D60

HOOK_A = 0x8034E680            # stick (post-calibration)
DISP_A = 0x88030007           # lbz r0, 7(r3)
CAVE_A = 0x803FA600

HOOK_B = 0x8034E2AC           # digital buttons (Altimor slot)
DISP_B = 0x540084BE           # rlwinm r0,r0,0x10,0x12,0x1f
CAVE_B = 0x803FA800

R13 = 0x804DB6A0
ODB_PTR_SLOT = R13 - 0x49E4   # 0x804D6CBC
OFF_ACTION_STATE = 0x10

S_WAIT = 0x000E
S_KNEEBEND = 0x0018
S_JUMPF = 0x0019
S_FALL = 0x001D
S_FALLAERIAL = 0x0020
S_LANDING = 0x002A
S_LANDINGFALLSPECIAL = 0x002B   # wavedash output
S_ESCAPEAIR = 0x00EC            # airdodge (in-air = late wavedash)

ADDI_BASE1 = 0x3908FFFF         # addi r8,r8,-1  (BASE=1; the sweep knob)

# ---- the two caves -------------------------------------------------------

CAVE_ASM_B = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    stw  10, 0x18(1)
    stw  11, 0x1C(1)

    lwz  8, -0x49E4(13)        # ODB ptr
    cmpwi 8, 0
    beq  bdone
    srwi 11, 8, 24
    cmplwi 11, 0x80
    bne  bdone
    lbz  10, 0x21(8)          # ODB_DELAY_FRAMES
    lbz  9, 0(8)              # ODB_LOCAL_PLAYER_INDEX
    mulli 9, 9, 0xE90
    lis  6, 0x8045
    ori  6, 6, 0x3130
    add  6, 6, 9
    lwz  6, 0(6)             # local GObj
    cmpwi 6, 0
    beq  bdone
    srwi 11, 6, 24
    cmplwi 11, 0x80
    bne  bdone
    lwz  6, 0x2C(6)          # local Player Data
    cmpwi 6, 0
    beq  bdone
    srwi 11, 6, 24
    cmplwi 11, 0x80
    bne  bdone
    lwz  7, 0x10(6)
    rlwinm 7, 7, 0, 16, 31    # action state

    cmpwi 7, 0x18             # KneeBend?
    bne  b_chk_jump
    lwz  8, 0x148(6)          # jumpsquat (int or float)
    cmplwi 8, 0x100
    blt  b_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
b_js_int:
    addi 8, 8, -1            # - BASE  (PATCHABLE sweep knob)
    subf 8, 10, 8            # - delay  -> r8 = target asfc
    lwz  9, 0x894(6)         # asfc (float)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8                # asfc == target ?
    bne  bdone
    oris 0, 0, 0x0040        # digital L (airdodge)
    b    bdone

b_chk_jump:
    cmpwi 7, 0x0E            # grounded-actionable (Wait..RunBrake)?
    blt  bdone
    cmpwi 7, 0x17
    bgt  bdone
    oris 0, 0, 0x0800        # Y (jump) -- self-drive; released off-ground -> loops

bdone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    lwz  10, 0x18(1)
    lwz  11, 0x1C(1)
    addi 1, 1, 0x20
"""

CAVE_ASM_A = """
    stwu 1, -0x30(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)
    stw  11, 0x20(1)

    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  adone
    srwi 11, 8, 24
    cmplwi 11, 0x80
    bne  adone
    lbz  10, 0x21(8)         # delay
    lbz  9, 0(8)             # local port
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)            # GObj
    cmpwi 5, 0
    beq  adone
    srwi 11, 5, 24
    cmplwi 11, 0x80
    bne  adone
    lwz  5, 0x2C(5)         # Player Data
    cmpwi 5, 0
    beq  adone
    srwi 11, 5, 24
    cmplwi 11, 0x80
    bne  adone
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x18           # only override during KneeBend
    bne  adone
    lwz  8, 0x148(5)
    cmplwi 8, 0x100
    blt  a_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
a_js_int:
    addi 8, 8, -1           # - BASE  (PATCHABLE; kept in sync with CAVE_B)
    subf 8, 10, 8           # - delay
    lwz  9, 0x894(5)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  adone
    lbz  10, 2(4)           # LIVE stickX -> direction
    extsb 10, 10
    cmpwi 10, 0x30
    bge  a_right
    cmpwi 10, -0x30
    ble  a_left
    li   8, 0               # down: (0, -0x70)
    li   7, -112
    b    a_set
a_right:
    li   8, 0x6A            # right: (+0x6A, -0x20)
    li   7, -32
    b    a_set
a_left:
    li   8, -0x6A           # left: (-0x6A, -0x20)
    li   7, -32
a_set:
    stb  8, 2(4)
    stb  7, 3(4)
adone:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    addi 1, 1, 0x30
"""

ORIS_L = 0x64000040            # oris r0,r0,0x40
ORIS_Y = 0x64000800           # oris r0,r0,0x800
SWEEP = [2, 1, 0, -1]         # BASE values to try -> target = js - BASE - delay


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


def addi_word(base):
    """addi r8,r8,-base  (the sweep knob)."""
    return 0x39080000 | ((-base) & 0xFFFF)


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def scene_maj(h, n=15):
    return Counter(mm(h.read_word(SCENE_WORD)) for _ in range(n)).most_common(1)[0]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def ensure_hooked(h):
    if dme.is_hooked():
        return True
    for _ in range(20):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.2)
    return False


def rd(h, addr):
    try:
        return h.read_word(addr)
    except Exception:
        if ensure_hooked(h):
            try:
                return h.read_word(addr)
            except Exception:
                return None
        return None


def maj_word(h, addr, n=5):
    vals = [v for v in (rd(h, addr) for _ in range(n)) if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def find_local_port(h, seconds=7):
    """The self-drive loops the LOCAL player; find it by KneeBend activity."""
    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    kb = {1: 0, 2: 0}
    prev = {1: None, 2: None}
    t_end = time.time() + seconds
    while time.time() < t_end:
        for p in (1, 2):
            if pds[p] == -1:
                continue
            st = rd(h, pds[p] + OFF_ACTION_STATE)
            if st is None:
                continue
            st &= 0xFFFF
            if st != prev[p] and st == S_KNEEBEND:
                kb[p] += 1
            prev[p] = st
        time.sleep(0.012)
    p = max(kb, key=kb.get)
    return (p, pds[p], kb) if kb[p] > 0 else (None, None, kb)


def measure(h, pd, seconds):
    """Count KneeBend (jump) & LandingFallSpecial (wavedash) onsets, note EscapeAir
    (in-air airdodge = late) and all states seen, on the local player."""
    jumps = wds = 0
    prev = None
    seen = set()
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = rd(h, pd + OFF_ACTION_STATE)
        if st is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        seen.add(st)
        if st != prev:
            if st == S_KNEEBEND:
                jumps += 1
            elif st == S_LANDINGFALLSPECIAL:
                wds += 1
            prev = st
        time.sleep(0.012)
    return jumps, wds, seen


def patch_base(h, base):
    w = addi_word(base)
    iw.write_instrs(h, ADDI_A_ADDR, [w])
    iw.write_instrs(h, ADDI_B_ADDR, [w])


ADDI_A_ADDR = ADDI_B_ADDR = 0   # set in main()


def main():
    global ADDI_A_ADDR, ADDI_B_ADDR
    logic_a = assemble(CAVE_ASM_A)
    logic_b = assemble(CAVE_ASM_B)
    pay_a = finalize_payload(logic_a, HOOK_A, CAVE_A, DISP_A)
    pay_b = finalize_payload(logic_b, HOOK_B, CAVE_B, DISP_B)

    # locate the sweep knob (addi r8,r8,-1) in each cave -- must be unique
    assert pay_a.count(ADDI_BASE1) == 1, f"cave A: addi knob count={pay_a.count(ADDI_BASE1)}"
    assert pay_b.count(ADDI_BASE1) == 1, f"cave B: addi knob count={pay_b.count(ADDI_BASE1)}"
    ADDI_A_ADDR = CAVE_A + pay_a.index(ADDI_BASE1) * 4
    ADDI_B_ADDR = CAVE_B + pay_b.index(ADDI_BASE1) * 4

    # sanity: required injectors present, displaced protected
    assert ORIS_L in pay_b and ORIS_Y in pay_b, "cave B missing oris L / oris Y"
    assert pay_a[-2] == DISP_A, "cave A displaced must precede branch-back"
    assert pay_b[-2] == DISP_B, "cave B displaced must precede branch-back"

    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    for name, pay, cave in [("CAVE_B @0x8034E2AC (buttons)", pay_b, CAVE_B),
                            ("CAVE_A @0x8034E680 (stick)", pay_a, CAVE_A)]:
        print(f"\n=== {name}: {len(pay)} words ===", flush=True)
        code = b"".join(w.to_bytes(4, "big") for w in pay)
        for i in md.disasm(code, cave):
            print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)
    print(f"\n[wd] sweep knob: A@0x{ADDI_A_ADDR:08X} B@0x{ADDI_B_ADDR:08X}  "
          f"(target = jumpsquat - BASE - delay)", flush=True)

    kill_stale()
    h = Harness()
    print("[wd] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[wd] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(12.0)
    online = False
    for i in range(12):
        top, cc = scene_maj(h)
        print(f"[wd] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[wd] online CSS; Enter, waiting (peer must be in a match) ...", flush=True)
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(8.0); continue
        if i % 3 == 2:
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)
    if not online:
        print("[wd] not in-game; abort. (2nd machine must be in an active match.)", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[wd] meta-flush NOT present in slot 4; abort (re-bake needed)", flush=True)
        dme.un_hook(); return 1
    print("[wd] meta-flush present.", flush=True)

    # connection delay (cave reads this at runtime; we print for the record)
    delay = None
    odb = maj_word(h, ODB_PTR_SLOT)
    if odb and (odb >> 24) == 0x80:
        try:
            delay = h.read_bytes(odb + 0x21, 1)[0]
        except Exception:
            pass
    print(f"[wd] ODB ptr=0x{odb:08X} ODB_DELAY_FRAMES={delay}" if odb else
          "[wd] ODB ptr unreadable", flush=True)

    # install both caves + hook branches
    iw.write_instrs(h, CAVE_A, pay_a)
    iw.write_instrs(h, CAVE_B, pay_b)
    iw.patch_branch(h, HOOK_A, CAVE_A)
    iw.patch_branch(h, HOOK_B, CAVE_B)
    print(f"[wd] hookA 0x{HOOK_A:08X}=0x{h.read_word(HOOK_A):08X}  "
          f"hookB 0x{HOOK_B:08X}=0x{h.read_word(HOOK_B):08X}", flush=True)
    patch_base(h, 0)            # start at a likely-good target (js - delay)
    time.sleep(2.0)

    print("\n[wd] finding local port (self-drive loops it via KneeBend) ...", flush=True)
    port, pd, kb = find_local_port(h, 7)
    print(f"   KneeBend onsets: P1={kb[1]} P2={kb[2]}", flush=True)
    if port is None:
        print("[wd] no port shows jumps -- self-drive not looping; inspect "
              "(ODB local-port? caves firing?). abort", flush=True)
        dme.un_hook(); return 1
    print(f"[wd] LOCAL port = P{port}, pd=0x{pd:08X}", flush=True)

    # ---- sweep BASE to find the tight wavedash --------------------------
    print("\n" + "=" * 64 + f"\nAIRDODGE-FRAME SWEEP (delay={delay}); BASE -> "
          f"target=js-BASE-delay\n" + "=" * 64, flush=True)
    results = {}
    for base in SWEEP:
        patch_base(h, base)
        time.sleep(1.0)
        jumps, wds, seen = measure(h, pd, 7)
        air = sorted(s for s in (S_JUMPF, S_FALL, S_FALLAERIAL, S_ESCAPEAIR) if s in seen)
        results[base] = (jumps, wds, air, seen)
        ad = " +ESCAPEAIR(late)" if S_ESCAPEAIR in seen else ""
        wdtag = "  <<< WAVEDASH" if wds > 0 else ""
        print(f"  BASE={base:+d} (target js{'-' if base>=0 else '+'}{abs(base)}-delay): "
              f"jumps={jumps} wavedashes(0x2B)={wds}{wdtag}{ad}", flush=True)
        print(f"      states={[hex(s) for s in sorted(seen)]}", flush=True)
        top, cc = scene_maj(h)
        if top != 0x0208:
            print(f"  [wd] scene left 0x0208 -> 0x{top:04X}; stopping sweep", flush=True)
            break

    print("\n[wd] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[wd] still online: {top == 0x0208} (scene 0x{top:04X}, {cc}/15)", flush=True)
    # pick best: most wavedashes, tie-break fewer escapeairs (no air = tighter)
    best = max(SWEEP, key=lambda b: (results.get(b, (0, 0, [], set()))[1],
                                     -(1 if S_ESCAPEAIR in results.get(b, (0,0,[],set()))[3] else 0)))
    bj, bw, bair, bseen = results.get(best, (0, 0, [], set()))
    if bw > 0:
        patch_base(h, best)
        print(f"[wd] [PASS] BASE={best:+d} wavedashed {bw}x "
              f"(target = jumpsquat - {best} - delay); caves left set to it.", flush=True)
        print(f"[wd]   -> for the shipped gecko use: target = jumpsquat - {best} - ODB_DELAY",
              flush=True)
    else:
        print("[wd] [?] no BASE produced LandingFallSpecial. The player jumped but "
              "didn't wavedash -- airdodge frame off, or digital L not registering "
              "at 0x8034E2AC. Inspect states above (EscapeAir => airdodging but late; "
              "no air states => L not firing).", flush=True)
    print("\n[wd] DONE. Dolphin left running. >>> Watch YOUR screen: is the harness "
          "character jump-wavedashing, and is there ANY desync/teleport/freeze? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
