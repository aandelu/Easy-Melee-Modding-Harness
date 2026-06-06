"""
peer.py -- Mac-side remote control of the Windows netplay peer.

Online macro testing needs a second machine (a Windows PC) acting as the netplay
peer. The Mac harness already enters online on its own side (F4 = load slot-4
direct-connect savestate, Enter = connect) and confirms via its own scene word
(see Harness.enter_online in melee_harness.py). The ONLY manual step left was
walking to the Windows box to launch Melee and press F1 (load its slot-1
direct-connect savestate, the Mac's name pre-typed) + Enter (connect).

This module triggers that remotely. Windows will NOT deliver synthetic keystrokes
to the game from an SSH-spawned process (Session 0 isolation -- the keys land on a
non-interactive desktop). So the keystrokes are sent by a pre-registered Windows
Scheduled Task that runs in the interactive desktop session; we merely trigger the
task over SSH with `schtasks /run`. See peer/SETUP_WINDOWS.md for the one-time
Windows setup and peer/melee_peer.py for the script the tasks run.

TWO confirmation signals (per the Windows handoff):
  1. peer_status.json -- the peer script writes its own outcome (focused? F1/Enter
     accepted by the OS?) to a JSON file each run; we read it back over SSH. This
     catches the *fixable* peer-side failures: locked desktop, crashed Slippi,
     keystroke rejection. `enter_online`/`restart` poll for a FRESH record (by
     epoch) and return (ok, status).
  2. The Mac's own game scene (0x0208) -- "we're actually in a match", read by
     Harness.enter_online. peer ok:true but scene never 0x0208 => the problem is
     savestate/timing/network, not the peer plumbing.
"""
import json
import subprocess
import time

# --- config: the Windows peer box ------------------------------------------
# An ssh host alias (preferred -- put HostName/User/IdentityFile in ~/.ssh/config)
# or a literal "user@192.168.x.y". Passwordless key auth must be set up so these
# calls are non-interactive (BatchMode below fails fast otherwise).
PEER_SSH_HOST = "winbox"

# Absolute path to peer_status.json ON the Windows box (next to melee_peer.py).
# Read back over SSH with `type`. Machine-specific -- match your peer install.
PEER_STATUS_PATH = r"C:\Users\esash\Desktop\melee 2\peer\peer_status.json"

# Interactive Scheduled Task names registered on the Windows box (no spaces --
# they travel as bare tokens in the remote ssh command). See SETUP_WINDOWS.md.
TASK_ENSURE = "MeleePeer_Ensure"     # launch Slippi Dolphin if not already running
TASK_ENTER = "MeleePeer_Enter"       # ensure running, focus, F1 (load slot 1), Enter
TASK_KILL = "MeleePeer_Kill"         # kill Slippi Dolphin
TASK_RESTART = "MeleePeer_Restart"   # force-kill + relaunch Slippi (recover a wedge)

SSH_TIMEOUT_S = 15.0
# Confirm timeout: must cover a COLD peer launch (win_paths LAUNCH_READY_TIMEOUT_S
# ~45s + settle + F1/Enter ~10s). enter/restart always take >3s, so the integer
# `epoch` in peer_status.json is reliably newer than the pre-trigger read (no
# same-second collision -- which is why only the slow commands are "confirmed").
CONFIRM_TIMEOUT_S = 75.0


def _log(msg):
    print(f"[peer] {msg}", flush=True)


class Peer:
    """Triggers the Windows peer's interactive Scheduled Tasks over SSH and (for
    enter/restart) reads back the peer's own peer_status.json result.

    Duck-typed for Harness.enter_online: enter_online() returns (ok, status) or
    None; restart() likewise. ensure_running()/kill() are fire-and-forget.
    """

    def __init__(self, host: str = PEER_SSH_HOST,
                 ssh_timeout_s: float = SSH_TIMEOUT_S,
                 status_path: str = PEER_STATUS_PATH):
        self.host = host
        self.ssh_timeout_s = ssh_timeout_s
        self.status_path = status_path

    def _ssh(self, *args) -> subprocess.CompletedProcess:
        cmd = ["ssh", "-o", "BatchMode=yes",
               "-o", f"ConnectTimeout={int(self.ssh_timeout_s)}",
               self.host, *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.ssh_timeout_s + 5)

    def _run_task(self, task: str):
        r = self._ssh("schtasks", "/run", "/tn", task)
        if r.returncode != 0:
            raise RuntimeError(
                f"schtasks /run {task} failed (rc={r.returncode}): "
                f"{r.stdout.strip()!r} {r.stderr.strip()!r}")
        _log(f"triggered {task}")

    def read_status(self):
        """Read the peer's last-command result (peer_status.json) over SSH.
        Returns the parsed dict, or None if missing/unreadable."""
        try:
            r = self._ssh("type", f'"{self.status_path}"')
        except Exception as e:
            _log(f"read_status failed: {e}")
            return None
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        try:
            return json.loads(r.stdout)
        except Exception:
            return None

    def _run_task_confirmed(self, task: str, cmd_name: str, timeout_s: float):
        """Trigger `task`, then poll peer_status.json until a FRESH record for
        `cmd_name` appears (epoch newer than the pre-trigger read). Returns
        (ok, status_dict): ok True/False as the peer reported; (None, None) if no
        fresh status arrived within timeout_s (peer slow/unreachable / didn't
        run). Only used for commands that take >3s, so the second-resolution
        epoch is reliably fresh."""
        before_epoch = (self.read_status() or {}).get("epoch")
        self._run_task(task)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.read_status()
            if (st and st.get("cmd") == cmd_name
                    and st.get("epoch") != before_epoch):
                ok = bool(st.get("ok"))
                if ok:
                    _log(f"{cmd_name}: peer reported ok detail={st.get('detail')}")
                else:
                    _log(f"{cmd_name}: peer reported FAILURE "
                         f"error={st.get('error')!r} detail={st.get('detail')}")
                return ok, st
            time.sleep(1.5)
        _log(f"{cmd_name}: no fresh peer status within {timeout_s:.0f}s "
             f"(peer slow/unreachable, or the task didn't run)")
        return None, None

    def ping(self) -> bool:
        """True if the Windows box answers a trivial SSH command. Use to fail
        fast / fall back to the manual flow when the peer is unreachable."""
        try:
            r = self._ssh("echo", "ok")
        except Exception as e:
            _log(f"ping failed: {e}")
            return False
        ok = r.returncode == 0 and "ok" in r.stdout
        if not ok:
            _log(f"ping unexpected (rc={r.returncode}): "
                 f"{r.stdout.strip()!r} {r.stderr.strip()!r}")
        return ok

    def ensure_running(self):
        """Launch Slippi Dolphin on the peer if it isn't already running.
        Fire-and-forget (a warm 'ensure' is sub-second, so it isn't confirmed)."""
        self._run_task(TASK_ENSURE)

    def enter_online(self, confirm: bool = True,
                     timeout_s: float = CONFIRM_TIMEOUT_S):
        """On the peer: ensure running, focus Dolphin, F1 (load slot-1
        direct-connect savestate), wait, Enter (search/connect).

        confirm=True (default): block until the peer writes a fresh result and
        return (ok, status) -- ok is True only if it focused the window AND the
        OS accepted both keystrokes. confirm=False: fire-and-forget, returns
        (None, None)."""
        if not confirm:
            self._run_task(TASK_ENTER)
            return (None, None)
        return self._run_task_confirmed(TASK_ENTER, "enter", timeout_s)

    def kill(self):
        """Kill Slippi Dolphin on the peer. Fire-and-forget."""
        self._run_task(TASK_KILL)

    def restart(self, confirm: bool = True,
                timeout_s: float = CONFIRM_TIMEOUT_S):
        """Force-close and relaunch Slippi Dolphin on the peer to recover a
        wedged/crashed session (kill -> wait until dead -> relaunch -> wait for
        the window). Does NOT re-enter online -- follow with enter_online().

        confirm=True (default): block until the peer reports the relaunch
        finished (window back up) and return (ok, status); confirm=False:
        fire-and-forget, returns (None, None)."""
        if not confirm:
            self._run_task(TASK_RESTART)
            return (None, None)
        return self._run_task_confirmed(TASK_RESTART, "restart", timeout_s)


def connect(host: str = PEER_SSH_HOST):
    """Return a reachable Peer, or None (with a warning) if the Windows box does
    not answer -- so scripts can fall back to the manual Windows flow."""
    p = Peer(host=host)
    if p.ping():
        return p
    _log(f"peer {host!r} unreachable over SSH -- falling back to MANUAL Windows "
         f"entry (launch Melee + F1 + Enter yourself).")
    return None


if __name__ == "__main__":
    import sys
    p = Peer()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    if cmd == "ping":
        print("reachable" if p.ping() else "UNREACHABLE")
    elif cmd == "status":
        print(json.dumps(p.read_status(), indent=2))
    elif cmd == "ensure":
        p.ensure_running()
    elif cmd == "enter":
        ok, st = p.enter_online()
        print(f"enter ok={ok} status={st}")
    elif cmd == "kill":
        p.kill()
    elif cmd == "restart":
        ok, st = p.restart()
        print(f"restart ok={ok} status={st}")
    else:
        print(f"usage: {sys.argv[0]} [ping|status|ensure|enter|kill|restart]",
              file=sys.stderr)
        sys.exit(2)
