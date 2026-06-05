"""
online_step1_bringup.py  -- ONLINE testing, step 1 (read-only bring-up).

Launches Slippi Dolphin via the harness hardlink (so dme can attach), stages
the meta-flush gecko but leaves it DORMANT (never arms the magic word), gets
into an online match against the user's test account, then does READ-ONLY
observation to confirm:

  1. online launch + dme attach work,
  2. we reach SCENE_ONLINE_IN_GAME (0x208),
  3. the frame counter advances steadily (our side isn't hung),
  4. the meta-flush gecko is installed (instruction at 0x803775C0 is a branch),
     WITHOUT arming it -- so this step makes ZERO game-state writes and cannot
     itself desync.

It deliberately does NOT call seed_snapshot / restore_snapshot / bp (all
offline-only / desync-unsafe) and does NOT close Dolphin on exit -- Dolphin is
left running so later steps can re-attach via dme.hook() to the SAME online
session.

Online-entry sequence (per user): F4, wait ~3s, Enter, wait ~15s.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_step1_bringup.py
"""
import subprocess
import sys
import time

import melee_harness as mh
from melee_harness import Harness
import instr_writer as iw

# macOS virtual keycodes
VK_F4 = 118
VK_RETURN = 36

META_FLUSH_HOOK = iw.META_FLUSH_HOOK     # 0x803775C0
META_FLUSH_ORIG = iw.META_FLUSH_ORIG     # 0x88190002 (vanilla lbz r0,2(r25))

# Scene-id candidates (CLAUDE.md says 0x80489D30 == 0x208 online; getMinorMajor
# in Slippi reads near 0x80479D30). Read both raw so we can see the encoding.
SCENE_CANDIDATES = [0x80479D28, 0x80479D2C, 0x80479D30, 0x80489D28,
                    0x80489D2C, 0x80489D30]
FRAME_PRIMARY = 0x80479D60
FRAME_POWERON = 0x804D7420


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


def send_key_to_dolphin(pid, vkey, label):
    mh._focus_pid(pid)
    time.sleep(0.3)
    mh._send_key(vkey)
    print(f"[step1] sent {label} to Dolphin", flush=True)


def main():
    kill_stale_dolphins()
    h = Harness()
    # Stage meta-flush as a boot-time C2. It merges with Slippi's full Sys
    # codeset (Slippi Online etc.), so online still works. We will NOT arm it.
    iw.install_meta_flush(h)
    print("[step1] launching Dolphin (online-capable; meta-flush staged, dormant)",
          flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[step1] Dolphin pid {pid}; CPU live", flush=True)

    # --- read-only verify meta-flush gecko is installed (branch, not vanilla) -
    instr = h.read_word(META_FLUSH_HOOK)
    is_branch = (instr & 0xFC000000) == 0x48000000
    print(f"[step1] instr @ 0x{META_FLUSH_HOOK:08X} = 0x{instr:08X} "
          f"({'BRANCH (meta-flush installed)' if is_branch else 'NOT a branch'}; "
          f"vanilla would be 0x{META_FLUSH_ORIG:08X})", flush=True)

    # --- get online (auto-drives the Windows peer if reachable) --------------
    # Harness.enter_online does the Mac's F4/Enter AND triggers the Windows
    # peer's F1/Enter over SSH, retrying both sides until scene == 0x0208.
    # peer.connect() returns None if the Windows box is unreachable, in which
    # case enter_online falls back to the legacy manual flow (drive Windows by
    # hand). See peer/SETUP_WINDOWS.md.
    from peer import connect as connect_peer
    peer = connect_peer()
    print("[step1] entering online (Mac F4/Enter + peer F1/Enter, retry to "
          "0x0208) ...", flush=True)
    if not h.enter_online(peer=peer):
        print("[step1] WARNING: could not confirm online in-game; continuing "
              "read-only observation anyway", flush=True)

    # --- observe scene + frame counters --------------------------------------
    print("\n[step1] === scene-id candidates (looking for 0x208 encoding) ===",
          flush=True)
    for addr in SCENE_CANDIDATES:
        try:
            w = h.read_word(addr)
            print(f"  0x{addr:08X} = 0x{w:08X}  "
                  f"(hi16=0x{(w>>16)&0xFFFF:04X} lo16=0x{w&0xFFFF:04X})",
                  flush=True)
        except Exception as e:
            print(f"  0x{addr:08X} = <read error: {e}>", flush=True)

    # --- frame counter advance check (12 samples over ~6s) -------------------
    print("\n[step1] === frame-counter advance (steady = our side running) ===",
          flush=True)
    last_p = last_po = None
    for i in range(12):
        try:
            fp = h.read_word(FRAME_PRIMARY)
            po = h.read_word(FRAME_POWERON)
        except Exception as e:
            print(f"  sample {i}: read error {e}", flush=True)
            time.sleep(0.5)
            continue
        dp = "" if last_p is None else f" (+{fp-last_p})"
        dpo = "" if last_po is None else f" (+{po-last_po})"
        print(f"  sample {i:2d}: primary=0x{fp:08X}{dp}  "
              f"poweron={po}{dpo}", flush=True)
        last_p, last_po = fp, po
        time.sleep(0.5)

    # --- player data pointers ------------------------------------------------
    print("\n[step1] === player data pointers (ports 1-4) ===", flush=True)
    for port in (1, 2, 3, 4):
        try:
            pd = h.player_data_ptr(port)
            print(f"  port {port}: player_data_ptr = "
                  f"{'INVALID' if pd == -1 else f'0x{pd:08X}'}", flush=True)
        except Exception as e:
            print(f"  port {port}: error {e}", flush=True)

    print("\n[step1] DONE. Dolphin left RUNNING (online session alive) for the "
          "next step. dme un-hooked on exit; re-hook in step 2.", flush=True)
    print("[step1] If scene is NOT 0x208 / players invalid: the other machine "
          "may not have been reset -- ask the user, then we can re-send F4/Enter "
          "without relaunching.", flush=True)
    # Intentionally do NOT call h.close() -- keep Dolphin alive.
    try:
        import dolphin_memory_engine as dme
        dme.un_hook()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
