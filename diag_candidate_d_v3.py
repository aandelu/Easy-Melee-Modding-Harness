"""Run candidate_d_v3: same as v2 but with a sentinel write after the
counter check. Tests whether the counter check is taking unexpectedly."""
import sys, time
import candidate_d_v3
from melee_harness import Harness
from scenario import CATCH, force_action_state, reset_b2_counter


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d_v3.NAME,
        hook_addr=candidate_d_v3.HOOK_ADDR,
        logic_words=candidate_d_v3.LOGIC,
        displaced_orig=candidate_d_v3.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)
        reset_b2_counter(h)
        h.write_bytes(candidate_d_v3.SENTINEL_ADDR, b"\x00")
        time.sleep(0.5)

        cnt_pre = h.read_word(candidate_d_v3.COUNTER_ADDR) >> 24
        sent_pre = h.read_word(candidate_d_v3.SENTINEL_ADDR) >> 24
        print(f"PRE: counter=0x{cnt_pre:02X} sentinel=0x{sent_pre:02X}  "
              f"Marth=0x{h.action_state(1) & 0xFFFF:04X}")

        force_action_state(h, 1, CATCH)
        time.sleep(0.5)

        cnt_post = h.read_word(candidate_d_v3.COUNTER_ADDR) >> 24
        sent_post = h.read_word(candidate_d_v3.SENTINEL_ADDR) >> 24
        print(f"POST: counter=0x{cnt_post:02X} sentinel=0x{sent_post:02X}  "
              f"Marth=0x{h.action_state(1) & 0xFFFF:04X}")

        print()
        print(f"sentinel = 0xCC -> got past counter check")
        print(f"counter  = 0x42 -> trigger arm fired")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
