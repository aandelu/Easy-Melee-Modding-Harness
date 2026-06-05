"""
online_wavedash_tune.py -- find the TRULY frame-perfect online airdodge frame by
INSTRUMENTING the cave (ground truth, not the lossy Python observer) and sweeping.

Why: the user (a frame-accurate eye) reports the online wavedash is "a frame late --
spends an extra frame in the air." My Python observer samples too slowly to see a
1-frame air state, so it called a late wavedash "tight." This cave counts, per
wavedash, whether the local player went KneeBend(0x18) -> LandingFallSpecial(0x2B)
DIRECTLY (0 air frames = PERFECT) or via an air frame (FLOATY = 1+ frame late). The
producer hook fires once per real frame, so the counts are reliable.

Clean self-drive (no artifact): an UP-LATCH (WD_PEND) -- set when the jump is
injected (grounded + up), fires the airdodge on the latch (not on current up), so
the sim only forces up in GROUNDED states. Forcing up every frame (the prior ship
test) made an up/down/up flip around the airdodge that spuriously double-jumped;
gating the sim to grounded + latching the airdodge removes that.

Sweep: target asfc = jumpsquat - BASE - delay; BASE is a patchable immediate
(addi r8,r8,-BASE). For each BASE we reset the counters, run ~9s, and read
PERFECT/FLOATY/KB. The BASE with the most PERFECT and ~0 FLOATY is the frame-perfect
airdodge frame; the shipped gecko bakes target = jumpsquat - (BASE) - delay.

Two producer hooks (proven): CAVE_B @0x8034E2AC (buttons + instrument + latch),
CAVE_A @0x8034E680 (stick angle). Both resolve LOCAL player via ODB(+0), delay +0x21.

Scratch (debug region, clear of meta-flush control plane 0x440-0x44C and caves):
  0x803FA470 WD_PEND(b)  0x803FA472 PREV_STATE(h)
  0x803FA480 PERFECT(w)  0x803FA484 FLOATY(w)  0x803FA488 KB(w)  0x803FA48C LIVE(w)

Run (peer in an active match; slot 4 baked w/ meta-flush):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_wavedash_tune.py
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
OFF_ACTION_STATE = 0x10

SC = 0x803FA470            # scratch base
WD_PEND = SC + 0x00
PREV_STATE = SC + 0x02
PERFECT = SC + 0x10
FLOATY = SC + 0x14
KB = SC + 0x18
LIVE = SC + 0x1C

ADDI_BASE0 = 0x39080000   # addi r8,r8,0  -> sweep knob (BASE=0)
SIM_STB = 0x99240003      # stb r9,3(r4)  -> sim-up toggle (patch->nop)
NOP = 0x60000000
SWEEP = [3, 2, 1, 0, -1]  # target = jumpsquat - BASE - delay

# ---- CAVE_B @ 0x8034E2AC : buttons + instrument + latch + sim --------------
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
    rlwinm 7, 7, 0, 16, 31   # state

    lis  12, 0x803F
    ori  12, 12, 0xA470       # scratch base
    lwz  8, 0x1C(12)
    addi 8, 8, 1
    stw  8, 0x1C(12)          # LIVE++

    # ---- instrument: PERFECT (KB->LFS direct) vs FLOATY (air->LFS) ----
    lhz  8, 0x02(12)          # PREV_STATE
    cmpwi 7, 0x18
    bne  i_chklfs
    cmpwi 8, 0x18
    beq  i_store
    lwz  9, 0x18(12)
    addi 9, 9, 1
    stw  9, 0x18(12)          # KB++
    b    i_store
i_chklfs:
    cmpwi 7, 0x2B
    bne  i_store
    cmpwi 8, 0x2B
    beq  i_store
    cmpwi 8, 0x18
    bne  i_floaty
    lwz  9, 0x10(12)
    addi 9, 9, 1
    stw  9, 0x10(12)          # PERFECT++
    b    i_store
i_floaty:
    lwz  9, 0x14(12)
    addi 9, 9, 1
    stw  9, 0x14(12)          # FLOATY++
i_store:
    sth  7, 0x02(12)          # PREV_STATE = state

    # ---- injection: latch + target + jump ----
    cmpwi 7, 0x18
    bne  b_notknee
    lbz  8, 0(12)             # WD_PEND
    cmpwi 8, 0
    beq  bdone
    lwz  8, 0x148(6)          # jumpsquat
    cmplwi 8, 0x100
    blt  b_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
b_js_int:
    addi 8, 8, 0            # - BASE (PATCHABLE sweep knob)
    subf 8, 10, 8          # - delay -> target
    lwz  9, 0x894(6)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  bdone
    oris 0, 0, 0x0040       # L (airdodge)
    b    bdone
b_notknee:
    lbz  8, 0(12)            # failsafe: clear stale latch if airborne
    cmpwi 8, 0
    beq  b_chkup
    cmpwi 7, 0x0E
    blt  b_clear
    cmpwi 7, 0x17
    bgt  b_clear
    b    b_chkup            # grounded-actionable -> keep latch
b_clear:
    li   8, 0
    stb  8, 0(12)
    b    bdone
b_chkup:
    cmpwi 7, 0x0E
    blt  bdone
    cmpwi 7, 0x17
    bgt  bdone
    li   9, 0x70           # SIM: force up (toggle stb->nop)
    stb  9, 3(4)
    lbz  9, 3(4)
    extsb 9, 9
    cmpwi 9, 0x40
    blt  bdone
    oris 0, 0, 0x0800       # Y (jump)
    li   8, 1
    stb  8, 0(12)           # WD_PEND = 1
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

# ---- CAVE_A @ 0x8034E680 : stick angle (gated on latch + target) -----------
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
    lbz  10, 0x21(8)        # delay
    lbz  9, 0(8)            # port
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
    lbz  9, 0(9)            # WD_PEND
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
    addi 8, 8, 0           # - BASE (sync with CAVE_B)
    subf 8, 10, 8          # - delay
    lwz  9, 0x894(5)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8
    bne  adone
    lbz  10, 2(4)          # live stickX -> direction
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

ORIS_L = 0x64000040
ORIS_Y = 0x64000800


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    if raw is None:
        raise RuntimeError("keystone empty")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


def addi_word(base):
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


def maj_word(h, addr, n=7):
    vals = [v for v in (rd(h, addr) for _ in range(n)) if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def reset_counters(h):
    # plain dme data writes (counters/latch are DATA -- no icache flush needed)
    for a in (PERFECT, FLOATY, KB, LIVE, WD_PEND):
        h.write_bytes(a, b"\x00\x00\x00\x00")   # WD_PEND word also clears PREV_STATE


def main():
    logic_b = assemble(ASM_B)
    logic_a = assemble(ASM_A)
    pay_b = finalize_payload(logic_b, HOOK_B, CAVE_B, DISP_B)
    pay_a = finalize_payload(logic_a, HOOK_A, CAVE_A, DISP_A)

    assert ORIS_L in pay_b and ORIS_Y in pay_b, "cave B missing L/Y"
    assert pay_a[-2] == DISP_A and pay_b[-2] == DISP_B, "displaced not protected"
    assert pay_b.count(ADDI_BASE0) == 1 and pay_a.count(ADDI_BASE0) == 1, "BASE knob not unique"
    assert pay_b.count(SIM_STB) == 1, "sim toggle not unique"
    ADDI_B = CAVE_B + pay_b.index(ADDI_BASE0) * 4
    ADDI_A = CAVE_A + pay_a.index(ADDI_BASE0) * 4
    SIM_B = CAVE_B + pay_b.index(SIM_STB) * 4

    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    for nm, pay, cave in [("CAVE_B", pay_b, CAVE_B), ("CAVE_A", pay_a, CAVE_A)]:
        print(f"\n=== {nm}: {len(pay)} words ===", flush=True)
        code = b"".join(w.to_bytes(4, "big") for w in pay)
        for i in md.disasm(code, cave):
            print(f"  0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)
    print(f"\n[wt] knobs: BASE A@0x{ADDI_A:08X} B@0x{ADDI_B:08X}  SIM B@0x{SIM_B:08X}",
          flush=True)

    def patch_base(base):
        w = addi_word(base)
        iw.write_instrs(h, ADDI_A, [w])
        iw.write_instrs(h, ADDI_B, [w])

    kill_stale()
    h = Harness()
    print("[wt] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    # Robust entry: re-F4 (reloads the direct-connect savestate fresh) + Enter on
    # EACH attempt, over a ~110s window. Re-F4 catches the peer whenever it's
    # waiting and never drifts into an offline match (blind Enter-only did).
    print("[wt] online entry: re-F4+Enter attempts (get your PC to 'waiting for "
          "opponent') ...", flush=True)
    online = False
    for attempt in range(8):
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
        time.sleep(3.0)
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(10.0)
        top, cc = scene_maj(h)
        print(f"[wt] attempt {attempt}: scene 0x{top:04X} ({cc}/15)"
              + ("  <- IN-GAME" if top == 0x0208 else ""), flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
    if not online:
        print("[wt] never reached online in-game (0x0208); abort. Make sure your PC "
              "is at 'waiting for opponent' during the attempts.", flush=True)
        dme.un_hook(); return 1
    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[wt] meta-flush NOT present; abort", flush=True); dme.un_hook(); return 1

    delay = None
    lport = None
    odb = maj_word(h, ODB_PTR_SLOT)
    if odb and (odb >> 24) == 0x80:
        try:
            delay = h.read_bytes(odb + 0x21, 1)[0]
            lport = h.read_bytes(odb + 0x00, 1)[0]
        except Exception:
            pass
    cid = None
    if lport is not None:
        try:
            cid = h.char_id(lport + 1) & 0xFF
        except Exception:
            pass
    print(f"[wt] delay={delay} local_port_idx={lport} local_char=0x{cid:02X}" if cid is not None
          else f"[wt] delay={delay} local_port_idx={lport}", flush=True)

    iw.write_instrs(h, CAVE_A, pay_a)
    iw.write_instrs(h, CAVE_B, pay_b)
    iw.patch_branch(h, HOOK_A, CAVE_A)
    iw.patch_branch(h, HOOK_B, CAVE_B)
    print(f"[wt] hookA=0x{h.read_word(HOOK_A):08X} hookB=0x{h.read_word(HOOK_B):08X}", flush=True)
    time.sleep(2.0)

    # ---- sweep BASE, read in-cave PERFECT/FLOATY/KB ----
    print("\n" + "=" * 64 + f"\nSWEEP (delay={delay}); target asfc = jumpsquat - BASE - delay\n"
          "  PERFECT = KneeBend->LandingFallSpecial direct (0 air = frame-perfect)\n"
          "  FLOATY  = via an air frame (1+ frame late)\n" + "=" * 64, flush=True)
    results = {}
    for base in SWEEP:
        patch_base(base)
        reset_counters(h)
        time.sleep(9.0)
        p = maj_word(h, PERFECT) or 0
        f = maj_word(h, FLOATY) or 0
        k = maj_word(h, KB) or 0
        live = maj_word(h, LIVE) or 0
        results[base] = (p, f, k)
        tag = "  <<< FRAME-PERFECT" if (p > 0 and f == 0) else ("  (mostly perfect)" if p > f else "")
        print(f"  BASE={base:+d} (target js{base:+d}*-1-delay): PERFECT={p} FLOATY={f} "
              f"jumps={k} live={live}{tag}", flush=True)
        top, _ = scene_maj(h)
        if top != 0x0208:
            print(f"  [wt] left 0x0208 -> 0x{top:04X}; stop", flush=True); break

    print("\n[wt] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[wt] still online: {top == 0x0208} (scene 0x{top:04X})", flush=True)
    # best = max PERFECT with fewest FLOATY (prefer clean)
    def score(b):
        p, f, k = results.get(b, (0, 0, 0))
        return (p - 3 * f, p)
    best = max(results, key=score) if results else 0
    bp, bf, bk = results.get(best, (0, 0, 0))
    if bp > 0:
        patch_base(best)
        reset_counters(h)
        print(f"[wt] best BASE={best:+d}: PERFECT={bp} FLOATY={bf} -> "
              f"target = jumpsquat - {best} - delay. Caves set to it.", flush=True)
        if bf == 0:
            print(f"[wt] [PASS] frame-perfect (0 air frames) at BASE={best:+d}.", flush=True)
        else:
            print(f"[wt] [PARTIAL] best still has {bf} floaty; may be a producer-delay floor.",
                  flush=True)
    else:
        print("[wt] [?] no PERFECT at any BASE -- inspect (latch? digital L? target range?).",
              flush=True)
    print("\n[wt] (running at best BASE now -- WATCH: is it frame-perfect (no air frame) now?)",
          flush=True)
    print("[wt] DONE. Dolphin left running. >>> frame-perfect? any desync? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
