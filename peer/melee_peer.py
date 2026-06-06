r"""
melee_peer.py -- Windows netplay-peer driver (runs ON the Windows box).

Reproduces the manual step the user used to do by hand: launch Slippi Dolphin,
press F1 (load this box's slot-1 direct-connect savestate, the Mac's name
pre-typed), press Enter (search/connect).

Triggered by the Mac over SSH via a pre-registered *interactive* Scheduled Task
(see SETUP_WINDOWS.md). Sub-commands:

    python melee_peer.py ensure    # launch Slippi Dolphin if not already running
    python melee_peer.py enter     # ensure running, focus, F1 (load slot 1), Enter
    python melee_peer.py kill      # kill Slippi Dolphin
    python melee_peer.py restart   # force-kill + wait-dead + relaunch (recover a wedge)
    python melee_peer.py debug     # print Slippi process state + visible windows

IMPORTANT -- why a Scheduled Task and not plain SSH:
    Synthetic keystrokes (SendInput) only reach Dolphin when this script runs in
    the *interactive desktop session*. A process spawned directly by OpenSSH runs
    in a non-interactive session (Session 0), so its keystrokes go nowhere. A
    Scheduled Task set to "Run only when user is logged on" runs in the
    interactive session -- that's the bridge. The session must also be UNLOCKED
    (a locked secure-desktop blocks SendInput).

Stdlib only. Logs to melee_peer.log next to this file (Scheduled Tasks launched
with pythonw have no console, so the log file is the only output you'll see).
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import win_paths

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "melee_peer.log")
# Machine-readable result of the LAST command, overwritten each run. The Mac
# reads this (over SSH) to know whether a triggered command actually succeeded,
# rather than inferring it from the game. Always carries an "epoch" so the Mac
# can confirm it's reading a FRESH result (epoch >= the time it fired the task)
# and not a stale one from a previous run.
STATUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "peer_status.json")


def _log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        # The console codec (often cp1252) can't always encode chars that show
        # up in window titles. The log file already captured it -- never let a
        # console-encoding error crash the run; emit a lossy version instead.
        try:
            enc = sys.stdout.encoding or "utf-8"
            print(line.encode(enc, "replace").decode(enc), flush=True)
        except Exception:
            pass


def _write_status(cmd, ok, error=None, detail=None):
    """Overwrite peer_status.json with the outcome of this command run. Written
    atomically (temp + replace) so the Mac never reads a half-written file."""
    rec = {
        "cmd": cmd,
        "ok": bool(ok),
        "error": error,
        "detail": detail or {},
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
    }
    try:
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, STATUS_PATH)
    except Exception as e:
        _log(f"WARNING: could not write status file: {e!r}")


# --- Win32 plumbing ---------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# Scancodes (keyboard set 1). Scancodes are more "real-hardware-like" than vkeys
# and are the reliable path for Dolphin's hotkey reader.
SC_F1 = 0x3B
SC_RETURN = 0x1C

SW_RESTORE = 9

ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


# The real Win32 INPUT union's largest member is MOUSEINPUT. We only ever send
# keyboard events, but the union MUST include MOUSEINPUT so sizeof(INPUT) matches
# what SendInput expects for cbSize (40 on x64, 28 on x86). Declaring only ki
# yields 32 on x64, which SendInput rejects with ERROR_INVALID_PARAMETER (87).
class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT


def _send_scancode(scan: int) -> bool:
    """Press and release a single key by scancode via SendInput. Returns True
    only if the OS accepted BOTH the down and up events (SendInput returned 1).
    Note: this confirms the keystroke entered the input stream, NOT that Dolphin
    consumed it -- the latter is only fully confirmable from game state."""
    extra = ctypes.c_ulong(0)

    def _evt(flags):
        ki = KEYBDINPUT(0, scan, flags, 0, ctypes.cast(ctypes.pointer(extra),
                                                       ULONG_PTR))
        return INPUT(INPUT_KEYBOARD, _INPUTunion(ki=ki))

    n = ctypes.sizeof(INPUT)
    down = _evt(KEYEVENTF_SCANCODE)
    up = _evt(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    ok_down = user32.SendInput(1, ctypes.byref(down), n) == 1
    if not ok_down:
        _log(f"WARNING: SendInput(down 0x{scan:02X}) failed err="
             f"{ctypes.get_last_error()}")
    time.sleep(0.05)
    ok_up = user32.SendInput(1, ctypes.byref(up), n) == 1
    if not ok_up:
        _log(f"WARNING: SendInput(up 0x{scan:02X}) failed err="
             f"{ctypes.get_last_error()}")
    return ok_down and ok_up


# --- window discovery + focus ----------------------------------------------
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _visible_windows():
    """Return [(hwnd, title), ...] for visible, titled top-level windows."""
    out = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value:
            out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def _find_dolphin_hwnd():
    substr = win_paths.WINDOW_TITLE_SUBSTR.lower()
    for hwnd, title in _visible_windows():
        if substr in title.lower():
            return hwnd
    return None


def _focus_hwnd(hwnd) -> bool:
    """Bring `hwnd` to the foreground, defeating Windows' focus-stealing block
    via the AttachThreadInput dance."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    cur_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = [t for t in {fg_tid, tgt_tid} if t and t != cur_tid]
    for t in attached:
        user32.AttachThreadInput(cur_tid, t, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        for t in attached:
            user32.AttachThreadInput(cur_tid, t, False)
    if not ok:
        _log("WARNING: SetForegroundWindow returned 0 (focus may have failed)")
    time.sleep(0.2)
    return ok


# --- process management -----------------------------------------------------
def _dolphin_running() -> bool:
    r = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {win_paths.PROCESS_NAME}"],
        capture_output=True, text=True)
    return win_paths.PROCESS_NAME.lower() in (r.stdout or "").lower()


def _launch():
    args = [win_paths.SLIPPI_EXE, "-e", win_paths.ISO_PATH]
    if win_paths.USER_DIR:
        args += ["-u", win_paths.USER_DIR]
    _log(f"launching: {args}")
    # DETACHED so the child outlives this short-lived task invocation.
    subprocess.Popen(args, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def ensure_running(wait_ready: bool = True):
    """Launch Slippi Dolphin if not running. If wait_ready, block until its
    window appears (+ a settle delay) so hotkeys won't be dropped. Returns the
    Dolphin hwnd (or None if not waiting / not found)."""
    if _dolphin_running():
        _log("Slippi Dolphin already running")
        return _find_dolphin_hwnd()
    _launch()
    if not wait_ready:
        return None
    deadline = time.time() + win_paths.LAUNCH_READY_TIMEOUT_S
    while time.time() < deadline:
        hwnd = _find_dolphin_hwnd()
        if hwnd:
            _log("Dolphin window appeared; settling before hotkeys")
            time.sleep(win_paths.LAUNCH_SETTLE_S)
            return hwnd
        time.sleep(1.0)
    raise RuntimeError("Dolphin window never appeared within "
                       f"{win_paths.LAUNCH_READY_TIMEOUT_S}s of launch")


def _kill():
    r = subprocess.run(["taskkill", "/F", "/IM", win_paths.PROCESS_NAME],
                       capture_output=True, text=True)
    _log(f"taskkill rc={r.returncode}: {(r.stdout or r.stderr).strip()}")


def _wait_until_dead(timeout_s: float = 10.0) -> bool:
    """Poll until Slippi Dolphin is gone from tasklist. taskkill /F returns
    immediately but the process takes a moment to actually exit -- relaunching
    before it's gone risks two instances."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _dolphin_running():
            return True
        time.sleep(0.25)
    return False


# --- commands ---------------------------------------------------------------
def restart():
    """Recovery for a wedged/crashed peer: force-kill Slippi Dolphin, wait for
    it to fully exit, then relaunch + wait for the window. Does NOT re-enter
    online -- the caller (or the harness retry loop) re-sends F1/Enter."""
    _kill()
    if not _wait_until_dead(10.0):
        _log("WARNING: Dolphin still in tasklist 10s after kill; relaunching anyway")
    time.sleep(1.0)
    ensure_running(wait_ready=True)
    _log("restart complete (Slippi relaunched)")


def enter():
    """ensure running -> focus -> F1 (load slot 1) -> wait -> Enter (connect).
    Returns a detail dict; raises if either keystroke was rejected by the OS so
    the exit code / status file reflect a real failure (not a silent warning)."""
    hwnd = ensure_running(wait_ready=True) or _find_dolphin_hwnd()
    if hwnd is None:
        raise RuntimeError("no Dolphin window to focus")
    focused = _focus_hwnd(hwnd)
    time.sleep(0.3)
    f1_ok = _send_scancode(SC_F1)
    _log("sent F1 (load slot-1 direct-connect savestate)")
    time.sleep(3.0)
    _focus_hwnd(hwnd)  # re-assert focus in case the load shifted it
    enter_ok = _send_scancode(SC_RETURN)
    _log("sent Enter (search/connect)")
    detail = {"focused": bool(focused), "f1_sent": f1_ok,
              "enter_sent": enter_ok}
    if not (f1_ok and enter_ok):
        raise RuntimeError(f"keystroke send rejected by OS: {detail}")
    return detail


def debug():
    _log(f"PROCESS_NAME={win_paths.PROCESS_NAME!r} running={_dolphin_running()}")
    _log(f"SLIPPI_EXE={win_paths.SLIPPI_EXE!r} exists="
         f"{os.path.exists(win_paths.SLIPPI_EXE)}")
    _log(f"ISO_PATH={win_paths.ISO_PATH!r} exists="
         f"{os.path.exists(win_paths.ISO_PATH)}")
    _log("visible windows:")
    for hwnd, title in _visible_windows():
        _log(f"    0x{hwnd:08X}  {title!r}")
    hwnd = _find_dolphin_hwnd()
    _log(f"matched Dolphin hwnd (substr {win_paths.WINDOW_TITLE_SUBSTR!r}): "
         f"{'0x%08X' % hwnd if hwnd else None}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "enter"
    _log(f"=== melee_peer {cmd} ===")
    detail = None
    try:
        if cmd == "ensure":
            ensure_running(wait_ready=True)
        elif cmd == "enter":
            detail = enter()
        elif cmd == "kill":
            _kill()
        elif cmd == "restart":
            restart()
        elif cmd == "debug":
            debug()
        else:
            _log(f"unknown command: {cmd!r} (use ensure|enter|kill|restart|debug)")
            _write_status(cmd, ok=False, error="unknown command")
            return 2
    except Exception as e:
        _log(f"ERROR ({cmd}): {e!r}")
        _write_status(cmd, ok=False, error=repr(e), detail=detail)
        return 1
    _write_status(cmd, ok=True, detail=detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
