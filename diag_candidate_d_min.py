"""Run candidate_d_min and observe whether the counter byte at
0x803FA424 becomes 0x42 when Marth is forced into Catch."""
import sys
import time

import candidate_d_min
from melee_harness import Harness
from scenario import CATCH, force_action_state, reset_b2_counter


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d_min.NAME,
        hook_addr=candidate_d_min.HOOK_ADDR,
        logic_words=candidate_d_min.LOGIC,
        displaced_orig=candidate_d_min.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)
        reset_b2_counter(h)
        time.sleep(0.2)

        cnt_before = h.read_word(candidate_d_min.COUNTER_ADDR) >> 24
        print(f"counter BEFORE force (Marth in Wait):  0x{cnt_before:02X}")
        print(f"  Marth action state = 0x{h.action_state(1) & 0xFFFF:04X}")

        force_action_state(h, 1, CATCH)
        time.sleep(0.5)

        cnt_after = h.read_word(candidate_d_min.COUNTER_ADDR) >> 24
        print(f"counter AFTER force  (Marth in Catch): 0x{cnt_after:02X}")
        print(f"  Marth action state = 0x{h.action_state(1) & 0xFFFF:04X}")

        if cnt_after == 0x42:
            print("\n[OK] minimal trigger fires. Bug is in D.1's "
                  "counter-check or state-machine layer.")
            return 0
        print(f"\n[BUG] counter never reached 0x42 (still 0x{cnt_after:02X}). "
              "Bug is in the action-state comparison or pointer chain.")
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
