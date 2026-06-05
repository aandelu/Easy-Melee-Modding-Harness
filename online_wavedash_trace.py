"""
online_wavedash_trace.py -- capture the EXACT per-frame action-state sequence of the
self-driven online wavedash, to settle a disagreement: the in-cave PERFECT/FLOATY
counter says BASE+1 (target=jumpsquat-1-delay) is frame-perfect (KneeBend->
LandingFallSpecial direct, 0 air), but the user (frame-accurate eye) still sees "a
frame in the air." A summary counter can structurally miss a 1-frame air state if
the producer hook ever skips a frame; a raw trace cannot.

The cave records the LOCAL player's state into a 32-slot ring each frame (until
full, then frozen). Python resets the ring, lets 32 frames fill (~0.5s), and dumps
the named state sequence. We capture at several BASE values (target = jumpsquat -
BASE - delay) so we can SEE whether any target is truly KneeBend->LandingFallSpecial
with no air state, or whether there's an unavoidable EscapeAir/JumpF (delay floor).

Same proven two-hook cave + up-latch as online_wavedash_tune.py (no double-jump
artifact). Self-drive via sim-up in grounded states.

Run (peer at 'waiting for opponent'; slot 4 baked w/ meta-flush):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_wavedash_trace.py
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

HOOK_A, DISP_A, CAVE_A = 0x8034E680, 0x88030007, 0x803FA600
HOOK_B, DISP_B, CAVE_B = 0x8034E2AC, 0x540084BE, 0x803FA800

R13 = 0x804DB6A0
ODB_PTR_SLOT = R13 - 0x49E4

SC = 0x803FA470
WD_PEND = SC + 0x00
TRACE_IDX = SC + 0x2C
TRACE = SC + 0x30        # 32 halfwords

ADDI_BASE0 = 0x39080000
SIM_STB = 0x99240003
NOP = 0x60000000
CAPTURE = [1, 0, 2]      # BASE values to trace

STATE_NAMES = {
    0x0E: "Wait", 0x0F: "WalkSlow", 0x12: "Turn", 0x14: "Dash", 0x15: "Run",
    0x18: "KneeBend", 0x19: "JumpF", 0x1A: "JumpB", 0x1B: "JumpAerialF",
    0x1C: "JumpAerialB", 0x1D: "Fall", 0x20: "FallAerial", 0x27: "Squat",
    0x2A: "Landing", 0x2B: "LandingFallSpecial", 0xEC: "EscapeAir",
}


def sname(s):
    return STATE_NAMES.get(s, f"0x{s:02X}")


# CAVE_B: resolve, trace-record, latch + target + jump + sim
ASM_B = """
    stwu 1, -0x30(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    stw  10, 0x18(1)
    stw  11, 0x1C(1)
    stw  12, 0x20(1)

    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  bdone
    srwi 11, 8, 24
    cmplwi 11, 0x80
    bne  bdone
    lbz  10, 0x21(8)
    lbz  9, 0(8)
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

    lis  12, 0x803F
    ori  12, 12, 0xA470

    # trace: TRACE[idx]=state until idx>=32
    lwz  8, 0x2C(12)
    cmpwi 8, 32
    bge  t_done
    slwi 9, 8, 1
    addi 9, 9, 0x30
    sthx 7, 12, 9
    addi 8, 8, 1
    stw  8, 0x2C(12)
t_done:

    # injection
    cmpwi 7, 0x18
    bne  b_notknee
    lbz  8, 0(12)
    cmpwi 8, 0
    beq  bdone
    lwz  8, 0x148(6)
    cmplwi 8, 0x100
    blt  b_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
b_js_int:
    addi 8, 8, 0           # - BASE (PATCHABLE)
    subf 8, 10, 8
    lwz  9, 0x894(6)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  bdone
    oris 0, 0, 0x0040       # L
    b    bdone
b_notknee:
    lbz  8, 0(12)
    cmpwi 8, 0
    beq  b_chkup
    cmpwi 7, 0x0E
    blt  b_clear
    cmpwi 7, 0x17
    bgt  b_clear
    b    b_chkup
b_clear:
    li   8, 0
    stb  8, 0(12)
    b    bdone
b_chkup:
    cmpwi 7, 0x0E
    blt  bdone
    cmpwi 7, 0x17
    bgt  bdone
    li   9, 0x70           # SIM up (toggle)
    stb  9, 3(4)
    lbz  9, 3(4)
    extsb 9, 9
    cmpwi 9, 0x40
    blt  bdone
    oris 0, 0, 0x0800       # Y
    li   8, 1
    stb  8, 0(12)
bdone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    lwz  10, 0x18(1)
    lwz  11, 0x1C(1)
    lwz  12, 0x20(1)
    addi 1, 1, 0x30
"""

ASM_A = """
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
    lbz  10, 0x21(8)
    lbz  9, 0(8)
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
    cmpwi 7, 0x18
    bne  adone
    lis  9, 0x803F
    ori  9, 9, 0xA470
    lbz  9, 0(9)
    cmpwi 9, 0
    beq  adone
    lwz  8, 0x148(5)
    cmplwi 8, 0x100
    blt  a_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
a_js_int:
    addi 8, 8, 0
    subf 8, 10, 8
    lwz  9, 0x894(5)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  adone
    lbz  10, 2(4)
    extsb 10, 10
    cmpwi 10, 0x30
    bge  a_right
    cmpwi 10, -0x30
    ble  a_left
    li   8, 0
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
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    addi 1, 1, 0x30
"""


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


def addi_word(base):
    return 0x39080000 | ((-base) & 0xFFFF)


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


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


def scene_maj(h, n=15):
    vals = [mm(v) for v in (rd(h, SCENE_WORD) for _ in range(n)) if v is not None]
    return Counter(vals).most_common(1)[0] if vals else (0, 0)


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def main():
    pay_b = finalize_payload(assemble(ASM_B), HOOK_B, CAVE_B, DISP_B)
    pay_a = finalize_payload(assemble(ASM_A), HOOK_A, CAVE_A, DISP_A)
    assert pay_b.count(ADDI_BASE0) == 1 and pay_a.count(ADDI_BASE0) == 1
    ADDI_B = CAVE_B + pay_b.index(ADDI_BASE0) * 4
    ADDI_A = CAVE_A + pay_a.index(ADDI_BASE0) * 4
    assert pay_a[-2] == DISP_A and pay_b[-2] == DISP_B
    print(f"[tr] cave B {len(pay_b)}w  cave A {len(pay_a)}w  "
          f"BASE A@0x{ADDI_A:08X} B@0x{ADDI_B:08X}", flush=True)

    def patch_base(base):
        w = addi_word(base)
        iw.write_instrs(h, ADDI_A, [w]); iw.write_instrs(h, ADDI_B, [w])

    kill_stale()
    h = Harness()
    print("[tr] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[tr] online entry: re-F4+Enter (get your PC to 'waiting') ...", flush=True)
    online = False
    for attempt in range(8):
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
        time.sleep(3.0)
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(10.0)
        top, cc = scene_maj(h)
        print(f"[tr] attempt {attempt}: scene 0x{top:04X} ({cc}/15)"
              + ("  <- IN-GAME" if top == 0x0208 else ""), flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
    if not online:
        print("[tr] never in-game; abort.", flush=True); dme.un_hook(); return 1
    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[tr] meta-flush absent; abort", flush=True); dme.un_hook(); return 1

    odb = rd(h, ODB_PTR_SLOT)
    delay = None
    if odb and (odb >> 24) == 0x80:
        try:
            delay = h.read_bytes(odb + 0x21, 1)[0]
        except Exception:
            pass
    print(f"[tr] delay={delay}", flush=True)

    iw.write_instrs(h, CAVE_A, pay_a)
    iw.write_instrs(h, CAVE_B, pay_b)
    iw.patch_branch(h, HOOK_A, CAVE_A)
    iw.patch_branch(h, HOOK_B, CAVE_B)
    time.sleep(2.0)

    for base in CAPTURE:
        patch_base(base)
        # capture a few rings so we can find a clean cycle
        print(f"\n=== BASE={base:+d}  (target asfc = jumpsquat {base:+d}*-1 - delay) ===",
              flush=True)
        for shot in range(4):
            if not ensure_hooked(h):
                print(f"  shot{shot}: <dme detached>", flush=True); continue
            try:
                h.write_bytes(TRACE_IDX, b"\x00\x00\x00\x00")   # reset ring
                time.sleep(0.7)                                 # let 32 frames fill
                raw = h.read_bytes(TRACE, 64)
            except Exception:
                print(f"  shot{shot}: <read failed>", flush=True); continue
            states = [struct.unpack(">H", raw[i:i + 2])[0] & 0xFFFF for i in range(0, 64, 2)]
            # collapse consecutive repeats for readability
            seq = []
            for s in states:
                if not seq or seq[-1][0] != s:
                    seq.append([s, 1])
                else:
                    seq[-1][1] += 1
            txt = " ".join(f"{sname(s)}x{n}" if n > 1 else sname(s) for s, n in seq)
            print(f"  shot{shot}: {txt}", flush=True)

    print("\n[tr] === reading: look for KneeBend -> ??? -> LandingFallSpecial ===", flush=True)
    print("[tr]   frame-perfect = KneeBend directly into LandingFallSpecial (no JumpF/", flush=True)
    print("[tr]   FallAerial/EscapeAir between). Any air state between = 1+ frame late.", flush=True)
    top, _ = scene_maj(h)
    print(f"[tr] still online: {top == 0x0208}. Dolphin left running.", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
