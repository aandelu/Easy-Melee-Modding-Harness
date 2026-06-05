"""
online_test.py -- consolidated ONLINE meta-flush test (single run).

Lesson from the false-abort: a single dme read of the scene word can be torn
(Slippi rollback rewrites MEM1 constantly). So we majority-vote every scene
read, and do the whole experiment in ONE process (no fragile re-attach).

Sequence:
  1. launch (meta-flush staged, dormant) + dme attach
  2. enter online: F4, +3s, Enter, +15s  (retry Enter a couple times)
  3. confirm SCENE_ONLINE_IN_GAME (0x0208) by majority vote
  4. ARM meta-flush once (zero-length ping) -- prints a timestamp marker so the
     user can correlate with their screen
  5. monitor scene + frame counter for ~40s

The only memory written is the meta-flush control plane (0x803FA440..0x803FA448,
debug-menu scratch). If that region is in Slippi's desync checksum, the user's
machine desyncs right at the ARM marker. Otherwise meta-flush is viable online.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_test.py
"""
import subprocess
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness
import instr_writer as iw

VK_F4 = 118
VK_RETURN = 36

SCENE_WORD = 0x80479D30
SCENE_ONLINE_IN_GAME = 0x0208
FRAME_PRIMARY = 0x80479D60


def minor_major(word):
    return ((word << 8) | (word >> 24)) & 0xFFFF


def robust_scene(h, samples=21, gap=0.01):
    """Majority-vote the scene to defeat torn reads during rollback."""
    vals = []
    for _ in range(samples):
        try:
            vals.append(minor_major(h.read_word(SCENE_WORD)))
        except Exception:
            vals.append(-1)
        time.sleep(gap)
    c = Counter(vals)
    top, n = c.most_common(1)[0]
    return top, n, samples, c


def kill_stale_dolphins():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin refused to die")


def send_key(pid, vkey, label):
    mh._focus_pid(pid)
    time.sleep(0.3)
    mh._send_key(vkey)
    print(f"[online] sent {label}", flush=True)


def confirm_online(h, pid, max_attempts=4):
    for attempt in range(max_attempts):
        top, n, total, dist = robust_scene(h)
        print(f"[online] scene majority 0x{top:04X} ({n}/{total})  dist={dict(dist)}",
              flush=True)
        if top == SCENE_ONLINE_IN_GAME and n >= total * 0.6:
            return True
        print(f"[online] not yet online (attempt {attempt+1}/{max_attempts}); "
              f"re-sending Enter + waiting", flush=True)
        send_key(pid, VK_RETURN, "Enter (retry)")
        time.sleep(6.0)
    return False


def main():
    kill_stale_dolphins()
    h = Harness()
    iw.install_meta_flush(h)
    print("[online] launching (meta-flush staged, dormant) ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[online] pid {pid}; CPU live", flush=True)

    # enter_online drives the Mac's F4/Enter AND the Windows peer's F1/Enter
    # (over SSH), retrying both sides until scene == 0x0208. connect() returns
    # None if the peer is unreachable -> legacy manual flow. See peer/SETUP_WINDOWS.md.
    from peer import connect as connect_peer
    peer = connect_peer()
    print("[online] entering online (Mac F4/Enter + peer F1/Enter, retry to "
          "0x0208) ...", flush=True)
    if not h.enter_online(peer=peer):
        print("\n[online] COULD NOT CONFIRM online in-game. Leaving Dolphin "
              "running; tell me if your queue dropped and I'll retry.", flush=True)
        return 1
    print("[online] >>> CONFIRMED online in-game (0x0208) <<<", flush=True)

    instr = h.read_word(iw.META_FLUSH_HOOK)
    print(f"[online] meta-flush hook 0x{iw.META_FLUSH_HOOK:08X} = 0x{instr:08X} "
          f"({'branch' if (instr & 0xFC000000)==0x48000000 else 'NOT branch!'})",
          flush=True)

    f_before = h.read_word(FRAME_PRIMARY)
    # ---- THE ARM TEST ----
    print(f"\n[online] ===== ARMING META-FLUSH NOW (t={time.strftime('%H:%M:%S')}) "
          f"===== WATCH YOUR SCREEN", flush=True)
    t0 = time.time()
    armed_ok = False
    try:
        iw.flush_range(h, iw.FLUSH_REQUEST, iw.FLUSH_REQUEST, timeout_s=2.0)
        print(f"[online] gecko cleared magic in {(time.time()-t0)*1000:.0f} ms "
              f"-- meta-flush RESPONDS online", flush=True)
        armed_ok = True
    except TimeoutError as e:
        print(f"[online] meta-flush did NOT respond: {e}", flush=True)

    # ---- monitor ~40s ----
    print("\n[online] monitoring scene + frame for ~40s (watch for desync):",
          flush=True)
    last_f = f_before
    desync_suspected = False
    for i in range(20):
        top, n, total, _ = robust_scene(h, samples=7, gap=0.005)
        f = h.read_word(FRAME_PRIMARY)
        df = f - last_f
        flag = ""
        if top != SCENE_ONLINE_IN_GAME:
            flag = "  <-- scene left online!"
            desync_suspected = True
        if df <= 0:
            flag += "  <-- frame not advancing!"
        print(f"  t+{i*2:2d}s: scene 0x{top:04X}({n}/{total})  frame 0x{f:08X} "
              f"(+{df}){flag}", flush=True)
        last_f = f
        time.sleep(2.0)

    print(f"\n[online] DONE. armed_ok={armed_ok}, my_side_desync_suspected="
          f"{desync_suspected}. Dolphin left running.", flush=True)
    print("[online] >>> Did your screen DESYNC at the ARM marker, or stay synced? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
