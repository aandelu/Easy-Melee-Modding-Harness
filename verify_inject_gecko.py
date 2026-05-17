"""
Task-#3 smoke test (Path A: boot-time gecko install).

Stages a minimal C2 gecko code that increments a counter in unused MEM1 every
time the hook fires, launches Dolphin (Slippi's bootloader installs the code
with proper icache flush), and watches the counter advance via dme.

No savestate is loaded -- the pad-process loop @ 0x803775C0 fires every frame
even at the Slippi netplay menu. If the counter advances, Path A works.

Prior dme-runtime-inject attempts confirmed Dolphin's emulated CPU does NOT
observe dme writes to instruction memory (counter stayed at 0). The bootloader
path is the only viable injection mechanism on Slippi Dolphin.
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT


HOOK_ADDR = 0x803775C0
HOOK_ORIG = 0x88190002          # lbz r0, 2(r25)
COUNTER_ADDR = 0x803FA428       # inside DEFAULT_CAVE region (debug menu tables)


# 5-instruction logic body: increment word at COUNTER_ADDR.
# r0 is clobbered by the displaced original (lbz r0, 2(r25)) right after this
# runs, so it's free. r11/r12 are volatile in PPC EABI -- safe to clobber.
# r25 (PADStatus pointer) untouched.
LOGIC = [
    0x3D80803F,    # lis  r12, 0x803F
    0x618CA428,    # ori  r12, r12, 0xA428    ; r12 = COUNTER_ADDR
    0x816C0000,    # lwz  r11, 0(r12)
    0x396B0001,    # addi r11, r11, 1
    0x916C0000,    # stw  r11, 0(r12)
]


def main():
    h = Harness()
    try:
        print("staging gecko C2 code + launching", flush=True)
        h.install_gecko_c2(
            name="frame-counter-smoke",
            hook_addr=HOOK_ADDR,
            logic_words=LOGIC,
            displaced_orig=HOOK_ORIG,
        )
        h.launch()
        h.hook_dme()

        # Wait for the CPU to be ticking (Slippi bootloader has installed
        # codes and the game is past initial boot).
        print("waiting for the CPU to start ticking ...", flush=True)
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"  CPU live ({prev} -> {cur})", flush=True)
                break
            prev = cur
        else:
            print("[FAIL] CPU never started ticking", flush=True)
            return 1

        # Info: hook word at 0x803775C0 visible to dme. With Slippi's
        # codehandler installed, this MAY or may not show as a branch -- the
        # functional proof is whether the payload's counter advances.
        cur_hook = h.read_word(HOOK_ADDR)
        print(f"hook 0x{HOOK_ADDR:08X} reads as 0x{cur_hook:08X} via dme",
              flush=True)

        # Watch counter for 15 real seconds. With the pad-process loop firing
        # multiple times per frame across all pad slots, expect hundreds to
        # thousands of hits at JIT speed.
        before = h.read_word(COUNTER_ADDR)
        print(f"\npre-watch counter @ 0x{COUNTER_ADDR:08X} = 0x{before:08X}",
              flush=True)
        print("polling counter for 15s ...", flush=True)
        last = before
        for i in range(15):
            time.sleep(1.0)
            cur = h.read_word(COUNTER_ADDR)
            delta = (cur - before) & 0xFFFFFFFF
            print(f"  t={i + 1:2d}s  counter=0x{cur:08X}  (delta {delta})",
                  flush=True)
            last = cur

        delta = (last - before) & 0xFFFFFFFF
        if delta > 0:
            print(f"\n[PASS] gecko-installed payload runs at boot "
                  f"({delta} hits in 15s ~= {delta / 15:.0f}/s).", flush=True)
            return 0
        print(f"\n[FAIL] counter never advanced", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
