"""Spin up the netplay-safe JC-shine macro for live play.

Boots Dolphin with meta-flush + candidate_d2 staged, runs the seed_snapshot
round-trip so both geckos survive savestate restore, then polls in a tight
loop: whenever Marth is NOT in a grab state, reset the fire-once counter
to 0 so the next grab fires. This lets you grab Fox repeatedly with Marth
and watch the macro JC-shine each time.

Take control of P1 (Marth) on your controller. P2 (Fox) is driven by the
gecko code. Grab attempts (A, Z, R+A, etc.) put Marth into Catch /
CatchDash / CatchTurn -- all three trigger the macro.

Exit: Ctrl-C in this terminal. Dolphin keeps running and you can close it
from its window when done (or pkill -x Dolphin from another terminal).
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import scenario as sc
import candidate_d2 as cd


def main():
    h = Harness()
    print("staging meta-flush + candidate_d2", flush=True)
    iw.install_meta_flush(h)
    h.install_gecko_c2(
        name=cd.NAME,
        hook_addr=cd.HOOK_ADDR,
        logic_words=cd.LOGIC,
        displaced_orig=cd.DISPLACED_ORIG,
    )

    print("launching Dolphin", flush=True)
    h.launch()
    h.hook_dme()

    print("waiting for CPU + meta-flush ...", flush=True)
    prev = h.read_word(POWERON_COUNT)
    for _ in range(60):
        time.sleep(1.0)
        cur = h.read_word(POWERON_COUNT)
        if cur != prev:
            print(f"  CPU live ({prev} -> {cur})", flush=True)
            break
        prev = cur
    iw.wait_for_meta_flush_alive(h)
    print("meta-flush alive", flush=True)

    print("seeding scenario (slot 2 + gecko overlay) ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    # Zero the fire-once counter so the very first grab arms.
    h.write_bytes(cd.COUNTER_ADDR, b"\x00")

    print()
    print("=" * 62, flush=True)
    print("READY -- the savestate is loaded.", flush=True)
    print()
    print("Take control of P1 (Marth) on your controller.", flush=True)
    print("Grab with Marth -- Fox will jump-cancel into shine.", flush=True)
    print()
    print("Fire-once counter auto-resets when Marth releases grab,", flush=True)
    print("so you can repeat as many times as you want.", flush=True)
    print()
    print("Ctrl-C this terminal to exit (Dolphin stays open).", flush=True)
    print("=" * 62, flush=True)
    print(flush=True)

    last_status = 0.0
    fires = 0
    try:
        while True:
            marth = h.action_state(1) & 0xFFFF
            fox = h.action_state(2) & 0xFFFF
            counter = h.read_bytes(cd.COUNTER_ADDR, 1)[0]

            # When Marth isn't grabbing and the counter has advanced, the
            # last fire is complete -- reset so the next grab arms.
            if not (0xD4 <= marth <= 0xD6) and counter != 0:
                h.write_bytes(cd.COUNTER_ADDR, b"\x00")
                fires += 1
                print(f"[fire #{fires} complete] marth back to 0x{marth:04X}, "
                      f"counter reset; ready for next grab", flush=True)

            # Heartbeat every 10s so you can see the script is alive even
            # if you haven't grabbed yet.
            now = time.time()
            if now - last_status > 10.0:
                print(f"[heartbeat] marth=0x{marth:04X}  fox=0x{fox:04X}  "
                      f"counter=0x{counter:02X}  fires={fires}", flush=True)
                last_status = now

            time.sleep(0.05)
    except KeyboardInterrupt:
        print(f"\nexiting (total fires observed: {fires}). "
              "Dolphin left running.", flush=True)
        # Deliberately do NOT call h.close() -- that would kill Dolphin.


if __name__ == "__main__":
    main()
