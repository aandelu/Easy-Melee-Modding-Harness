"""observer.py -- shared bring-up + trace formatting for observer scripts.

Every observation session used to hand-roll this boilerplate, and two of the
re-rolls caused real failures (ASDI session, 2026-07-26):

- floats printed to 2 decimals hid a ~1e-4 difference; a gate built on the
  inferred "exact equality" rejected 100% of hits  -> ffmt() always shows
  the raw bits next to the value.
- Player Data pointers cached at startup went stale after a death/respawn
  and contaminated a run  -> players() re-resolves on every call; never
  cache its result across frames.

Usage:
    import observer
    h = observer.bring_up()                 # or bring_up(geckos=[{...}])
    while True:
        for port, pd in observer.players(h).items():
            y = h.read_word(pd + 0xB4)
            print(port, observer.ffmt(y))
"""
import struct
import subprocess
import time

from melee_harness import Harness


def ffmt(word):
    """Format a raw 32-bit float word as value AND bits: '-1.0000 (0xBF800000)'.

    Always log floats through this when a payload might compare them --
    rounded output is how the ASDI exact-compare bug was born.
    """
    word &= 0xFFFFFFFF
    val = struct.unpack(">f", struct.pack(">I", word))[0]
    return f"{val:+.4f} (0x{word:08X})"


def kill_stale_dolphin(timeout_s=10.0):
    """pkill -9 -x Dolphin and poll pgrep until empty (REFERENCE.md §5.2).

    Note this only clears Dolphin -- a prior harness/probe *Python* still
    running holds the dme attach and must be killed separately (hook_dme
    detects that case and names the pid).
    """
    subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not subprocess.run(["pgrep", "-x", "Dolphin"],
                              capture_output=True, text=True).stdout.strip():
            return
        time.sleep(0.3)
    raise RuntimeError(
        "Dolphin would not die -- an unkillable kernel-UE corpse needs a "
        "reboot (REFERENCE.md §5.2)")


def bring_up(geckos=None, meta_flush=True, timeout_s=60.0):
    """Standard one-process bring-up: kill stale Dolphin, stage geckos,
    launch, hook dme, seed_snapshot. Returns the live Harness.

    meta_flush=True stages the meta-flush gecko FIRST -- required whenever
    any other gecko is staged (seed_snapshot wedges the CPU without it) and
    harmless otherwise; it also enables runtime write_instrs iteration.
    geckos: optional list of install_gecko_c2 kwargs dicts.
    """
    kill_stale_dolphin()
    h = Harness()
    if meta_flush:
        import instr_writer
        instr_writer.install_meta_flush(h)
    for g in geckos or []:
        h.install_gecko_c2(**g)
    h.launch()
    h.hook_dme()
    h.seed_snapshot(timeout_s=timeout_s)
    return h


def players(h):
    """Fresh {port: player_data_ptr} for every port that resolves right now.

    Ports are 1-indexed (P1=1). Call this EVERY frame you observe -- a death
    and respawn moves Player Data, so a cached pointer silently reads the
    wrong (or freed) struct.
    """
    out = {}
    for port in range(1, 5):
        pd = h.player_data_ptr(port)
        if pd != -1:
            out[port] = pd
    return out


if __name__ == "__main__":
    # Self-check (no Dolphin needed).
    assert ffmt(0xBF800000) == "-1.0000 (0xBF800000)"
    # the bug class ffmt guards against: floats that print alike but differ
    a, b = 0x42C80000, 0x42C80001
    assert ffmt(a)[:8] == ffmt(b)[:8] and ffmt(a) != ffmt(b)
    try:
        Harness().entity_ptr(0)
        raise SystemExit("FAIL: 0-indexed port not rejected")
    except ValueError:
        pass
    print("[PASS] observer self-check")
