"""Run the action-state probe gecko and inspect what it reads.

Setup:
  1. Install candidate_d_probe gecko (writes Marth's action state low 16
     bits to 0x803FA428 every Fox pad pass).
  2. Launch + seed.
  3. Read 0x803FA428 with Marth in Wait -> expect 0x000E (or whatever
     Wait reads as).
  4. Force Marth into Catch via dme.
  5. Read 0x803FA428 again -> expect 0x00D4 if the chain works.
"""
import sys
import time

import candidate_d_probe
from melee_harness import Harness
from scenario import CATCH, force_action_state


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d_probe.NAME,
        hook_addr=candidate_d_probe.HOOK_ADDR,
        logic_words=candidate_d_probe.LOGIC,
        displaced_orig=candidate_d_probe.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)

        # Read probe scratch with Marth still in Wait.
        probe_word = h.read_word(candidate_d_probe.PROBE_ADDR)
        probe_hi = (probe_word >> 16) & 0xFFFF
        print(f"\nMarth in Wait:")
        print(f"  P1 action_state (harness)  = 0x{h.action_state(1) & 0xFFFF:04X}")
        print(f"  scratch @ 0x{candidate_d_probe.PROBE_ADDR:08X} (top u16) = "
              f"0x{probe_hi:04X}")

        # Force Marth into Catch and re-read.
        force_action_state(h, 1, CATCH)
        time.sleep(0.5)
        probe_word2 = h.read_word(candidate_d_probe.PROBE_ADDR)
        probe_hi2 = (probe_word2 >> 16) & 0xFFFF
        print(f"\nMarth forced to Catch:")
        print(f"  P1 action_state (harness)  = 0x{h.action_state(1) & 0xFFFF:04X}")
        print(f"  scratch @ 0x{candidate_d_probe.PROBE_ADDR:08X} (top u16) = "
              f"0x{probe_hi2:04X}")

        if probe_hi2 == 0x00D4:
            print("\n[OK] gecko's action-state read works -- chain returns "
                  "the expected value. The D.1 bug must be in the comparison "
                  "or downstream logic.")
            return 0
        print(f"\n[BUG] gecko's action-state read returns 0x{probe_hi2:04X}, "
              "not 0x00D4. Chain is wrong.")
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
