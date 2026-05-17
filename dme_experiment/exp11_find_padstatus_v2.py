"""exp11: try to discover the raw PADStatus base address.

Two approaches:

A) Disassemble around 0x80347364 (the bl target from HSD_PadRead's
   prologue) to see if it sets up r25 from a fixed pointer.

B) Empirical scan: look for the 4 known signatures of unpressed
   PADStatus in MEM1: buttons halfword in {0x0000, 0x0080, 0x8080}
   followed by likely-stick bytes. Find arrays of 4 such entries
   with stride 12 (PADStatus size).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Harness, runs_dir  # noqa: E402
from exp03_find_padstatus import ppc_disasm  # noqa: E402


def disassemble_function(h, start, n=64, out_path=None):
    """Dump n instructions starting at `start`."""
    bytes_ = h.read_bytes(start, n * 4)
    lines = []
    for i in range(n):
        addr = start + i * 4
        word = struct.unpack(">I", bytes_[i*4:(i+1)*4])[0]
        line = f"{addr:08X}: {word:08X}  {ppc_disasm(word)}"
        lines.append(line)
    if out_path:
        with open(out_path, "w") as f:
            f.write("\n".join(lines) + "\n")
    return lines


def scan_for_padstatus(h):
    """Scan MEM1 for arrays of 4 PADStatus-like structs (12 bytes apart).

    Heuristic: unpressed PADStatus has buttons halfword in a small set
    (0x0000, 0x0080) and stickX/stickY bytes near origin (0x80 ish or
    small offsets). Search for 4 consecutive PADStatus-like entries.
    """
    # Read MEM1 (24MB). This is slow but only done once.
    print("reading MEM1 (24MB) for scan...", flush=True)
    mem = h.read_bytes(0x80000000, 0x1800000)
    print(f"got {len(mem)} bytes", flush=True)

    def looks_like_padstatus(off):
        """Check 4 entries at off, off+12, off+24, off+36 are unpressed."""
        for i in range(4):
            o = off + i * 12
            if o + 12 > len(mem):
                return False
            btn = struct.unpack(">H", mem[o:o+2])[0]
            stx = mem[o+2]
            sty = mem[o+3]
            # Buttons typically 0 (no press) or 0x0080 (origin bit).
            if btn not in (0x0000, 0x0080):
                return False
            # stick X / Y should be near 0 (origin) for idle controller.
            # Acceptable: -8..8 signed (-128..127 byte range).
            sx = stx - 256 if stx > 127 else stx
            sy = sty - 256 if sty > 127 else sty
            if abs(sx) > 16 or abs(sy) > 16:
                return False
        return True

    candidates = []
    # PADStatus structs typically aligned to 4 bytes
    for off in range(0, len(mem) - 48, 4):
        if looks_like_padstatus(off):
            addr = 0x80000000 + off
            candidates.append(addr)
    print(f"found {len(candidates)} candidate PADStatus arrays")
    for addr in candidates[:30]:
        # Dump first 48 bytes (4 entries) of each
        print(f"  0x{addr:08X}:", end=" ")
        for i in range(4):
            o = addr - 0x80000000 + i * 12
            btn = struct.unpack(">H", mem[o:o+2])[0]
            stx = mem[o+2]
            sty = mem[o+3]
            print(f"[btn=0x{btn:04X} sx={stx:02X} sy={sty:02X}]", end=" ")
        print()
    return candidates


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h._wait_for_cpu_alive(timeout_s=30.0)
        import time
        time.sleep(2.0)

        # Need to be in-game so PADStatus is being populated
        h.seed_snapshot()

        print("\n=== Disassemble 0x80347364 (bl target from PadRead) ===")
        lines = disassemble_function(h, 0x80347364, n=64,
                                     out_path=os.path.join(
                                         runs_dir(), "exp11_helper_disasm.txt"))
        for line in lines:
            print(line)

        print("\n=== Scan MEM1 for PADStatus arrays ===")
        candidates = scan_for_padstatus(h)

        with open(os.path.join(runs_dir(), "exp11_padstatus_candidates.txt"),
                  "w") as f:
            f.write(f"# {len(candidates)} candidate PADStatus arrays\n")
            for addr in candidates:
                f.write(f"0x{addr:08X}\n")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
