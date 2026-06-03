"""
online_probe_hooks.py -- read-only: which gecko hooks are live ONLINE?

Attaches to the running online Dolphin and majority-reads a set of hook
addresses to classify each as BRANCH (code installed) vs its known vanilla
opcode (reverted / never installed). Compares Slippi's own required hooks
against our harness-added ones to find out what online keeps vs strips.
"""
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
from melee_harness import Harness

SCENE_WORD = 0x80479D30


def mm(word):
    return ((word << 8) | (word >> 24)) & 0xFFFF


def majority_word(h, addr, n=11):
    vals = []
    for _ in range(n):
        try:
            vals.append(h.read_word(addr))
        except Exception:
            vals.append(-1)
        time.sleep(0.01)
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


# (addr, label, known vanilla opcode or None, owner)
HOOKS = [
    (0x803775C0, "meta-flush (ours)",            0x88190002, "harness/user"),
    (0x803775B8, "auto_lcancel (ours)",          0xA0190000, "harness/user"),
    (0x801A4FA4, "Extract Menu Info (user INI)", None,       "user INI"),
    (0x80376A20, "SkipNewInputFetch (Slippi)",   None,       "Slippi Sys"),
    (0x80376A24, "ApplyInGameDelay (Slippi)",    None,       "Slippi Sys"),
    (0x80376A28, "TriggerSendInput (Slippi)",    None,       "Slippi Sys"),
    (0x8034E2AC, "Altimor slot (not installed)", None,       "n/a"),
]


def classify(word, vanilla):
    if (word & 0xFC000000) == 0x48000000:
        return "BRANCH (installed)"
    if vanilla is not None and word == vanilla:
        return "VANILLA (reverted/never-installed)"
    return "other"


def main():
    h = Harness()
    h.hook_dme()
    top, c, n = majority_word(h, SCENE_WORD)
    print(f"scene 0x{top:08X} -> minorMajor 0x{mm(top):04X} ({c}/{n})  "
          f"{'ONLINE' if mm(top)==0x0208 else 'NOT online'}", flush=True)
    print(flush=True)
    print(f"{'addr':<12}{'word':<12}{'state':<38}{'owner':<14}label", flush=True)
    for addr, label, vanilla, owner in HOOKS:
        w, c, n = majority_word(h, addr)
        state = classify(w, vanilla)
        print(f"0x{addr:08X}  0x{w:08X}  {state:<38}{owner:<14}{label} ({c}/{n})",
              flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
