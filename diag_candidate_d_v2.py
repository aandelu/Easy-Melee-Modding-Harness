"""Run candidate_d_v2 and check whether the counter byte becomes 0x42
when Marth is forced into Catch."""
import sys, time
import candidate_d_v2
from melee_harness import Harness
from scenario import CATCH, force_action_state, reset_b2_counter


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d_v2.NAME,
        hook_addr=candidate_d_v2.HOOK_ADDR,
        logic_words=candidate_d_v2.LOGIC,
        displaced_orig=candidate_d_v2.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)
        reset_b2_counter(h)
        time.sleep(0.3)

        before = h.read_word(candidate_d_v2.COUNTER_ADDR) >> 24
        print(f"counter before force: 0x{before:02X}  "
              f"(Marth = 0x{h.action_state(1) & 0xFFFF:04X})")
        force_action_state(h, 1, CATCH)
        time.sleep(0.5)
        after = h.read_word(candidate_d_v2.COUNTER_ADDR) >> 24
        print(f"counter after force:  0x{after:02X}  "
              f"(Marth = 0x{h.action_state(1) & 0xFFFF:04X})")
        return 0 if after == 0x42 else 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
