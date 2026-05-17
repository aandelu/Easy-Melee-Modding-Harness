"""
auto_lcancel/find_lcancel_check.py

Boot Dolphin + slot 2, then scan MEM1 for the engine code that reads
Player Data +0x680 (the L/R press timer) and answers:

  1. Where exactly is the L-cancel check?
  2. What's the order of (timer-update / landing-check / button-read) within
     a single frame?

We need to boot first because the game's .text section is only mapped into
MEM1 after the ISO has booted into a live game state. Reading code memory
before that returns garbage / float-like noise.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/find_lcancel_check.py

No side effects to game state -- the rig hook isn't installed; we only
read code memory.
"""
import os
import struct
import subprocess
import sys
import time

import capstone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import Harness, MEM1_BASE, MEM1_SIZE
import instr_writer as iw

OFF_LR_TIMER = 0x0680
OFF_Z_TIMER = 0x067F
OFF_AT_TIMER = 0x0678
OFF_AERIAL_TIMER = 0x065C       # not actually a timer; just a sanity test

# Known landing-related references documented in Char_Data_Offsets.csv 0x2358:
#   "Read upon landing at 80096d68 and compared at 800d5db0."
DOC_LANDING_SITES = [
    ("act-out compare (old)", 0x80093C94),
    ("act-out compare (old)", 0x80093CD0),
    ("act-out read on landing", 0x80096D68),
    ("act-out compare", 0x800D5DB0),
]


def kill_stale_dolphins():
    r = subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    if r.returncode == 0:
        for _ in range(40):
            p = subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                               text=True)
            if not p.stdout.strip():
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin refused to die within 10s")


def disasm_one(word, addr):
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    md.detail = False
    raw = struct.pack(">I", word)
    for ins in md.disasm(raw, addr, count=1):
        return f"{ins.mnemonic} {ins.op_str}"
    return None


def disasm_chunk(chunk, addr):
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    md.detail = False
    return [(ins.address, ins.mnemonic, ins.op_str)
            for ins in md.disasm(bytes(chunk), addr)]


def find_load_store_at_offset(mem, target_off, text_lo, text_hi):
    """Yield (addr, instr_word, mnemonic, op_str) for every aligned PPC
    load/store whose 16-bit displacement equals target_off and which lives in
    [text_lo, text_hi)."""
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    md.detail = False
    hi = (target_off >> 8) & 0xFF
    lo = target_off & 0xFF
    base = MEM1_BASE
    start = max(0, text_lo - base)
    end = min(len(mem), text_hi - base)
    # The displacement field is the LOW 16 bits, i.e. bytes 2 and 3 of the
    # big-endian 4-byte instruction. Prefilter on those.
    i = start
    while i < end - 3:
        if mem[i+2] == hi and mem[i+3] == lo:
            raw = bytes(mem[i:i+4])
            addr = base + i
            for ins in md.disasm(raw, addr, count=1):
                if ins.mnemonic in ("lbz", "lhz", "lha", "lwz",
                                    "stb", "sth", "stw",
                                    "lbzu", "lhzu", "lhau", "lwzu",
                                    "stbu", "sthu", "stwu",
                                    "lfs", "lfd", "stfs", "stfd"):
                    yield (addr, struct.unpack(">I", raw)[0],
                           ins.mnemonic, ins.op_str)
        i += 4


def context(mem, addr, n_before=8, n_after=14):
    base = MEM1_BASE
    start_off = max(0, addr - base - n_before * 4)
    n = n_before + n_after + 1
    chunk = mem[start_off:start_off + n * 4]
    out = []
    for (a, mn, op) in disasm_chunk(chunk, base + start_off):
        mark = " <-- HIT" if a == addr else ""
        out.append(f"      0x{a:08X}  {mn:8s} {op}{mark}")
    return out


def main():
    kill_stale_dolphins()
    h = Harness()
    iw.install_meta_flush(h)
    print("[find] launching Dolphin + seeding slot 2 ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    h.seed_snapshot(timeout_s=60.0)
    print("[find] in-game. Reading MEM1 ...", flush=True)
    mem = bytearray(h.read_bytes(MEM1_BASE, MEM1_SIZE))
    print(f"[find] read {len(mem)} bytes", flush=True)

    # Sanity: read 0x803775B8 -- should be a branch into our cave (rig hook
    # NOT installed yet in this script, so it should be vanilla 0xA0190000).
    w = struct.unpack(">I", mem[0x803775B8 - MEM1_BASE:0x803775B8 - MEM1_BASE + 4])[0]
    print(f"[find] sanity: 0x803775B8 word = 0x{w:08X}  "
          f"({disasm_one(w, 0x803775B8)})", flush=True)
    if w != 0xA0190000:
        print(f"[find] WARNING: expected 0xA0190000 (lhz r0, 0(r25)); "
              "got something else -- code section may not be loaded as "
              "expected.", flush=True)

    # ---- 1. Look up the documented landing sites ----
    print("\n" + "=" * 70, flush=True)
    print("Documented landing-related sites (from Char_Data_Offsets 0x2358):",
          flush=True)
    print("=" * 70, flush=True)
    for label, addr in DOC_LANDING_SITES:
        off = addr - MEM1_BASE
        chunk = mem[off:off+4]
        word = struct.unpack(">I", chunk)[0]
        print(f"\n  {label} @ 0x{addr:08X}  word=0x{word:08X}", flush=True)
        for line in context(mem, addr, n_before=4, n_after=12):
            print(line, flush=True)

    # ---- 2. Scan code section for refs to offset 0x680 ----
    print("\n" + "=" * 70, flush=True)
    print(f"Scanning code section for load/store at offset 0x{OFF_LR_TIMER:03X} "
          f"(L/R press timer):", flush=True)
    print("=" * 70, flush=True)
    text_lo, text_hi = 0x80003000, 0x80400000
    hits = list(find_load_store_at_offset(mem, OFF_LR_TIMER, text_lo, text_hi))
    print(f"  found {len(hits)} hit(s)", flush=True)
    for addr, word, mn, op in hits[:40]:
        print(f"\n  HIT 0x{addr:08X}  {mn} {op}", flush=True)
        for line in context(mem, addr, n_before=6, n_after=10):
            print(line, flush=True)

    # ---- 3. Cross-check: 0x67F and 0x678 (Z and analog) ----
    for label, off in [("Z press timer (0x67F)", OFF_Z_TIMER),
                       ("analog trigger timer (0x678)", OFF_AT_TIMER)]:
        print("\n" + "=" * 70, flush=True)
        print(f"Cross-check: refs to offset 0x{off:03X} ({label}):", flush=True)
        print("=" * 70, flush=True)
        hits = list(find_load_store_at_offset(mem, off, text_lo, text_hi))
        for addr, word, mn, op in hits[:8]:
            print(f"  0x{addr:08X}  {mn} {op}", flush=True)

    # ---- 4. Compare against PlCo constant for the 7-frame window ----
    # PlCo 0xA0C4 = 7 lives somewhere -- find any "cmpwi rX, 7" instruction
    # near a 0x680 load. PPC `cmpwi rA, 7` = 0x2C 0a 00 07 with rA=a.
    # Scan for the `cmpwi rX, 7` byte pattern near each 0x680 hit.
    print("\n" + "=" * 70, flush=True)
    print("Searching near each 0x680 hit for `cmpwi rX, 7` (the 7-frame window):",
          flush=True)
    print("=" * 70, flush=True)
    md = capstone.Cs(capstone.CS_ARCH_PPC,
                     capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    md.detail = False
    for addr, word, mn, op in hits if hits else []:
        off = addr - MEM1_BASE
        chunk = mem[max(0, off - 32):off + 64]
        for ins in md.disasm(bytes(chunk), MEM1_BASE + max(0, off - 32)):
            if ins.mnemonic == "cmpwi" and ", 7" in ins.op_str.replace(" ", ""):
                print(f"  near 0x{addr:08X}: 0x{ins.address:08X}  cmpwi {ins.op_str}",
                      flush=True)

    print("\n[find] done. Leaving Dolphin running.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
