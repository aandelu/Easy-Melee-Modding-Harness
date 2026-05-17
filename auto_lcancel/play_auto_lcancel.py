"""
Live-play driver for the auto-L-cancel macro.

Boots Dolphin with the meta-flush gecko staged, loads slot 2, installs the
auto-L-cancel hook (port 2 / Fox) at runtime, then sits idle so you can play.

Take control of P2 (Fox) on your controller. Jump and do aerials however
you like -- whenever Fox is in an aerial state (NAIR/FAIR/BAIR/UAIR/DAIR)
the hook pulses the L bit every other frame, so any landing during an
aerial L-cancels automatically.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/play_auto_lcancel.py

Exit: Ctrl-C. Dolphin keeps running.
"""
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import Harness
import instr_writer as iw

sys.path.insert(0, HERE)
import auto_lcancel as macro

OFF_ACTION_STATE = 0x0010
TARGET_PORT = 2


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


def main():
    kill_stale_dolphins()

    h = Harness()
    iw.install_meta_flush(h)
    print("[play] launching Dolphin ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[play] meta-flush alive", flush=True)

    print("[play] loading slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print(f"[play] P{TARGET_PORT} player data invalid -- abort", flush=True)
        return 1

    # Install AFTER seed_snapshot (slot 2 load wipes pre-installed patches).
    info = macro.install(h, port=TARGET_PORT)
    print(f"[play] auto-L-cancel installed: hook=0x{info['hook']:08X} "
          f"cave=0x{info['cave']:08X} ({info['payload_len']} words)", flush=True)

    print()
    print("=" * 64, flush=True)
    print("READY -- take control of P2 (Fox) on your controller.", flush=True)
    print("Do aerials however you like; landing-aerials will L-cancel", flush=True)
    print("automatically as long as the action state is in 0x41..0x45.", flush=True)
    print()
    print("Ctrl-C in this terminal to exit (Dolphin stays open).", flush=True)
    print("=" * 64, flush=True)

    try:
        last_print = time.time()
        while True:
            a = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
            now = time.time()
            if now - last_print > 30.0:
                print(f"[play] heartbeat -- P{TARGET_PORT} state=0x{a:04X}",
                      flush=True)
                last_print = now
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[play] exiting -- Dolphin left running.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
