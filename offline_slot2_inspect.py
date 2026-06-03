"""
offline_slot2_inspect.py -- READ-ONLY inspection of the slot-2 savestate.

The discovery probe drove Fox off the stage before I ever saw his resting state.
This script injects NOTHING: it brings up the harness, seeds slot 2, then watches
both ports' action state / facing / position for a few seconds so we can see where
Fox actually starts and whether he's grounded. Use this to design a self-drive
that keeps him on the stage.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_slot2_inspect.py
"""
import struct
import subprocess
import sys
import time

from melee_harness import Harness
import instr_writer as iw

OFF_ACTION_STATE = 0x10
OFF_FACING = 0x2C       # float (empirically confirmed: +1 right / -1 left)
OFF_POS_X  = 0xB0       # float, + right
OFF_POS_Y  = 0xB4       # float

STATE_NAMES = {
    0x000E: "Wait", 0x0012: "Turn", 0x0013: "TurnRun", 0x0014: "Dash",
    0x0015: "Run", 0x0016: "RunDirect", 0x0017: "RunBrake", 0x001D: "Fall",
    0x0027: "Squat", 0x0028: "SquatWait", 0x0029: "SquatRv", 0x0000: "(0/dead)",
}


def sname(st):
    return STATE_NAMES.get(st, f"0x{st:04X}")


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"],
                      capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def rf(h, pd, off):
    try:
        return struct.unpack(">f", h.read_bytes(pd + off, 4))[0]
    except Exception:
        return float("nan")


def main():
    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[in] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[in] seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    for port in (1, 2):
        pd = h.player_data_ptr(port)
        print(f"[in] P{port} pd = {('0x%08X' % pd) if pd != -1 else 'INVALID'}",
              flush=True)

    print("\n  watching 90 frames, NO input injected:", flush=True)
    print("  frame |        P1 (state  face   posX     posY) "
          "|        P2 (state  face   posX     posY)", flush=True)
    for i in range(90):
        row = f"  {i:5d} |"
        for port in (1, 2):
            pd = h.player_data_ptr(port)
            if pd == -1:
                row += "  P%d INVALID                          |" % port
                continue
            st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
            row += (f"  {sname(st):>9} {rf(h,pd,OFF_FACING):+.0f}  "
                    f"{rf(h,pd,OFF_POS_X):+8.1f} {rf(h,pd,OFF_POS_Y):+8.1f} |")
        if i % 6 == 0 or i >= 84:
            print(row, flush=True)
        h.wait_frames(1)

    print("\n[in] done. Leaving Dolphin running.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
