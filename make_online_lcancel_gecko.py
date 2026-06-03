"""
make_online_lcancel_gecko.py -- DIGITAL-Z generator (HISTORICAL / SUPERSEDED).

>>> The shipped macro is now ANALOG L: use make_online_analog_lcancel_gecko.py. <<<
This digital-Z version L-cancels but (a) misses on aerials that hit (hitlag freezes
the action-frame counter it anchors to) and (b) can airdodge/re-nair when an aerial
ends in the air. Analog L fixes both by construction. Kept for reference only.

make_online_lcancel_gecko.py -- generate the SHIPPABLE online auto-L-cancel gecko.

This is the gate+pulse-only macro (NO self-drive — in real play the human controls
the character). It hooks the producer-side PAD_Read slot 0x8034E2AC and, while the
LOCAL player is in an aerial attack (0x41-0x45), pulses Z (0x0010) on a
1-press/6-release cadence so each press is a fresh rising edge (the game has no
input buffer) — keeping a Z press within the 7-frame L-cancel window without
re-triggering an aerial.

The cadence is ANCHORED to the aerial via the in-game "Action State Frame Counter"
(Player Data + 0x894), not the global frame counter. That field is part of game
state (so it is rollback-safe — a scratch counter is not) and resets to 1.0 on
every new action state, so the first Z press lands on the first aerial frame
regardless of when the aerial started. This is the fix for the v2 timing bug where
the unanchored global %7 cadence had a random 0-6 frame phase, so short / late /
near-ground aerials could end before any press frame fell inside them and never
L-cancelled. See docs/L_CANCEL_HANDOFF.md.

Local player is resolved dynamically from the ODB (*(r13-0x49E4) +
ODB_LOCAL_PLAYER_INDEX) so it works whether you're P1 or P2. All pointers are
MEM1-checked so it can't crash on garbage during transitions.

Validated 2026-05-21 online: the identical gate+pulse logic (as part of the
self-drive test cave) cut Fox NAIR landing 14.9f -> 7.5f with no desync and no
grab/airdodge misfires (`online_lcancel_selfdrive.py`).

Prints the Dolphin/Slippi-Manager gecko text. To deploy online: add it via Slippi
Manager, enter online normally, and re-save the slot-4 (F4) savestate so it's
baked in (see docs/ONLINE_MACRO_GUIDE.md §3). capstone-verified before printing.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 make_online_lcancel_gecko.py
"""
import struct
import sys

import capstone
import keystone

import melee_harness as mh

HOOK = 0x8034E2AC
DISPLACED = 0x540084BE     # rlwinm r0,r0,0x10,0x12,0x1f (the instruction we replace)

CAVE_ASM = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    # --- resolve the LOCAL player's Player Data (works as P1 or P2) ---
    lwz  8, -0x49E4(13)     # ODB ptr = *(r13 + OFST_R13_ODB_ADDR)
    cmpwi 8, 0
    beq  ldone
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  ldone
    lbz  9, 0(8)            # ODB_LOCAL_PLAYER_INDEX (0..3)
    mulli 9, 9, 0xE90       # * GObj stride
    lis  6, 0x8045
    ori  6, 6, 0x3130       # 0x80453130 (P1 GObj ptr base)
    add  6, 6, 9
    lwz  6, 0(6)            # local GObj ptr
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  6, 0x2C(6)         # local Player Data
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  7, 0x10(6)         # action state word
    rlwinm 7, 7, 0, 16, 31

    # --- only during an aerial attack (NAIR..DAIR) ---
    cmpwi 7, 0x41
    blt  ldone
    cmpwi 7, 0x45
    bgt  ldone

    # --- 1-press / 6-release Z cadence ANCHORED to the aerial ---
    # The cadence is keyed to the in-game "Action State Frame Counter"
    # (Player Data + 0x894), NOT the global frame counter. This is the bug fix:
    #   * It is part of game state -> rewound by rollback -> deterministic on
    #     both clients (a scratch counter is NOT rollback-safe online).
    #   * It resets to 1.0 on every new action state, so the FIRST press lands
    #     on the first detectable aerial frame regardless of when the aerial
    #     started -- fixing "slow uptake / short or near-ground aerials that
    #     never saw a global %7==0 frame don't L-cancel".
    # The field is a FLOAT (n.0). Decode it to an integer with pure integer ops
    # (no FPU): for an integer-valued single n.0 in [1, 2^24),
    #     n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exponent)
    lwz  8, 0x894(6)       # r8 = action_frame float bits
    rlwinm 9, 8, 9, 24, 31 # r9 = exponent (IEEE bits 1..8)
    rlwinm 8, 8, 0, 9, 31  # r8 = mantissa (low 23 bits)
    oris 8, 8, 0x0080      # r8 |= 0x800000 (restore implicit leading 1)
    subfic 9, 9, 150       # r9 = 150 - exponent (= right-shift amount)
    srw  8, 8, 9           # r8 = (int) action_frame  (1, 2, 3, ...)
    # press when (n-1) % 7 == 0  ->  n in {1, 8, 15, ...}: first press on the
    # first aerial frame, then every 7 frames (gap to landing always <= 6).
    addi 8, 8, -1
    li   9, 7
    divw 6, 8, 9
    mulli 6, 6, 7
    subf 8, 6, 8          # r8 = (n-1) % 7
    cmpwi 8, 0
    bne  ldone
    oris 0, 0, 0x0010      # press Z (digital; triggers L-cancel)
ldone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
"""


def main():
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(CAVE_ASM)
    logic = [struct.unpack(">I", bytes(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]

    # Use gecko_c2_lines (now fixed): it appends the displaced original AND a
    # 0x00000000 branch-slot as the LAST word, so the codehandler's branch-back
    # overwrites the throwaway slot -- NOT the displaced. The displaced
    # (0x540084BE = the button-extraction rlwinm) therefore runs exactly once.
    lines = mh.gecko_c2_lines(HOOK, logic, DISPLACED, "Online Auto L-Cancel [pulsed Z]")

    # capstone-verify: reconstruct the body words from the printed lines and show
    # the displaced is present and NOT the last (branch-slot) word.
    body = []
    for l in lines[1:]:
        body += [int(x, 16) for x in l.split()]
    body = body[2:]   # drop the C2 header pair (addr, n_lines)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    code = b"".join(w.to_bytes(4, "big") for w in body)
    print("=== capstone verify (cave body; last 0x00000000 = branch slot the "
          "codehandler overwrites) ===")
    for i in md.disasm(code, HOOK):
        print(f"  0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}")
    assert body.count(0x64000010) == 1, "expected exactly one `oris r0,r0,0x10` (Z)"
    assert DISPLACED in body, "displaced (button extraction) must be present!"
    assert body[-1] == 0x00000000, "last word must be the throwaway branch slot"
    assert body[-2] != 0x00000000, "displaced/code must sit before the branch slot"
    print(f"  [ok] one Z press; displaced 0x{DISPLACED:08X} present and protected "
          f"(branch slot 0x00000000 is last)")

    print("\n=== SHIPPABLE GECKO (paste into Slippi Manager -> Add Gecko Code) ===")
    print("$Online Auto L-Cancel [pulsed Z]")
    for l in lines[1:]:
        print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
