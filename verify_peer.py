"""
verify_peer.py -- end-to-end smoke test for the autonomous Windows netplay peer.

Proves a Mac script can get BOTH machines into an online match with ZERO physical
interaction on the Windows box. Prints [PASS]/[FAIL] per stage and exits non-zero
on any failure, like the other verify_*.py one-shots.

Prereqs: the Windows box is set up per peer/SETUP_WINDOWS.md (OpenSSH + key auth,
the three MeleePeer_* interactive Scheduled Tasks, logged-in + UNLOCKED desktop)
and PEER_SSH_HOST in peer.py points at it.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 verify_peer.py
"""
import subprocess
import sys
import time

from melee_harness import Harness, SCENE_ONLINE_IN_GAME
import instr_writer as iw
from peer import Peer


def kill_stale_dolphins():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"],
                      capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"],
                                  capture_output=True, text=True).stdout.strip():
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin refused to die")


def main():
    fails = 0
    p = Peer()

    # --- 1. peer reachable over SSH -----------------------------------------
    if p.ping():
        print("[PASS] SSH to Windows peer reachable")
    else:
        print("[FAIL] SSH to Windows peer unreachable -- check "
              "peer/SETUP_WINDOWS.md step 3 (OpenSSH + key auth + PEER_SSH_HOST)")
        return 1   # nothing downstream can work without the peer

    # --- 2. remote trigger + status return channel --------------------------
    try:
        p.kill()  # fast command -> writes a fresh peer_status.json shortly after
        st = None
        for _ in range(8):                 # poll ~8s for the scheduler to run it
            time.sleep(1.0)
            st = p.read_status()
            if isinstance(st, dict) and st.get("cmd") == "kill":
                break
        if isinstance(st, dict) and "epoch" in st:
            print(f"[PASS] peer status channel works "
                  f"(last: cmd={st.get('cmd')} ok={st.get('ok')})")
        else:
            print(f"[FAIL] peer_status.json unreadable (got {st!r}) -- check "
                  f"PEER_STATUS_PATH in peer.py")
            fails += 1
        p.ensure_running()
        print("[PASS] remote launch trigger (kill + ensure_running) accepted")
    except Exception as e:
        print(f"[FAIL] remote trigger/status: {e}")
        fails += 1

    # --- 3. headline: full two-machine online entry from the Mac ------------
    kill_stale_dolphins()
    h = Harness()
    # Stage meta-flush dormant -- keeps Slippi's online codeset intact (same as
    # the proven online_*.py scripts); we never arm it, so it writes nothing.
    iw.install_meta_flush(h)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)

    if h.enter_online(peer=p):
        top, n, total, _ = h.robust_scene()
        if top == SCENE_ONLINE_IN_GAME:
            print(f"[PASS] online match reached with zero physical Windows "
                  f"interaction (scene 0x{top:04X}, {n}/{total} votes)")
        else:
            print(f"[FAIL] enter_online returned True but scene re-reads "
                  f"0x{top:04X} (expected 0x0208)")
            fails += 1
    else:
        print("[FAIL] enter_online could not reach 0x0208 -- check the harness "
              "log above and C:\\...\\peer\\melee_peer.log on Windows")
        fails += 1

    fin = p.read_status()
    if fin:
        print(f"[verify_peer] final peer status: {fin}")
    print("[verify_peer] Dolphin left running (online session alive).")
    if fails:
        print(f"\n{fails} stage(s) FAILED.")
        return 1
    print("\nAll stages PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
