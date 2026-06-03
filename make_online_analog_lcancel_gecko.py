"""
make_online_analog_lcancel_gecko.py -- generate the SHIPPABLE online auto-L-cancel
gecko, ANALOG-L version (v4). This supersedes the digital-Z make_online_lcancel_gecko.py.

While the LOCAL player is in an aerial attack (0x41-0x45), this pulses a LIGHT
ANALOG L trigger (value 0x80, below the 0xAA digital-conversion threshold) every
other frame. Why analog L beats digital Z:
  * It L-cancels (a pulsed/rising-edge light trigger satisfies the L-cancel window;
    proven offline 15f->7f and online 15/15).
  * It CANNOT airdodge or re-nair: PAD_Read only sets a digital button bit when the
    analog trigger >= 0xAA (0x8034E244), and the airdodge trigger-check reads the
    digital L/R timer (0x680) -- a value < 0xAA touches neither, and presses no Z
    (Z is what re-nairs). This fixes the trailing-spill (BUG 2) by construction.
  * The pulse is keyed to the GLOBAL frame counter parity, which keeps ticking
    through hitlag (unlike the action-state frame counter, which freezes). So it
    keeps L-cancelling when an aerial connects (fixes the hitlag miss) -- confirmed
    online 10/10 hit-aerials.

INJECTION POINT (producer-side; found via disasm_lcancel_analog.py): PAD_Read
finalizes the analog L byte 6(r4) at 0x8034E67C (per-port calibration) and the
report-builder returns at 0x8034E69C. We hook 0x8034E680 -- analog L is final, the
function hasn't returned, and this is well upstream of TriggerSendInput's EXI scrape
(0x80376A28), so the edit is producer-side / netplay-safe. r4 = local PADStatus;
r3 = calibration ptr (preserved for the displaced lbz r0,7(r3)).

Local player resolved from the ODB (*(r13-0x49E4)+ODB_LOCAL_PLAYER_INDEX) so it
works as P1 or P2. All pointers MEM1-checked.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 make_online_analog_lcancel_gecko.py
"""
import struct
import sys

import capstone
import keystone

import melee_harness as mh

HOOK = 0x8034E680
DISPLACED = 0x88030007     # lbz r0, 7(r3)  (the instruction we replace; R-trig calib)
ANALOG_VAL = 0x80          # light analog L (< 0xAA -> no digital bit, no airdodge)

CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)

    # --- resolve the LOCAL player's Player Data (works as P1 or P2) ---
    lwz  8, -0x49E4(13)     # ODB ptr
    cmpwi 8, 0
    beq  done
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  done
    lbz  9, 0(8)            # ODB_LOCAL_PLAYER_INDEX
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)           # local GObj
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)        # local Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  7, 0x10(5)        # action state
    rlwinm 7, 7, 0, 16, 31

    # --- only during an aerial attack (NAIR..DAIR) ---
    cmpwi 7, 0x41
    blt  done
    cmpwi 7, 0x45
    bgt  done

    # --- pulse light analog L every other frame (global parity; spans hitlag) ---
    lis  9, 0x8047
    ori  9, 9, 0x9D60
    lwz  9, 0(9)           # global frame counter
    andi. 9, 9, 1
    bne  done             # odd frame -> release (leave the real trigger value)
    li   6, 0x{ANALOG_VAL:02X}
    stb  6, 6(4)          # analog L byte in the local PADStatus (post-calibration)
done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    addi 1, 1, 0x20
"""


def main():
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(CAVE_ASM)
    logic = [struct.unpack(">I", bytes(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]

    lines = mh.gecko_c2_lines(HOOK, logic, DISPLACED, "Online Auto L-Cancel [analog L]")

    body = []
    for l in lines[1:]:
        body += [int(x, 16) for x in l.split()]
    body = body[2:]   # drop the C2 header pair (addr, n_lines)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    code = b"".join(w.to_bytes(4, "big") for w in body)
    print("=== capstone verify (cave body; last 0x00000000 = branch slot) ===")
    for i in md.disasm(code, HOOK):
        print(f"  0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}")
    assert body.count(0x98C40006) == 1, "expected exactly one `stb r6,6(r4)` (analog L)"
    assert DISPLACED in body, "displaced lbz r0,7(r3) must be present!"
    assert body[-1] == 0x00000000, "last word must be the throwaway branch slot"
    assert body[-2] == DISPLACED, "displaced must sit just before the branch slot"
    print(f"  [ok] one analog-L store; displaced 0x{DISPLACED:08X} present and protected")

    print("\n=== SHIPPABLE GECKO (paste into Slippi Manager -> Add Gecko Code) ===")
    print("$Online Auto L-Cancel [analog L]")
    for l in lines[1:]:
        print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
