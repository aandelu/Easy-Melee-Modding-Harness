"""Manual test: F2 to load slot 2, then try osascript to send Shift+F1.
Check if GALE01.s01 mtime updates."""
import os
import subprocess
import time

from melee_harness import Harness, _focus_pid

SLOT_1_PATH = (
    "/Users/andrewashman/Library/Application Support/com.project-slippi.dolphin/"
    "netplay/User/StateSaves/GALE01.s01"
)


def slot1_mtime():
    return os.stat(SLOT_1_PATH).st_mtime


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.load_savestate(slot=2, timeout_s=30.0)

        pre = slot1_mtime()
        print(f"slot 1 mtime before save: {pre}", flush=True)
        print("focusing + sending Shift+F1 via osascript ...", flush=True)
        _focus_pid(h._proc.pid)
        time.sleep(0.5)
        script = 'tell application "System Events" to key code 122 using {shift down}'
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True)
        print(f"osascript exit={result.returncode}  stderr={result.stderr!r}",
              flush=True)
        time.sleep(1.5)
        post = slot1_mtime()
        print(f"slot 1 mtime after save:  {post}", flush=True)
        if post > pre:
            print("[PASS] osascript Shift+F1 triggered save", flush=True)
            return 0
        print("[FAIL] no save fired", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    raise SystemExit(main())
