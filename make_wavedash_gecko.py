"""
make_wavedash_gecko.py -- generate the SHIPPABLE up-bound wavedash gecko (online,
netplay-safe). Bind: hold UP on the left stick while grounded -> the macro jumps
and, on the frame-perfect airdodge frame, airdodges into the ground (= wavedash,
LandingFallSpecial). Direction from the horizontal you hold (up-only = straight
down). Holding up repeats it. Character-agnostic (jumpsquat read per-character).

VALIDATED ONLINE (this session, vs a live peer, delay=1, Fox):
  * No desync (producer-side edits only).
  * Frame-perfect airdodge frame found by an INSTRUMENTED in-cave sweep: target asfc
    = jumpsquat - 1 - delay (33/33 KneeBend->LandingFallSpecial direct, 0 air frames;
    the neighbouring target gave 1 air frame -- a frame late).
  * Up-latch + grounded-only gating proven (no double-jump artifact, repeat works,
    no airborne activation).

DELAY NOTE (read before judging on a 2-frame connection): the airdodge can only be
injected as early as the first jumpsquat frame, so frame-perfect needs
jumpsquat >= delay + 2. Fox (jumpsquat 3) is frame-perfect up to delay 1; on delay 2
it lands ~1 frame late (a fundamental producer-side floor, not a tuning miss).
Longer-jumpsquat characters (Marth 4, etc.) stay frame-perfect at delay 2. The
target is CLAMPED to >=1 so it always fires (degrades to 1-late, never "no wavedash").

NETPLAY-SAFE: two producer-side hooks inside PAD_Read, upstream of the EXI scrape:
  * 0x8034E2AC -- digital buttons (Y jump / L airdodge) via `oris r0,r0,BIT`;
    displaced rlwinm r0,r0,0x10,0x12,0x1f = 0x540084BE. (Digital L must go here --
    0x8034E680 is downstream of the analog->digital conversion.)
  * 0x8034E680 -- the airdodge stick angle (post stick-calibration); displaced
    lbz r0,7(r3) = 0x88030007.
Both resolve the LOCAL player via the ODB (port = *(*(r13-0x49E4)+0)) and read the
connection's input delay (ODB+0x21) at runtime. Per-port latch (WD_PEND) at
0x803FA470. Caves at 0x803FA600 / 0x803FA800 (debug-menu free RAM).

Two output forms (both written):
  * C2 (codehandler) form -- paste into Slippi's GALE01 gecko list.
  * RAW 06+04 form -- writes the caves to free RAM + branches the hooks directly;
    use this if the C2 form does nothing (it is the EXACT memory state the harness
    validated, and bypasses any codehandler cave-size limit).

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 make_wavedash_gecko.py
"""
import struct
import sys

import capstone
import keystone

import melee_harness as mh
from melee_harness import finalize_payload

HOOK_B, DISP_B, CAVE_B = 0x8034E2AC, 0x540084BE, 0x803FA800   # digital buttons
HOOK_A, DISP_A, CAVE_A = 0x8034E680, 0x88030007, 0x803FA600   # stick angle
GECKO_NAME = "Up-Bound Wavedash"
OUT_FILE = "online_wavedash.gecko.txt"

# CAVE_B @ 0x8034E2AC : up-gate + latch + delay-comp target + jump/airdodge buttons
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
    lbz  10, 0x21(8)          # ODB_DELAY_FRAMES
    lbz  9, 0(8)              # ODB_LOCAL_PLAYER_INDEX
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
    lwz  6, 0x2C(6)           # local Player Data
    cmpwi 6, 0
    beq  bdone
    srwi 11, 6, 24
    cmplwi 11, 0x80
    bne  bdone
    lwz  7, 0x10(6)
    rlwinm 7, 7, 0, 16, 31    # action state
    lis  12, 0x803F
    ori  12, 12, 0xA470        # WD_PEND latch at 0(r12)

    cmpwi 7, 0x18             # KneeBend?
    bne  b_notknee
    # airdodge if the latch is set OR up is held NOW. The latch (set in the grounded
    # frame) covers quick-taps; the live up-check covers the FIRST jump, whose
    # grounded frame hadn't yet registered up (0x624 lags by the input delay) -- so
    # that jump would otherwise full-hop instead of wavedash.
    lbz  8, 0(12)             # WD_PEND set?  -> airdodge
    cmpwi 8, 0
    bne  b_do_ad
    lwz  8, 0x624(6)          # else up held now? Analog Stick Data Y (float) >= 0.5625
    lis  9, 0x3F10
    cmpw 8, 9
    blt  bdone                # neither latched nor up -> no airdodge
    li   8, 1                 # up held NOW during KneeBend -> SET the latch. This
    stb  8, 0(12)             # bridges a quick up-tap (released before the target
                              # frame) through to the airdodge frame. Fox's target
                              # (asfc 1, js 3) is early enough that up-now alone caught
                              # the tap; Luigi/Marth (target asfc 2, js 4) fire a frame
                              # later, by which point a tap's up has dropped -> without
                              # this latch the tap full-hops instead of wavedashing.
b_do_ad:
    lwz  8, 0x148(6)          # jumpsquat (int or float)
    cmplwi 8, 0x100
    blt  b_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
b_js_int:
    addi 8, 8, -1            # jumpsquat - 1
    subf 8, 10, 8           # - delay  -> target
    cmpwi 8, 1
    bge  b_tgt_ok
    li   8, 1               # clamp target >= 1 (high-delay floor; always fires)
b_tgt_ok:
    lwz  9, 0x894(6)         # asfc (float)
    rlwinm 11, 9, 9, 24, 31
    rlwinm 9, 9, 0, 9, 31
    oris 9, 9, 0x0080
    subfic 11, 11, 150
    srw  9, 9, 11
    cmpw 9, 8                # asfc == target ?
    bne  bdone
    oris 0, 0, 0x0040        # digital L (airdodge); latch persists -> hook A sets stick
    b    bdone

b_notknee:
    cmpwi 7, 0x0E            # grounded-actionable (Wait..RunBrake)?
    blt  b_clear
    cmpwi 7, 0x17
    bgt  b_clear
    lwz  9, 0x624(6)        # up held? Analog Stick Data Y (FLOAT, +Y=up) -- the
                            # engine's processed stick. The raw PADStatus byte at
                            # the 0x8034E2AC hook is NOT a clean centered stickY on
                            # real hardware (flickered the trigger -> spurious hops
                            # + cleared latch -> no airdodge); 0x624 is reliable.
    lis  8, 0x3F10          # 0.5625 threshold (0x3F100000); signed float compare:
    cmpw 9, 8              # positive floats >= thr pass; neutral(0)/negative fail
    blt  b_clear            # not up -> drop the latch
    oris 0, 0, 0x0800       # Y (jump): first jump + repeat
    li   8, 1
    stb  8, 0(12)           # latch wavedash pending
    b    bdone
b_clear:
    li   8, 0
    stb  8, 0(12)           # clear latch (airborne, or grounded w/o up)
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

# CAVE_A @ 0x8034E680 : airdodge stick angle (gated on latch + same target frame)
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
    lbz  10, 0x21(8)         # delay
    lbz  9, 0(8)             # local port
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
    lwz  5, 0x2C(5)          # local Player Data
    cmpwi 5, 0
    beq  adone
    srwi 11, 5, 24
    cmplwi 11, 0x80
    bne  adone
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x18            # KneeBend only
    bne  adone
    lis  9, 0x803F          # airdodge stick if latch set OR up held now (match CAVE_B)
    ori  9, 9, 0xA470
    lbz  9, 0(9)             # WD_PEND set?
    cmpwi 9, 0
    bne  a_do_ad
    lwz  9, 0x624(5)         # else up held now? Analog Stick Data Y (float) >= 0.5625
    lis  8, 0x3F10
    cmpw 9, 8
    blt  adone
a_do_ad:
    lwz  8, 0x148(5)
    cmplwi 8, 0x100
    blt  a_js_int
    rlwinm 11, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 11, 11, 150
    srw  8, 8, 11
a_js_int:
    addi 8, 8, -1
    subf 8, 10, 8
    cmpwi 8, 1
    bge  a_tgt_ok
    li   8, 1               # clamp >= 1 (match CAVE_B)
a_tgt_ok:
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
    li   8, 0               # up-only: straight down (0, -0x70)
    li   7, -112
    b    a_set
a_right:
    li   8, 0x6A            # right (+0x6A, -0x20)
    li   7, -32
    b    a_set
a_left:
    li   8, -0x6A           # left (-0x6A, -0x20)
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

ORIS_L, ORIS_Y = 0x64000040, 0x64000800
STB_X, STB_Y = 0x99040002, 0x98E40003     # stb r8,2(r4) / stb r7,3(r4)


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    if raw is None:
        raise RuntimeError("keystone empty")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


def raw_lines(logic, hook, cave, disp):
    """06 (write cave to RAM) + 04 (branch hook) lines, matching the harness state."""
    pay = finalize_payload(logic, hook, cave, disp)
    assert pay[-2] == disp, "displaced must precede the branch-back"
    words = list(pay)
    if len(words) % 2:
        words.append(0x00000000)
    nbytes = len(words) * 4
    branch = 0x48000000 | ((cave - hook) & 0x03FFFFFC)
    lines = [f"06{cave & 0x01FFFFFF:06X} {nbytes:08X}"]
    for i in range(0, len(words), 2):
        lines.append(f"{words[i]:08X} {words[i + 1]:08X}")
    lines.append(f"04{hook & 0x01FFFFFF:06X} {branch:08X}")
    return lines, pay


def main():
    logic_b = assemble(ASM_B)
    logic_a = assemble(ASM_A)
    c2_b = mh.gecko_c2_lines(HOOK_B, logic_b, DISP_B, GECKO_NAME + " (buttons)")
    c2_a = mh.gecko_c2_lines(HOOK_A, logic_a, DISP_A, GECKO_NAME + " (stick)")
    raw_b, pay_b = raw_lines(logic_b, HOOK_B, CAVE_B, DISP_B)
    raw_a, pay_a = raw_lines(logic_a, HOOK_A, CAVE_A, DISP_A)

    # ---- verify ----
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print("=== capstone verify (RAW payloads -- what ships) ===")
    for nm, pay, cave in [("CAVE_B buttons @0x8034E2AC", pay_b, CAVE_B),
                          ("CAVE_A stick   @0x8034E680", pay_a, CAVE_A)]:
        print(f"\n--- {nm} ({len(pay)} words) ---")
        code = b"".join(w.to_bytes(4, "big") for w in pay)
        for i in md.disasm(code, cave):
            print(f"  0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}")
    assert ORIS_L in pay_b and ORIS_Y in pay_b, "cave B missing L/Y"
    assert STB_X in pay_a and STB_Y in pay_a, "cave A missing stick stores"
    assert pay_b[-2] == DISP_B and pay_a[-2] == DISP_A, "displaced not protected"
    assert CAVE_A + len(pay_a) * 4 <= CAVE_B, "cave A overruns cave B"
    print("\n[ok] L/Y present, stick stores present, displaced protected, caves disjoint.")

    # ---- write the gecko file (both forms) ----
    hdr = f"""# ============================================================================
# {GECKO_NAME}  (Super Smash Bros. Melee NTSC 1.02 / GALE01) -- netplay-safe
# ============================================================================
# BIND: hold UP on the left control stick while on the ground -> the character
#   jumps and wavedashes (airdodges into the ground). The horizontal you hold sets
#   the direction (up-only = straight-down wavedash). Keep holding up to repeat.
#   Works on any character (per-character jumpsquat read at runtime). LOCAL player
#   only (resolved via the ODB), so it only affects YOU and is netplay-safe.
#
# It only ever acts on the GROUND: the jump fires in grounded-actionable states and
#   the airdodge only during jumpsquat (KneeBend). It never airdodges in mid-air.
#
# TIMING: airdodge frame = jumpsquat - 1 - input_delay (delay read at runtime).
#   Frame-perfect needs jumpsquat >= delay + 2 -- so Fox is frame-perfect up to
#   1-frame delay and ~1 frame late at 2-frame delay (a producer-side floor);
#   higher-jumpsquat characters stay frame-perfect at 2 frames. Always fires.
#
# INSTALL (normal online play -- no savestate/harness needed):
#   Slippi Launcher -> gear -> Configure Dolphin -> Config -> open the GALE01 gecko
#   list (or edit Sys/GameSettings/GALE01r2.ini). Add BOTH codes below and ENABLE
#   them. Prefer the RAW (06+04) form -- it is the exact memory state validated on
#   the harness. If the C2 form is used instead, add BOTH C2 codes. Do not mix
#   forms (don't enable C2 and RAW of the same hook at once).
# ============================================================================
"""

    out = [hdr]
    out.append("\n# ---------- RAW 06+04 form (RECOMMENDED -- exact validated state) ----------")
    out.append(f"${GECKO_NAME} (buttons, RAW)")
    out += raw_b
    out.append(f"\n${GECKO_NAME} (stick, RAW)")
    out += raw_a
    out.append("\n# ---------- C2 codehandler form (alternative) ----------")
    out += [f"${GECKO_NAME} (buttons)"] + list(c2_b[1:])
    out.append("")
    out += [f"${GECKO_NAME} (stick)"] + list(c2_a[1:])
    out.append("")
    text = "\n".join(out)
    with open(OUT_FILE, "w") as f:
        f.write(text)
    print(f"\n=== wrote {OUT_FILE} ===\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
