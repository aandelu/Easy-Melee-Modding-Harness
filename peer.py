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

MVP: fire-and-forget. The Mac confirms success by its OWN scene reaching 0x0208
(online in-game => the peer connected). No return channel from Windows is needed.
"""
import subprocess

# --- config: the Windows peer box ------------------------------------------
# An ssh host alias (preferred -- put HostName/User/IdentityFile in ~/.ssh/config)
# or a literal "user@192.168.x.y". Passwordless key auth must be set up so these
# calls are non-interactive (BatchMode below fails fast otherwise).
PEER_SSH_HOST = "winbox"

# Interactive Scheduled Task names registered on the Windows box (no spaces --
# they travel as bare tokens in the remote ssh command). See SETUP_WINDOWS.md.
TASK_ENSURE = "MeleePeer_Ensure"   # launch Slippi Dolphin if not already running
TASK_ENTER = "MeleePeer_Enter"     # ensure running, focus, F1 (load slot 1), Enter
TASK_KILL = "MeleePeer_Kill"       # kill Slippi Dolphin

SSH_TIMEOUT_S = 15.0


def _log(msg):
    print(f"[peer] {msg}", flush=True)


class Peer:
    """Triggers the Windows peer's interactive Scheduled Tasks over SSH.

    Duck-types the interface Harness.enter_online expects: ensure_running() and
    enter_online(). All calls are fire-and-forget (schtasks /run returns as soon
    as the task is launched); confirmation is the Mac harness's own scene read.
    """

    def __init__(self, host: str = PEER_SSH_HOST,
                 ssh_timeout_s: float = SSH_TIMEOUT_S):
        self.host = host
        self.ssh_timeout_s = ssh_timeout_s

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
        """Launch Slippi Dolphin on the peer if it isn't already running."""
        self._run_task(TASK_ENSURE)

    def enter_online(self):
        """On the peer: ensure running, focus Dolphin, F1 (load slot-1
        direct-connect savestate), wait, Enter (search/connect)."""
        self._run_task(TASK_ENTER)

    def kill(self):
        """Kill Slippi Dolphin on the peer (recover a wedged session)."""
        self._run_task(TASK_KILL)


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
    elif cmd == "ensure":
        p.ensure_running()
    elif cmd == "enter":
        p.enter_online()
    elif cmd == "kill":
        p.kill()
    else:
        print(f"usage: {sys.argv[0]} [ping|ensure|enter|kill]", file=sys.stderr)
        sys.exit(2)
