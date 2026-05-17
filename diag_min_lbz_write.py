import sys, time
import candidate_min_lbz_write as g
from melee_harness import Harness
from scenario import reset_b2_counter

h = Harness()
h.install_gecko_c2(name=g.NAME, hook_addr=g.HOOK_ADDR, logic_words=g.LOGIC, displaced_orig=g.DISPLACED_ORIG)
try:
    h.launch()
    h.hook_dme()
    h.seed_snapshot()
    h.reset()
    time.sleep(0.5)
    reset_b2_counter(h)
    time.sleep(0.5)
    cnt = h.read_word(g.COUNTER_ADDR) >> 24
    print(f"counter = 0x{cnt:02X}  (expect 0x42)")
finally:
    h.close()
