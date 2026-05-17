"""Run candidate_d_probe2 and read both echoes after the gecko has fired
for many frames. counter_echo should show the value of the counter byte
as the gecko sees it; action_echo should show Marth's action state."""
import sys, time
import candidate_d_probe2
from melee_harness import Harness
from scenario import CATCH, force_action_state, reset_b2_counter


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_d_probe2.NAME,
        hook_addr=candidate_d_probe2.HOOK_ADDR,
        logic_words=candidate_d_probe2.LOGIC,
        displaced_orig=candidate_d_probe2.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)
        reset_b2_counter(h)
        # Zero both echo addresses so we can detect writes.
        h.write_bytes(candidate_d_probe2.COUNTER_ECHO_ADDR, b"\x00")
        h.write_bytes(candidate_d_probe2.ACTION_ECHO_ADDR, b"\x00\x00")
        time.sleep(0.5)

        ce = h.read_word(candidate_d_probe2.COUNTER_ECHO_ADDR) >> 24
        ae_word = h.read_word(candidate_d_probe2.ACTION_ECHO_ADDR)
        ae_hi = (ae_word >> 16) & 0xFFFF
        print(f"PRE  (Marth in Wait):")
        print(f"  counter @ 0x803FA424     = 0x{h.read_word(0x803FA424) >> 24:02X}")
        print(f"  counter echo @ 0x{candidate_d_probe2.COUNTER_ECHO_ADDR:08X} = 0x{ce:02X}")
        print(f"  action echo @ 0x{candidate_d_probe2.ACTION_ECHO_ADDR:08X}  = 0x{ae_hi:04X}")
        print(f"  harness reads Marth action = 0x{h.action_state(1) & 0xFFFF:04X}")

        force_action_state(h, 1, CATCH)
        time.sleep(0.5)
        ce = h.read_word(candidate_d_probe2.COUNTER_ECHO_ADDR) >> 24
        ae_word = h.read_word(candidate_d_probe2.ACTION_ECHO_ADDR)
        ae_hi = (ae_word >> 16) & 0xFFFF
        print(f"\nPOST (Marth forced to Catch):")
        print(f"  counter @ 0x803FA424     = 0x{h.read_word(0x803FA424) >> 24:02X}")
        print(f"  counter echo @ 0x{candidate_d_probe2.COUNTER_ECHO_ADDR:08X} = 0x{ce:02X}")
        print(f"  action echo @ 0x{candidate_d_probe2.ACTION_ECHO_ADDR:08X}  = 0x{ae_hi:04X}")
        print(f"  harness reads Marth action = 0x{h.action_state(1) & 0xFFFF:04X}")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
