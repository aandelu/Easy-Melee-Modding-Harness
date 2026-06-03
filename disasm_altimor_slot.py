"""
disasm_altimor_slot.py -- static disassembly of the code around 0x8034E2AC
(Altimor's Swap X/Z hook = our chosen producer-side L-cancel injection point).

Launches Dolphin, attaches at the MENU (no online needed -- code is identical
regardless of scene), reads the code window in-process (reliable), and
disassembles with capstone so we can see:
  - the exact instruction at 0x8034E2AC (our displaced original)
  - how the button word flows through r0 (which half downstream consumes)
  - the function prologue/epilogue for context

Read-only. Kills Dolphin when done.
"""
import subprocess
import sys
import time

import capstone
import dolphin_memory_engine as dme
from melee_harness import Harness

TARGET = 0x8034E2AC
WIN_START = 0x8034E250          # ~0x60 before
WIN_END = 0x8034E320            # ~0x74 after
SCAN_BACK = 0x8034DE00          # search region for the function prologue


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def read_code(h, start, end):
    n = end - start
    b = h.read_bytes(start, n)
    return b


def main():
    kill_stale()
    h = Harness()
    print("[disasm] launching (menu only, no online) ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    print("[disasm] CPU live; reading code window in-process", flush=True)

    # sanity: read the same word a few times to ensure stable (not torn)
    samples = [h.read_word(TARGET) for _ in range(5)]
    print(f"[disasm] word @ 0x{TARGET:08X} sampled: "
          f"{[hex(s) for s in samples]}", flush=True)

    code = read_code(h, WIN_START, WIN_END)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print(f"\n[disasm] === 0x{WIN_START:08X}..0x{WIN_END:08X} "
          f"(TARGET 0x{TARGET:08X} marked '>>>') ===", flush=True)
    for insn in md.disasm(code, WIN_START):
        mark = ">>>" if insn.address == TARGET else "   "
        print(f"  {mark} 0x{insn.address:08X}: {insn.bytes.hex().upper():<10} "
              f"{insn.mnemonic} {insn.op_str}", flush=True)

    # Scan backward from TARGET for the nearest function prologue (stwu r1,..)
    print(f"\n[disasm] scanning back from 0x{TARGET:08X} for prologue "
          f"(stwu r1 / mflr) ...", flush=True)
    back = read_code(h, SCAN_BACK, TARGET + 4)
    last_prologue = None
    for insn in md.disasm(back, SCAN_BACK):
        if insn.mnemonic == "stwu" and "r1" in insn.op_str.split(",")[0]:
            last_prologue = insn.address
        if insn.mnemonic in ("mflr",) and last_prologue is None:
            pass
    if last_prologue:
        print(f"[disasm] nearest stwu r1 (function start) before target: "
              f"0x{last_prologue:08X} (target is +0x{TARGET-last_prologue:X} in)",
              flush=True)
    else:
        print("[disasm] no stwu r1 found in scan window (leaf fn or larger frame)",
              flush=True)

    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
