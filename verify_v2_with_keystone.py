"""Bit-for-bit verify candidate_d_standalone_v2.LOGIC against keystone.

Hand-encoded gecko bodies are easy to typo (rA vs rS swaps, off-by-one
branch offsets, wrong opcode for srwi vs srawi, etc.). Catches like
verifying with capstone-disasm are round-trips through the *same* manual
encoding, so a typo that produces a valid-but-wrong instruction
(e.g. lbz r11, 0(r11) instead of lbz r9, 0(r11)) can survive.

This script does the strictly-stronger check:
  1. Write the macro out in PPC assembly with named labels (no manual
     branch offsets)
  2. Assemble with keystone-engine (libkeystone)
  3. Diff against candidate_d_standalone_v2.LOGIC word-by-word

Any miscount or label-resolution mistake in the hand-encoded version
shows up as a mismatch.

Run with:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 verify_v2_with_keystone.py
"""
import sys
import struct

import keystone

import candidate_d_standalone_v2 as cs


# Same body keystone will assemble. Labels are resolved by keystone, so
# every "b/beq/bne/blt/bgt <label>" is independently computed from the
# label's address -- the hand-coded offsets in candidate file never enter
# the picture here.
SRC = """
    cmpwi 24, 1
    bne   end
    lis   11, 0x803F
    ori   11, 11, 0xA424
    lbz   9, 0(11)
    cmpwi 9, 0
    beq   read_marth
    cmpwi 9, 5
    bgt   read_marth
    cmpwi 9, 3
    blt   inc_only
    lhz   0, 0(25)
    ori   0, 0, 0x200
    sth   0, 0(25)
    li    0, 0x81
    stb   0, 3(25)
inc_only:
    addi  9, 9, 1
    stb   9, 0(11)
    b     end
read_marth:
    lis   12, 0x8045
    ori   12, 12, 0x3130
    lwz   12, 0(12)
    cmpwi 12, 0
    beq   end
    srwi  0, 12, 24
    cmplwi 0, 0x80
    bne   end
    lwz   12, 0x2C(12)
    cmpwi 12, 0
    beq   end
    srwi  0, 12, 24
    cmplwi 0, 0x80
    bne   end
    lhz   0, 0x12(12)
    cmplwi 0, 0xD4
    blt   not_grab
    cmplwi 0, 0xD6
    bgt   not_grab
    cmpwi 9, 0
    bne   end
    li    0, 1
    stb   0, 0(11)
    lhz   0, 0(25)
    ori   0, 0, 0x800
    sth   0, 0(25)
    b     end
not_grab:
    cmpwi 9, 0
    beq   end
    li    0, 0
    stb   0, 0(11)
end:
"""


def main():
    ks = keystone.Ks(
        keystone.KS_ARCH_PPC,
        keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN,
    )
    raw, _ = ks.asm(SRC)
    assert len(raw) % 4 == 0, f"keystone output length {len(raw)} not /4"
    words = [struct.unpack(">I", bytes(raw[i:i+4]))[0]
             for i in range(0, len(raw), 4)]

    print(f"keystone produced {len(words)} words; candidate has "
          f"{len(cs.LOGIC)} words")
    if len(words) != len(cs.LOGIC):
        print("LENGTH MISMATCH")
        return 1

    mismatches = []
    for i, (k, c) in enumerate(zip(words, cs.LOGIC)):
        marker = "" if k == c else "  <-- MISMATCH"
        print(f"  [{i:2}] keystone={k:08X}  hand={c:08X}{marker}")
        if k != c:
            mismatches.append((i, k, c))

    print()
    if mismatches:
        print(f"FAIL: {len(mismatches)} mismatch(es)")
        for i, k, c in mismatches:
            print(f"  idx {i}: keystone=0x{k:08X}  hand=0x{c:08X}")
        return 1
    print(f"PASS: all {len(words)} words match bit-for-bit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
