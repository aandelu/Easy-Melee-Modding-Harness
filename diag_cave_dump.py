"""Install Candidate D.1 and dump the cave bytes to verify the encoding
actually landed where I think it did."""
import sys
import time

import candidate_d
from melee_harness import Harness


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d.NAME,
        hook_addr=candidate_d.HOOK_ADDR,
        logic_words=candidate_d.LOGIC,
        displaced_orig=candidate_d.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)

        # Resolve cave from hook word.
        hook_word = h.read_word(candidate_d.HOOK_ADDR)
        print(f"hook word @ 0x{candidate_d.HOOK_ADDR:08X} = 0x{hook_word:08X}")
        # b cave decoding: opcode 18, offset = (word & 0x03FFFFFC) sign-extended
        if (hook_word >> 26) == 18:
            disp = hook_word & 0x03FFFFFC
            if disp & 0x02000000:
                disp -= 0x04000000
            cave = candidate_d.HOOK_ADDR + disp
            print(f"cave @ 0x{cave:08X}")
        else:
            cave = 0x8066B310
            print(f"hook word isn't a branch; using known cave @ 0x{cave:08X}")

        for i, expected in enumerate(candidate_d.LOGIC):
            actual = h.read_word(cave + i * 4)
            mark = "" if actual == expected else "  <-- MISMATCH"
            print(f"  pos {i:>2}  @ 0x{cave + i * 4:08X}  "
                  f"expected 0x{expected:08X}  actual 0x{actual:08X}{mark}")
        # Also dump displaced and any padding.
        actual_disp = h.read_word(cave + len(candidate_d.LOGIC) * 4)
        print(f"  displaced  @ 0x{cave + len(candidate_d.LOGIC) * 4:08X}  "
              f"expected 0x{candidate_d.DISPLACED_ORIG:08X}  actual "
              f"0x{actual_disp:08X}")
        # The codehandler will have appended a branch back; print a few more.
        for j in range(4):
            addr = cave + (len(candidate_d.LOGIC) + 1 + j) * 4
            print(f"  +{j+1:>2}       @ 0x{addr:08X}  = 0x{h.read_word(addr):08X}")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
