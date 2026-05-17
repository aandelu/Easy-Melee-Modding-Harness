"""Test: does a bare gecko that just stb's 0x42 to 0x803FA424 work?"""
import sys, time
import candidate_min_write
from melee_harness import Harness
from scenario import reset_b2_counter


def main():
    h = Harness()
    h.install_gecko_c2(
        name=candidate_min_write.NAME,
        hook_addr=candidate_min_write.HOOK_ADDR,
        logic_words=candidate_min_write.LOGIC,
        displaced_orig=candidate_min_write.DISPLACED_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.reset()
        time.sleep(0.5)
        reset_b2_counter(h)
        time.sleep(0.5)
        cnt = h.read_word(candidate_min_write.COUNTER_ADDR) >> 24
        print(f"counter @ 0x{candidate_min_write.COUNTER_ADDR:08X} = 0x{cnt:02X}  "
              f"(expect 0x42 if minimal write works)")
        return 0 if cnt == 0x42 else 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
