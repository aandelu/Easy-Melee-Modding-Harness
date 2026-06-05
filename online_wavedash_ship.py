"""
online_wavedash_ship.py -- validate the SHIPPABLE wavedash cave ONLINE, then it's
ready to package as a gecko for the user's machine.

Difference from online_wavedash_test.py (the bring-up sweep): this installs the
REAL shippable logic -- an UP-GATE (only wavedash when stickY is held up), Y-jump
injection for the first jump + repeat, a live-direction airdodge, and the TIGHT
airdodge frame found by the sweep: target asfc = jumpsquat - delay (BASE=0; the
no-air-float wavedash; the looser BASE+1 spends a frame airborne).

Since there's no human at the harness, a tiny SIM preamble forces stickY=up so the
up-gated path self-drives. The CORE logic below the preamble is EXACTLY what ships
(the gecko maker assembles the same CORE without the preamble). The SIM force is a
single `stb` per hook, patchable to nop -- so within this one session we can:
  PHASE 1  SIM up ON  -> confirm tight wavedash (LandingFallSpecial, NO EscapeAir),
                          repeat, no desync.
  PHASE 2  SIM up OFF -> confirm it goes QUIET (up-gate negative; no wavedash).

Two producer-side hooks (each a proven path; ONLINE_MACRO_GUIDE):
  CAVE_B @ 0x8034E2AC -- digital buttons (oris Y / oris L); displaced 0x540084BE.
  CAVE_A @ 0x8034E680 -- stick angle (post-calibration); displaced 0x88030007.
Both resolve the LOCAL player via the ODB (+0) and read input delay (ODB+0x21).

Run (peer in an ACTIVE in-game match; slot 4 baked with meta-flush):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_wavedash_ship.py
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

HOOK_A = 0x8034E680
DISP_A = 0x88030007
CAVE_A = 0x803FA600

HOOK_B = 0x8034E2AC
DISP_B = 0x540084BE
CAVE_B = 0x803FA800

R13 = 0x804DB6A0
ODB_PTR_SLOT = R13 - 0x49E4
OFF_ACTION_STATE = 0x10

S_KNEEBEND = 0x0018
S_JUMPF = 0x0019
S_FALL = 0x001D
S_FALLAERIAL = 0x0020
S_LANDING = 0x002A
S_LANDINGFALLSPECIAL = 0x002B
S_ESCAPEAIR = 0x00EC

UPTHRESH = 0x40

# SIM preamble (TEST only) -- force stickY=up so the up-gate self-drives.
#   li r6, 0x70 ; stb r6, 3(r4)
# The `stb r6,3(r4)` (0x98C40003) is the toggle: patch -> nop to drop the sim.
SIM_PRE = """
    li   6, 0x70
    stb  6, 3(4)
"""
SIM_STB = 0x98C40003          # stb r6, 3(r4)  -- the sim toggle (patch->nop)
NOP = 0x60000000

# ---- CORE_B: digital buttons at 0x8034E2AC (preserve r0/r4/r5/r13) --------
SAVE_B = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    stw  10, 0x18(1)
    stw  11, 0x1C(1)
"""
CORE_B = """
    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  bdone
    srwi 11, 8, 24
    cmplwi 11, 0x80
    bne  bdone
    lbz  10, 0x21(8)         # delay
    lbz  9, 0(8)             # local port
    mulli 9, 9, 0xE90
    lis  6, 0x8045
    ori  6, 6, 0x3130
    add  6, 6, 9
    lwz  6, 0(6)
    cmpwi 6, 0
    beq  bdone
    srwi 11, 6, 24
    cmplwi 11, 0x80
    bne  bdone
    lwz  6, 0x2C(6)
    cmpwi 6, 0
    beq  bdone
    srwi 11, 6, 24
    cmplwi 11, 0x80
    bne  bdone
    lwz  7, 0x10(6)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x18            # KneeBend?
    bne  b_chk_jump
    lwz  8, 0x148(6)         # jumpsquat
    cmplwi 8, 0x100
    blt  b_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
b_js_int:
    subf 8, 10, 8           # target = jumpsquat - delay
    lwz  9, 0x894(6)        # asfc
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  bdone
    lbz  9, 3(4)            # up held?
    extsb 9, 9
    cmpwi 9, 0x40
    blt  bdone
    oris 0, 0, 0x0040       # L (airdodge)
    b    bdone
b_chk_jump:
    cmpwi 7, 0x0E           # grounded-actionable?
    blt  bdone
    cmpwi 7, 0x17
    bgt  bdone
    lbz  9, 3(4)            # up held?
    extsb 9, 9
    cmpwi 9, 0x40
    blt  bdone
    oris 0, 0, 0x0800       # Y (jump) -- first jump + repeat
bdone:
"""
REST_B = """
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    lwz  10, 0x18(1)
    lwz  11, 0x1C(1)
    addi 1, 1, 0x20
"""

# ---- CORE_A: stick at 0x8034E680 (preserve r3/r4/r13) --------------------
SAVE_A = """
    stwu 1, -0x30(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)
    stw  11, 0x20(1)
"""
CORE_A = """
    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  adone
    srwi 11, 8, 24
    cmplwi 11, 0x80
    bne  adone
    lbz  10, 0x21(8)        # delay
    lbz  9, 0(8)            # local port
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)
    cmpwi 5, 0
    beq  adone
    srwi 11, 5, 24
    cmplwi 11, 0x80
    bne  adone
    lwz  5, 0x2C(5)
    cmpwi 5, 0
    beq  adone
    srwi 11, 5, 24
    cmplwi 11, 0x80
    bne  adone
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x18           # KneeBend only
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
    subf 8, 10, 8           # target = jumpsquat - delay
    lwz  9, 0x894(5)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  adone
    lbz  9, 3(4)            # up held?
    extsb 9, 9
    cmpwi 9, 0x40
    blt  adone
    lbz  10, 2(4)           # LIVE stickX -> direction
    extsb 10, 10
    cmpwi 10, 0x30
    bge  a_right
    cmpwi 10, -0x30
    ble  a_left
    li   8, 0               # down
    li   7, -112
    b    a_set
a_right:
    li   8, 0x6A
    li   7, -32
    b    a_set
a_left:
    li   8, -0x6A
    li   7, -32
a_set:
    stb  8, 2(4)
    stb  7, 3(4)
adone:
"""
REST_A = """
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    addi 1, 1, 0x30
"""

ORIS_L = 0x64000040
ORIS_Y = 0x64000800


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


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


def build(with_sim):
    pre = SIM_PRE if with_sim else ""
    logic_b = assemble(SAVE_B + pre + CORE_B + REST_B)
    logic_a = assemble(SAVE_A + pre + CORE_A + REST_A)
    pay_b = finalize_payload(logic_b, HOOK_B, CAVE_B, DISP_B)
    pay_a = finalize_payload(logic_a, HOOK_A, CAVE_A, DISP_A)
    return pay_a, pay_b


def main():
    pay_a, pay_b = build(with_sim=True)
    # sanity
    assert ORIS_L in pay_b and ORIS_Y in pay_b, "cave B missing oris L/Y"
    assert pay_a[-2] == DISP_A and pay_b[-2] == DISP_B, "displaced not protected"
    assert pay_b.count(SIM_STB) == 1 and pay_a.count(SIM_STB) == 1, "sim toggle not unique"
    SIM_B_ADDR = CAVE_B + pay_b.index(SIM_STB) * 4
    SIM_A_ADDR = CAVE_A + pay_a.index(SIM_STB) * 4

    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    for nm, pay, cave in [("CAVE_B buttons @E2AC", pay_b, CAVE_B),
                          ("CAVE_A stick @E680", pay_a, CAVE_A)]:
        print(f"\n=== {nm}: {len(pay)} words ===", flush=True)
        code = b"".join(w.to_bytes(4, "big") for w in pay)
        for i in md.disasm(code, cave):
            print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)
    print(f"\n[ws] sim toggle: A@0x{SIM_A_ADDR:08X} B@0x{SIM_B_ADDR:08X}", flush=True)

    kill_stale()
    h = Harness()
    print("[ws] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[ws] online entry: F4,+3s,Enter,+12s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(12.0)
    online = False
    for i in range(14):
        top, cc = scene_maj(h)
        print(f"[ws] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[ws] online CSS; Enter, waiting (peer must be in a match) ...", flush=True)
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(8.0); continue
        if i % 3 == 2:
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)
    if not online:
        print("[ws] not in-game; abort. (2nd machine must be in an active match.)", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[ws] meta-flush NOT present in slot 4; abort", flush=True)
        dme.un_hook(); return 1

    delay = None
    odb = maj_word(h, ODB_PTR_SLOT)
    if odb and (odb >> 24) == 0x80:
        try:
            delay = h.read_bytes(odb + 0x21, 1)[0]
        except Exception:
            pass
    print(f"[ws] meta-flush present; ODB=0x{odb:08X} delay={delay} "
          f"-> target asfc = jumpsquat - {delay}", flush=True)

    iw.write_instrs(h, CAVE_A, pay_a)
    iw.write_instrs(h, CAVE_B, pay_b)
    iw.patch_branch(h, HOOK_A, CAVE_A)
    iw.patch_branch(h, HOOK_B, CAVE_B)
    print(f"[ws] hookA=0x{h.read_word(HOOK_A):08X} hookB=0x{h.read_word(HOOK_B):08X}",
          flush=True)
    time.sleep(2.0)

    print("\n[ws] finding local port ...", flush=True)
    port, pd, kb = find_local_port(h, 7)
    print(f"   KneeBend onsets: P1={kb[1]} P2={kb[2]}", flush=True)
    if port is None:
        print("[ws] no port jumping -- inspect (up-gate? sim? ODB?). abort", flush=True)
        dme.un_hook(); return 1
    print(f"[ws] LOCAL port = P{port}, pd=0x{pd:08X}", flush=True)

    # PHASE 1: SIM up ON -> tight wavedash
    print("\n" + "=" * 64 + "\nPHASE 1: SIM up ON -> expect TIGHT wavedash "
          "(LandingFallSpecial, NO EscapeAir)\n" + "=" * 64, flush=True)
    j1, w1, s1 = measure(h, pd, 10)
    air1 = sorted(s for s in (S_JUMPF, S_FALL, S_FALLAERIAL, S_ESCAPEAIR) if s in s1)
    print(f"[ws] jumps={j1} wavedashes(0x2B)={w1}  air-states={[hex(s) for s in air1]}",
          flush=True)
    print(f"[ws] all states={[hex(s) for s in sorted(s1)]}", flush=True)
    tight = (w1 > 0 and S_ESCAPEAIR not in s1)
    print(f"[ws] -> {'TIGHT (no airborne frame)' if tight else 'wavedashes but check air-states'}",
          flush=True)

    # PHASE 2: SIM up OFF -> up-gate negative
    print("\n" + "=" * 64 + "\nPHASE 2: SIM up OFF -> expect QUIET (up-gate gates it off)\n"
          + "=" * 64, flush=True)
    iw.write_instrs(h, SIM_A_ADDR, [NOP])
    iw.write_instrs(h, SIM_B_ADDR, [NOP])
    time.sleep(1.0)
    j2, w2, s2 = measure(h, pd, 6)
    print(f"[ws] jumps={j2} wavedashes(0x2B)={w2}  states={[hex(s) for s in sorted(s2)]}",
          flush=True)
    gate_ok = (w2 == 0 and j2 == 0)
    print(f"[ws] -> up-gate {'WORKS (quiet with up released)' if gate_ok else 'LEAKED (still active)'}",
          flush=True)

    print("\n[ws] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[ws] still online: {top == 0x0208} (scene 0x{top:04X})", flush=True)
    if tight and gate_ok:
        print("[ws] [PASS] shippable logic validated online: tight wavedash on up, "
              "quiet without up, no desync. Ready to package the gecko.", flush=True)
    elif w1 > 0:
        print("[ws] [PARTIAL] wavedashes but: "
              + ("air-float present; " if S_ESCAPEAIR in s1 else "")
              + ("up-gate leaked; " if not gate_ok else "") + "inspect.", flush=True)
    else:
        print("[ws] [?] no wavedash in phase 1 -- inspect (timing / digital L / up-check).",
              flush=True)
    # re-enable sim so the user can keep watching the tight wavedash
    iw.write_instrs(h, SIM_A_ADDR, [SIM_STB])
    iw.write_instrs(h, SIM_B_ADDR, [SIM_STB])
    print("\n[ws] (sim re-enabled; harness will keep wavedashing for you to watch)", flush=True)
    print("[ws] DONE. Dolphin left running. >>> frame-perfect now? any desync? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
