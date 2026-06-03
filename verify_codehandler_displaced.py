"""
verify_codehandler_displaced.py -- does the Slippi codehandler auto-append the
original instruction to a C2 code? (Offline, ~15s, no online needed.)

Theory: the shipped L-cancel breaks input because gecko_c2_lines includes the
displaced original (rlwinm 0x540084BE), AND the codehandler appends it too -> the
rotate runs twice -> button word corrupted. (Idempotent loads like meta-flush's
lbz survive double execution; a rotate does not.)

Test: stage the L-cancel gecko via install_gecko_c2 (codehandler/boot path), launch
offline, follow the branch the codehandler put at 0x8034E2AC into its cave, and
COUNT how many times 0x540084BE appears. 2 = bug confirmed (don't include the
displaced); 1 = theory wrong.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 verify_codehandler_displaced.py
"""
import struct
import subprocess
import sys
import time

import capstone
import keystone
import dolphin_memory_engine as dme
from melee_harness import Harness

HOOK = 0x8034E2AC
DISPLACED = 0x540084BE

CAVE_ASM = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  ldone
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  ldone
    lbz  9, 0(8)
    mulli 9, 9, 0xE90
    lis  6, 0x8045
    ori  6, 6, 0x3130
    add  6, 6, 9
    lwz  6, 0(6)
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  6, 0x2C(6)
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  7, 0x10(6)
    rlwinm 7, 7, 0, 16, 31
    cmpwi 7, 0x41
    blt  ldone
    cmpwi 7, 0x45
    bgt  ldone
    lis  8, 0x8047
    ori  8, 8, 0x9D60
    lwz  8, 0(8)
    li   9, 7
    divw 6, 8, 9
    mulli 6, 6, 7
    subf 8, 6, 8
    cmpwi 8, 0
    bne  ldone
    oris 0, 0, 0x0010
ldone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
"""


def main():
    # TINY body (one nop) so it definitely fits the codehandler cave (the big
    # L-cancel cave didn't install in the harness's minimal setup). gecko_c2_lines
    # appends DISPLACED; if the codehandler ALSO appends the original, DISPLACED
    # shows up twice in the cave.
    logic = [0x60000000]   # nop
    _ = (CAVE_ASM, keystone, struct)

    subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    time.sleep(1.0)
    h = Harness()
    # Stage via the codehandler path EXACTLY like the shipped gecko (logic + displaced).
    h.install_gecko_c2(name="lcancel-displaced-test", hook_addr=HOOK,
                       logic_words=logic, displaced_orig=DISPLACED)
    print("[v] launching offline ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)

    instr = h.read_word(HOOK)
    print(f"[v] 0x{HOOK:08X} = 0x{instr:08X}", flush=True)
    if (instr & 0xFC000000) != 0x48000000:
        print("[v] not a branch -- codehandler didn't install at menu? abort", flush=True)
        h.close(); return 1
    # decode branch target (b = 0x48, signed 26-bit)
    off = instr & 0x03FFFFFC
    if off & 0x02000000:
        off -= 0x04000000
    cave = (HOOK + off) & 0xFFFFFFFF
    print(f"[v] codehandler cave @ 0x{cave:08X}", flush=True)

    n = len(logic) + 8          # logic + a few extra to catch appended original + branch
    data = h.read_bytes(cave, n * 4)
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    count_disp = 0
    print(f"[v] === cave tail (looking for {DISPLACED:#010x} = the displaced rlwinm) ===",
          flush=True)
    for i in md.disasm(data, cave):
        word = int.from_bytes(i.bytes, "big")
        mark = "  <== DISPLACED" if word == DISPLACED else ""
        if word == DISPLACED:
            count_disp += 1
        # only print the tail region (last ~12) to keep it short
        print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}{mark}",
              flush=True)

    print(f"\n[v] 0x{DISPLACED:08X} appears {count_disp} time(s) in the cave.", flush=True)
    if count_disp >= 2:
        print("[v] >>> BUG CONFIRMED: displaced runs TWICE. Shipped gecko must NOT "
              "include the displaced (codehandler appends it). <<<", flush=True)
    elif count_disp == 1:
        print("[v] displaced appears once -- theory wrong; corruption is elsewhere.",
              flush=True)
    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
