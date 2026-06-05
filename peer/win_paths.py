"""
peer/win_paths.py -- per-machine Windows constants for the Slippi netplay peer.

Edit these on the Windows box to match its install. The Mac never imports this
file; only melee_peer.py (running ON Windows) does.

The Mac harness's equivalent block lives in melee_harness.py (DOLPHIN_HARDLINK,
ISO_PATH, USER_DIR). The values below are the Windows analogs and MUST be
verified during setup -- see ../peer/SETUP_WINDOWS.md "Resolve the unknowns".
"""

# Slippi Dolphin executable (netplay build). Typical install location -- the
# Slippi Launcher unpacks the netplay Dolphin under %APPDATA%\Slippi Launcher.
# Confirm the real path (it may be "Slippi Dolphin.exe" or "Dolphin.exe").
SLIPPI_EXE = (
    r"C:\Users\YOU\AppData\Roaming\Slippi Launcher\netplay\Slippi Dolphin.exe"
)

# Process image name as it appears in `tasklist` -- used to detect/kill Dolphin.
# Often matches the exe basename above. Verify with `tasklist | findstr /i dolphin`.
PROCESS_NAME = "Slippi Dolphin.exe"

# The Melee 1.02 NTSC ISO and (optional) a -u user dir override. Leave USER_DIR
# empty ("") to let Slippi use its default user dir (recommended -- that's where
# the slot-1 direct-connect savestate and Hotkeys.ini live).
ISO_PATH = r"C:\path\to\Super Smash Bros. Melee (v1.02 NTSC).iso"
USER_DIR = ""

# Substring (case-insensitive) matched against visible top-level window titles
# to locate Dolphin's window for focusing. Slippi's render/main window title
# contains "Dolphin". Confirm with the EnumWindows dump in melee_peer.py --debug.
WINDOW_TITLE_SUBSTR = "Dolphin"

# Cold-launch: seconds to wait for the Dolphin window to appear, plus a settle
# delay after it appears before sending hotkeys (let it boot into Melee).
LAUNCH_READY_TIMEOUT_S = 45.0
LAUNCH_SETTLE_S = 6.0
