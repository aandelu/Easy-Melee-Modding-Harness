"""
disasm_lcancel_analog.py -- read-only disasm to plan the ANALOG-L L-cancel.

Disassembles four regions from a live (menu) Dolphin (code is scene-independent):
  1. L-cancel detection (~0x8008E4A8, wide) -- THE key question: does it read an
     ANALOG trigger timer (0x672/0x675/0x678) and/or the Z timer (0x67F), not just
     the digital L/R timer (0x680)? And is the test "frames since pressed <= window"
     (so a HELD analog L would keep the timer low only on the press edge) vs a
     "currently pressed" check (so HELD analog L always satisfies it)? This decides
     whether held analog L can L-cancel without pulsing.
  2. PAD_Read analog->digital conversion (~0x8034E244) -- the >=0xAA->digital-L step.
  3. PAD_Read after our hook (0x8034E2AC..) -- where the analog L byte (report +6)
     is written, to find a producer-side analog injection point.
  4. HSD_PadRead (0x803775B8 region) -- the consumer pad struct layout, to find the
     analog-L offset for OFFLINE injection via (r25).

Read-only. Kills Dolphin when done.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 disasm_lcancel_analog.py
"""
import subprocess
import sys
import time

import capstone
from melee_harness import Harness

REGIONS = [
    ("L-cancel detection", 0x8008E460, 0x8008E540),
    ("PAD_Read analog->digital + our hook", 0x8034E220, 0x8034E2C0),
    ("PAD_Read after hook (analog byte writes)", 0x8034E2B0, 0x8034E5C0),
    ("HSD_PadRead pad struct", 0x803775A0, 0x80377660),
]

# Player-Data offsets to annotate when they appear as a load/store displacement.
ANNOT = {
    0x650: "analogTrigger(processed)", 0x65C: "buttons(processed)",
    0x672: "framesAnalogLightPressed", 0x675: "framesAnalogHardPressed",
    0x678: "framesSinceAnalogMoved", 0x67C: "framesSinceA", 0x67D: "framesSinceB",
    0x67E: "framesSinceXY", 0x67F: "framesSinceZ", 0x680: "framesSinceLR",
    0x10: "actionState", 0x894: "actionFrame", 0x195C: "hitlag",
    0x2354: "landingLagDiv", 0x25FF: "LCancelStatus",
}


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
    print("[da] launching (menu only) ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    print("[da] CPU live; disassembling\n", flush=True)
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)

    for name, start, end in REGIONS:
        try:
            code = h.read_bytes(start, end - start)
        except Exception as e:
            print(f"[da] read failed for {name}: {e}", flush=True); continue
        print(f"\n========== {name}  0x{start:08X}..0x{end:08X} ==========", flush=True)
        for insn in md.disasm(code, start):
            st = "*ST*" if insn.mnemonic.startswith("st") else (
                 "*LD*" if insn.mnemonic.startswith("l") else "    ")
            # annotate known player-data displacements
            note = ""
            for off, label in ANNOT.items():
                tok = hex(off).replace("0x", "")
                if (f"0x{tok}(" in insn.op_str.lower() or
                        f", 0x{tok}" in insn.op_str.lower() or
                        f" {off}(" in insn.op_str):
                    note = f"   ; {label} (0x{off:X})"
            print(f"  {st} 0x{insn.address:08X}: {insn.bytes.hex().upper():<10} "
                  f"{insn.mnemonic} {insn.op_str}{note}", flush=True)

    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
