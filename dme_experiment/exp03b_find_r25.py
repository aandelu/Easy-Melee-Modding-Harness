"""Trace where r25 gets set up before 0x803775B8.

The previous run showed the working code at +-50 instructions doesn't load r25
fresh from memory; r25 is set up earlier in the function. Dump a wider window
(roughly the whole function -- bl/blr boundaries) and look for r25 setups.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Harness, runs_dir  # noqa: E402
from exp03_find_padstatus import ppc_disasm  # noqa: E402


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h._wait_for_cpu_alive(timeout_s=30.0)
        import time
        time.sleep(1.0)

        # Look back FAR -- 0x800 bytes (512 instructions) before the hook.
        start = 0x803775B8 - 0x800
        n_instr = 0x600 // 4 + 256  # cover up to right after hook
        bytes_ = h.read_bytes(start, n_instr * 4)
        out_path = os.path.join(runs_dir(), "exp03b_padread_wide_disasm.txt")
        lines = []
        for i in range(n_instr):
            addr = start + i * 4
            word = struct.unpack(">I", bytes_[i*4:(i+1)*4])[0]
            mnem = ppc_disasm(word)
            mark = ""
            # Look for r25 setup instructions
            rt = (word >> 21) & 0x1F
            ra = (word >> 16) & 0x1F
            op = (word >> 26) & 0x3F
            if op in (14, 15, 32) and rt == 25:
                mark = "  <-- sets r25"
            if op == 31 and (word >> 1) & 0x3FF == 444:
                rs = (word >> 21) & 0x1F
                if ra == 25:
                    mark = "  <-- mr r25, ..."
            if addr == 0x803775B8:
                mark = "  <-- HSD_PadRead hook (read PADStatus.buttons)"
            # Look for function-prologue patterns (stwu r1, ...) to find
            # function boundaries
            if word == 0x4E800020:
                mark += "  [blr]"
            lines.append(f"{addr:08X}: {word:08X}  {mnem}{mark}")
        with open(out_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        # Print only the lines marked or with r25 references
        for line in lines:
            if "r25" in line or "blr" in line or "HSD_PadRead" in line or "stwu" in line:
                print(line, flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
