"""exp03: find the raw PADStatus base address.

HSD_PadRead at ~0x803775B8 reads `lhz r0, 0(r25)` where r25 = pointer to a
port's PADStatus. We want to find where r25 is loaded so we can write to
PADStatus from dme (the earliest, gecko-equivalent input plane).

Approach: launch Dolphin, dump instructions around 0x803775B8, decode any
`lis r25, ...` / `addi r25, ...` / `lwz r25, ...` to find the static base.
We also walk a few hundred bytes back from the hook to find the function
prologue.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Harness, runs_dir  # noqa: E402


def ppc_disasm(word):
    """Tiny PPC disassembler: just enough to spot r25 setups."""
    op = (word >> 26) & 0x3F
    rt = (word >> 21) & 0x1F
    ra = (word >> 16) & 0x1F
    rb = (word >> 11) & 0x1F
    simm = word & 0xFFFF
    if simm & 0x8000:
        simm_s = simm - 0x10000
    else:
        simm_s = simm
    if op == 14:        # addi
        if ra == 0:
            return f"li      r{rt}, {simm_s:#x}"
        return f"addi    r{rt}, r{ra}, {simm_s:#x}"
    if op == 15:        # addis / lis
        if ra == 0:
            return f"lis     r{rt}, {simm:#x}"
        return f"addis   r{rt}, r{ra}, {simm:#x}"
    if op == 24:        # ori
        return f"ori     r{ra}, r{rt}, {simm:#x}"
    if op == 32:        # lwz
        return f"lwz     r{rt}, {simm_s:#x}(r{ra})"
    if op == 34:        # lbz
        return f"lbz     r{rt}, {simm_s:#x}(r{ra})"
    if op == 40:        # lhz
        return f"lhz     r{rt}, {simm_s:#x}(r{ra})"
    if op == 38:        # stb
        return f"stb     r{rt}, {simm_s:#x}(r{ra})"
    if op == 44:        # sth
        return f"sth     r{rt}, {simm_s:#x}(r{ra})"
    if op == 36:        # stw
        return f"stw     r{rt}, {simm_s:#x}(r{ra})"
    if op == 18:        # b / bl
        li = word & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        bl = "bl" if (word & 1) else "b"
        return f"{bl}      <li={li:+#x}>"
    if op == 16:        # bc
        bd = word & 0xFFFC
        if bd & 0x8000:
            bd -= 0x10000
        bo = (word >> 21) & 0x1F
        bi = (word >> 16) & 0x1F
        return f"bc      bo={bo} bi={bi} bd={bd:+#x}"
    if op == 31:
        xo = (word >> 1) & 0x3FF
        if xo == 444:
            return f"or      r{ra}, r{rt}, r{rb}"
        if xo == 339:
            return f"mfspr   r{rt}"
        if xo == 23:
            return f"lwzx    r{rt}, r{ra}, r{rb}"
        return f"op31_{xo}"
    return f"opcode_{op}"


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        # We don't need to load slot 2; the hook code lives in MEM1 from
        # boot. Just wait for the CPU to be alive and read.
        h._wait_for_cpu_alive(timeout_s=30.0)
        import time
        time.sleep(1.0)

        # Dump 256 bytes around the hook (64 instructions back, 64 forward).
        start = 0x803775B8 - 128
        n_instr = 96
        bytes_ = h.read_bytes(start, n_instr * 4)
        out_path = os.path.join(runs_dir(), "exp03_padread_disasm.txt")
        lines = []
        for i in range(n_instr):
            addr = start + i * 4
            word = struct.unpack(">I", bytes_[i*4:(i+1)*4])[0]
            mark = ""
            if addr == 0x803775B8:
                mark = "  <-- HSD_PadRead hook (read PADStatus.buttons)"
            elif addr == 0x803775C0:
                mark = "  <-- prev hook spot"
            line = f"{addr:08X}: {word:08X}  {ppc_disasm(word)}{mark}"
            lines.append(line)
            print(line, flush=True)

        with open(out_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nWrote {out_path}", flush=True)
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
