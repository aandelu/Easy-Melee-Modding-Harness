"""Diagnose: does F2 (savestate load) wipe a boot-installed gecko?

Installs the same counter-increment gecko verify_inject_gecko.py used (hooks
0x803775C0, increments word at COUNTER_ADDR every pad pass), then:

  Phase A: post-launch, BEFORE F2. Watch counter for 5s. Should advance.
  Phase B: send F2. Watch counter for 5s. If gecko was wiped by savestate
           load, counter freezes here.
  Phase C: write the gecko bytes back via dme (overlay cave + hook patch).
           Watch counter for 5s. If post-savestate dme overlay is observed
           by the CPU emulator, counter resumes. If not, still frozen.

Outcomes determine the harness architecture:
  - A advances, B freezes, C resumes -> overlay works; harness needs an
    overlay step after F2.
  - A advances, B freezes, C freezes -> overlay does NOT work; harness
    needs an entirely different reset strategy (no F2 / a new savestate
    captured WITH the gecko / etc).
  - A doesn't advance -> something more fundamental is broken.
"""
import sys
import time

from melee_harness import (
    DEFAULT_CAVE,
    Harness,
    POWERON_COUNT,
    make_branch,
)


HOOK_ADDR = 0x803775C0
HOOK_ORIG = 0x88190002
COUNTER_ADDR = 0x803FA428

LOGIC = [
    0x3D80803F,    # lis  r12, 0x803F
    0x618CA428,    # ori  r12, r12, 0xA428
    0x816C0000,    # lwz  r11, 0(r12)
    0x396B0001,    # addi r11, r11, 1
    0x916C0000,    # stw  r11, 0(r12)
]


def watch(h, label: str, seconds: int = 5):
    before = h.read_word(COUNTER_ADDR)
    print(f"\n[{label}] start counter=0x{before:08X}", flush=True)
    for i in range(seconds):
        time.sleep(1.0)
        cur = h.read_word(COUNTER_ADDR)
        delta = (cur - before) & 0xFFFFFFFF
        print(f"  t={i + 1}s  counter=0x{cur:08X}  delta={delta}", flush=True)
    final = h.read_word(COUNTER_ADDR)
    return (final - before) & 0xFFFFFFFF


def cave_payload(cave_addr: int) -> list:
    """Reproduce what Slippi's codehandler writes into the cave: the logic
    body + the displaced original + a branch back to hook+4."""
    body = list(LOGIC) + [HOOK_ORIG]
    branch_back_src = cave_addr + len(body) * 4
    body.append(make_branch(branch_back_src, HOOK_ADDR + 4))
    return body


def main():
    h = Harness()
    h.install_gecko_c2(
        name="diag-savestate-wipe",
        hook_addr=HOOK_ADDR,
        logic_words=LOGIC,
        displaced_orig=HOOK_ORIG,
    )
    try:
        print("staging counter gecko + launching", flush=True)
        h.launch()
        h.hook_dme()
        h._wait_for_cpu_alive(timeout_s=30.0)

        # Locate the cave Slippi's codehandler picked. Read first few words
        # at DEFAULT_CAVE: if they match our LOGIC[0..N], we're at the right
        # cave. If not, the codehandler placed our code somewhere else.
        cave_addr = DEFAULT_CAVE
        first = h.read_word(cave_addr)
        print(f"\nDEFAULT_CAVE 0x{cave_addr:08X} first word: 0x{first:08X}  "
              f"(expected first logic word: 0x{LOGIC[0]:08X})", flush=True)
        # Phase A: gecko should be installed by boot codehandler.
        delta_a = watch(h, "Phase A (post-launch, pre-F2)")

        # Phase B: load savestate via F2.
        print("\n>>> sending F2 to load slot 2 <<<", flush=True)
        h.load_savestate(slot=2, timeout_s=30.0)
        delta_b = watch(h, "Phase B (post-F2, no overlay)")

        # Phase C: try dme overlay of the gecko bytes back into MEM1.
        # Re-derive the cave payload and write it; patch the hook to a branch.
        payload = cave_payload(cave_addr)
        print(f"\n>>> dme overlay: writing {len(payload)} words to cave "
              f"0x{cave_addr:08X} + branch at hook 0x{HOOK_ADDR:08X} <<<",
              flush=True)
        h.write_words(cave_addr, payload)
        h.write_words(HOOK_ADDR, [make_branch(HOOK_ADDR, cave_addr)])
        # Reset our counter so any post-overlay activity is unambiguous.
        h.write_words(COUNTER_ADDR, [0])
        delta_c = watch(h, "Phase C (post-F2, after dme overlay)")

        print("\n" + "=" * 60, flush=True)
        print(f"Phase A delta (pre-F2):                {delta_a}", flush=True)
        print(f"Phase B delta (post-F2, no overlay):   {delta_b}", flush=True)
        print(f"Phase C delta (post-F2, dme overlay):  {delta_c}", flush=True)
        print("=" * 60, flush=True)
        if delta_a == 0:
            print("\n[FATAL] gecko never ran at boot -- something else is "
                  "wrong; install path not working.", flush=True)
            return 1
        if delta_b > 0:
            print("\n[INFO] savestate load did NOT wipe the gecko -- "
                  "Candidate A's failure must be due to something else "
                  "(check raw PADStatus write semantics).", flush=True)
            return 0
        if delta_c > 0:
            print("\n[INFO] savestate WIPED the gecko, but dme overlay "
                  "re-installs it. Harness needs an overlay step after F2.",
                  flush=True)
            return 0
        print("\n[INFO] savestate WIPED the gecko, and dme overlay did NOT "
              "restore execution. Need a new reset strategy (no F2 / new "
              "savestate captured WITH the gecko / etc).", flush=True)
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
