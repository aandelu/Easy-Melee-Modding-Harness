"""
disasm_padread.py -- wide static disasm of PAD_Read around 0x8034E2AC to locate
where the analog trigger byte (pad-report +0x6 = analog L) is stored, so we can
pick a producer-side hook for analog-L injection (the 0x8034E2AC hook only sets
the button halfword; the trigger byte is written separately/after).

Launches Dolphin to the menu (code is scene-independent), reads the window
in-process, capstone-disassembles, and flags every store (stb/sth/stw) so we can
see the +6/+7 trigger writes and which register holds the pad-report base.

Read-only. Kills Dolphin when done.
"""
import subprocess
import sys
import time

import capstone
from melee_harness import Harness

TARGET = 0x8034E2AC
WIN_START = 0x8034E1C0
WIN_END = 0x8034E640


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def main():
    kill_stale()
    h = Harness()
    print("[disasm] launching (menu only) ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    print("[disasm] CPU live; reading PAD_Read window", flush=True)

    samples = [h.read_word(TARGET) for _ in range(3)]
    print(f"[disasm] word @ 0x{TARGET:08X}: {[hex(s) for s in samples]} "
          f"(expect 0x540084BE rlwinm)", flush=True)

    code = h.read_bytes(WIN_START, WIN_END - WIN_START)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    md.detail = True
    print(f"\n[disasm] === 0x{WIN_START:08X}..0x{WIN_END:08X} "
          f"(TARGET '>>>', stores '*ST*') ===", flush=True)
    for insn in md.disasm(code, WIN_START):
        mark = ">>>" if insn.address == TARGET else "   "
        st = "*ST*" if insn.mnemonic.startswith("st") else "    "
        print(f"  {mark}{st} 0x{insn.address:08X}: {insn.bytes.hex().upper():<10} "
              f"{insn.mnemonic} {insn.op_str}", flush=True)

    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
