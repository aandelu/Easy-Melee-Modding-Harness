"""
make_cactuar_dash_gecko.py -- generate the SHIPPABLE Cactuar dash gecko.

The Cactuar dash skips Melee's slow run-turnaround (TurnRun 0x13, ~20-30f). While
the LOCAL player is in a Run (0x15) or RunBrake (0x17) and slams the stick to the
extreme OPPOSITE of their facing (|X| >= 0x60, |Y| <= 0x18 -- a clean turnaround
intent), this VETOES that input and substitutes a crouch (stickX=0, stickY=full
down). It holds the crouch through Squat (0x27), releasing once the squat frame
counter (+0x894) reaches THRESH_FRAMES -- an EARLY release that compensates the
~2-frame netplay input delay so the real dash-out lands on ~frame 7. On release the
player's still-held opposite stick dashes them out the new direction.

NETPLAY-SAFE (producer-side): hook 0x8034E680, inside PAD_Read after the stick bytes
are calibrated (2/3(r4)) and well upstream of TriggerSendInput's EXI scrape
(0x80376A28), so the edited input is what gets transmitted -- both clients simulate
identically (no desync; confirmed online, 0 desync over 65 reversals). Displaced
original lbz r0,7(r3) = 0x88030007; r3 (calib ptr), r4 (PADStatus), r13 preserved.
Local player resolved from the ODB (*(r13-0x49E4)+0) so it works as P1 or P2. All
pointers MEM1-checked.

VALIDATED:
  * Offline A/B: TurnRun(0x13) 30f -> 0f; replaced by Squat(7f) -> dash, facing flips.
  * Online: 65/65 run-reversals -> crouch, 0 TurnRun, 0 desync. Delay-comp at squat
    frame 5 gave 7.0f crouch; this ships frame 4 (~6f, snappier) per user feel.

THRESH_FRAMES is the one tunable: lower = snappier (earlier dash), higher = later.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 make_cactuar_dash_gecko.py
"""
import struct
import sys

import capstone
import keystone

import melee_harness as mh

HOOK = 0x8034E680
DISPLACED = 0x88030007          # lbz r0, 7(r3)
GECKO_NAME = "Cactuar Dash"
OUT_FILE = "online_cactuar_dash.gecko.txt"
RAW_OUT_FILE = "online_cactuar_dash.raw.gecko.txt"

XTHRESH = 0x60                  # |stickX| opposite-extreme threshold
YDEAD = 0x18                   # |stickY| max to count as "Y=0"
# Release threshold is computed at runtime as (6 - ODB_DELAY_FRAMES) so the dash-out
# lands on the first actionable frame on any machine (1-frame delay -> squat frame 5,
# 2-frame -> 4). ODB_DELAY_FRAMES is at +0x21 in the ODB (from Slippi Online.s).

CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)

    # --- resolve the LOCAL player's Player Data (works as P1 or P2) ---
    lwz  8, -0x49E4(13)        # ODB ptr
    cmpwi 8, 0
    beq  done
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  done
    lbz  9, 0(8)               # ODB_LOCAL_PLAYER_INDEX
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)               # local GObj
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)            # local Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31     # action state

    # --- phase HOLD: in Squat(0x27) -> crouch until 0x894 >= (6 - ODB_DELAY_FRAMES) ---
    # Reading the delay at runtime lands the release on the first actionable frame on
    # ANY machine (1-frame delay -> release at squat frame 5; 2-frame -> 4). 0x894 is a
    # float (resets to 1.0 per state); decode to int without FPU.
    cmpwi 7, 0x27
    bne  chk_trig
    lbz  9, 0x21(8)          # ODB_DELAY_FRAMES (r8 = ODB ptr)
    subfic 9, 9, 6           # r9 = 6 - delay  (release threshold)
    lwz  6, 0x894(5)         # squat frame counter (float bits)
    rlwinm 10, 6, 9, 24, 31  # exp = (bits >> 23) & 0xFF
    rlwinm 6, 6, 0, 9, 31    # mantissa = bits & 0x7FFFFF
    oris 6, 6, 0x0080        #          | 0x800000
    subfic 10, 10, 150       # shift = 150 - exp
    srw  6, 6, 10            # n = (int) squat frame
    cmpw 6, 9
    bge  done                # crouched long enough -> release (dash out)
    b    do_override

chk_trig:
    # --- phase TRIGGER: Run 0x15 / RunBrake 0x17 ? ---
    cmpwi 7, 0x15
    beq  trig
    cmpwi 7, 0x17
    bne  done
trig:
    # |stickY| <= YDEAD ?
    lbz  6, 3(4)
    extsb 6, 6
    srawi 9, 6, 31
    xor  6, 6, 9
    subf 6, 9, 6             # abs(stickY)
    cmpwi 6, {YDEAD}
    bgt  done
    # stickX opposite-extreme vs facing?
    lwz  10, 0x2C(5)         # facing (sign = dir)
    lbz  6, 2(4)
    extsb 6, 6              # stickX signed
    cmpwi 10, 0
    blt  fac_left
    # facing right -> opposite LEFT: stickX <= -XTHRESH
    cmpwi 6, -{XTHRESH}
    bgt  done
    b    do_override
fac_left:
    # facing left -> opposite RIGHT: stickX >= +XTHRESH
    cmpwi 6, {XTHRESH}
    blt  done

do_override:
    li   6, 0
    stb  6, 2(4)            # stickX = 0
    li   6, -128
    stb  6, 3(4)            # stickY = full down (crouch)
done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    addi 1, 1, 0x20
"""

STB_X = 0x98C40002             # stb r6, 2(r4)
STB_Y = 0x98C40003             # stb r6, 3(r4)

HEADER = f"""# ============================================================================
# Cactuar Dash  (Super Smash Bros. Melee NTSC 1.02 / GALE01) -- netplay-safe
# ============================================================================
# WHAT: while you're in a run (Run 0x15 / RunBrake 0x17) and slam the stick to the
#   extreme OPPOSITE of your facing, this vetoes the slow turnaround (TurnRun 0x13)
#   and instead crouches ~7 frames, then releases so your held stick dashes you out
#   the new direction. Local player only (resolved via the ODB; works as P1/P2).
#
# NETPLAY-SAFE: producer-side input edit at 0x8034E680 (inside PAD_Read, upstream of
#   Slippi's EXI scrape) -> the peer receives the edited input; no desync. Validated
#   online: 48/49 run-reversals crouched, 0 desync.
#
# DELAY-ADAPTIVE: the crouch is released the moment you become actionable, computed
#   as squat frame (6 - ODB_DELAY_FRAMES) -- read at runtime -- so the dash-out lands
#   on time whether the connection runs 1 or 2 frames of input delay. No tuning knob.
#
# INSTALL (normal online play):
#   1. Slippi Launcher -> (gear) -> Configure Dolphin -> Config -> open the Gecko
#      code list for GALE01, OR edit Sys/GameSettings/GALE01r2.ini -- "Add New Code",
#      paste everything below the title line, ENABLE it.
#   2. Play online normally. The code is active in all matches (no savestate needed).
#   (Only the dev harness needed a slot-4 bake; normal play does not.)
# ============================================================================
"""


def main():
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(CAVE_ASM)
    logic = [struct.unpack(">I", bytes(raw[i:i + 4]))[0]
             for i in range(0, len(raw), 4)]

    lines = mh.gecko_c2_lines(HOOK, logic, DISPLACED, GECKO_NAME)

    # capstone verify the packed body
    body = []
    for l in lines[1:]:
        body += [int(x, 16) for x in l.split()]
    body = body[2:]            # drop the C2 header pair (addr, n_lines)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    code = b"".join(w.to_bytes(4, "big") for w in body)
    print("=== capstone verify (cave body; last 0x00000000 = branch slot) ===")
    for i in md.disasm(code, HOOK):
        print(f"  0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}")
    NOP = 0x60000000
    assert body.count(STB_X) == 1, "expected exactly one `stb r6,2(r4)`"
    assert body.count(STB_Y) == 1, "expected exactly one `stb r6,3(r4)`"
    assert DISPLACED in body, "displaced lbz r0,7(r3) must be present!"
    assert body[-1] == 0x00000000, "last word must be the throwaway branch slot"
    # gecko_c2_lines packs [logic][displaced]([nop] to stay even)[branch-slot]; the
    # codehandler only overwrites body[-1], so the displaced (at -2 or -3) is protected.
    assert body[-2] == DISPLACED or (body[-2] == NOP and body[-3] == DISPLACED), \
        "displaced must immediately precede the branch slot (allowing the even-pad nop)"
    print(f"  [ok] override stores present; displaced 0x{DISPLACED:08X} present + protected; "
          f"release threshold = (6 - ODB_DELAY_FRAMES), computed at runtime")

    gecko_lines = [f"${GECKO_NAME}"] + list(lines[1:])
    out = HEADER + "\n" + "\n".join(gecko_lines) + "\n"
    with open(OUT_FILE, "w") as f:
        f.write(out)
    print(f"\n=== wrote {OUT_FILE} ===\n")
    print(out)

    # ----------------------------------------------------------------------
    # RAW (06 + 04) variant: write the cave into free debug-menu RAM (06) and the
    # hook branch (04), bypassing the C2 codehandler cave/heap entirely. This is the
    # EXACT memory state the harness proved works (cave @ 0x803FA600, branch @ HOOK),
    # so it isn't subject to whatever append-space limit blocked the big C2 in Slippi.
    # ----------------------------------------------------------------------
    RAW_CAVE = 0x803FA600                       # debug-menu free region (safe to clobber)
    raw = mh.finalize_payload(logic, HOOK, RAW_CAVE, DISPLACED)  # [logic][displaced][b HOOK+4]
    assert raw[-2] == DISPLACED, "raw: displaced must precede the branch-back"
    words = list(raw)
    if len(words) % 2:
        words.append(0x00000000)                # pad to even for clean 8-byte 06 lines
    nbytes = len(words) * 4
    branch = 0x48000000 | ((RAW_CAVE - HOOK) & 0x03FFFFFC)
    raw_lines = [f"06{RAW_CAVE & 0x01FFFFFF:06X} {nbytes:08X}"]
    for i in range(0, len(words), 2):
        raw_lines.append(f"{words[i]:08X} {words[i + 1]:08X}")
    raw_lines.append(f"04{HOOK & 0x01FFFFFF:06X} {branch:08X}")

    raw_header = (
        "# ============================================================================\n"
        "# Cactuar Dash (RAW 06+04 form) -- use this if the C2 form above does nothing.\n"
        "# Writes the cave to free RAM 0x803FA600 (06) + the hook branch at 0x8034E680\n"
        f"# (04 -> {branch:08X} = b 0x{RAW_CAVE:08X}). Same memory state the harness validated;\n"
        "# bypasses the codehandler cave so code size is not a constraint. Add + enable\n"
        "# exactly like the C2 form. Netplay-safe; delay-adaptive (6 - ODB_DELAY_FRAMES).\n"
        "# ============================================================================\n")
    raw_out = raw_header + f"\n${GECKO_NAME}\n" + "\n".join(raw_lines) + "\n"
    with open(RAW_OUT_FILE, "w") as f:
        f.write(raw_out)
    print(f"\n=== wrote {RAW_OUT_FILE} ({len(words)} words to 0x{RAW_CAVE:08X}) ===\n")
    print(raw_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
