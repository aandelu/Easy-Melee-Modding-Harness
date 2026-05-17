"""Quick diagnostic: read the FULL word at Marth's Player Data + 0x10 to see
exactly where the 0xD4 byte lives in the 32-bit word."""
import sys, time
from melee_harness import Harness
from scenario import force_action_state, CATCH

h = Harness()
try:
    h.launch()
    h.hook_dme()
    h.seed_snapshot()
    h.reset()
    time.sleep(0.5)
    pd = h.player_data_ptr(1)
    print(f"Marth Player Data ptr = 0x{pd:08X}")
    word_before = h.read_word(pd + 0x10)
    print(f"action_state word BEFORE force (+0x10) = 0x{word_before:08X}")
    print(f"  byte at +0x10 = 0x{(word_before >> 24) & 0xFF:02X}")
    print(f"  byte at +0x11 = 0x{(word_before >> 16) & 0xFF:02X}")
    print(f"  byte at +0x12 = 0x{(word_before >>  8) & 0xFF:02X}")
    print(f"  byte at +0x13 = 0x{(word_before >>  0) & 0xFF:02X}")
    force_action_state(h, 1, CATCH)
    time.sleep(0.3)
    word_after = h.read_word(pd + 0x10)
    print(f"action_state word AFTER force = 0x{word_after:08X}")
    print(f"  byte at +0x10 = 0x{(word_after >> 24) & 0xFF:02X}")
    print(f"  byte at +0x11 = 0x{(word_after >> 16) & 0xFF:02X}")
    print(f"  byte at +0x12 = 0x{(word_after >>  8) & 0xFF:02X}")
    print(f"  byte at +0x13 = 0x{(word_after >>  0) & 0xFF:02X}")
    # The gecko also reads via 0x80453130 -> +0x2C. Verify same ptr.
    gobj = h.read_word(0x80453130)
    pd_via_gecko_chain = h.read_word(gobj + 0x2C)
    print(f"\nP1 GObj @ 0x80453130 = 0x{gobj:08X}")
    print(f"PD via GObj +0x2C    = 0x{pd_via_gecko_chain:08X}  "
          f"(matches harness PD? {pd_via_gecko_chain == pd})")
finally:
    h.close()
