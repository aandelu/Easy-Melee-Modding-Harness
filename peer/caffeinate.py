"""caffeinate.py -- keep this Windows box awake + the display on for the length
of a netplay testing session, so the Mac-driven keystrokes (SendInput) always
land on an UNLOCKED interactive desktop.

Why this exists: SendInput is dropped by a locked secure-desktop. A lock happens
on sleep->resume or via a password-screensaver. Rather than permanently disable
sleep / the login PIN, run this at the START of a testing session and leave the
window open; it prevents sleep + screensaver while it runs (so the desktop never
auto-locks), then RESTORES normal power behavior the moment you close it.

It does NOT disable your login PIN and changes no settings -- it only holds an
"app is busy, stay awake" request for as long as this process is alive (the same
mechanism video players use). Closing the window or Ctrl+C releases it; Windows
also auto-clears the request if the process dies.

    python caffeinate.py        # leave open during the session; Ctrl+C to stop
"""
import ctypes
import time

# SetThreadExecutionState flags (winbase.h).
ES_CONTINUOUS = 0x80000000        # keep the state until the next call
ES_SYSTEM_REQUIRED = 0x00000001   # don't sleep the system
ES_DISPLAY_REQUIRED = 0x00000002  # don't blank the display / start screensaver


def main():
    kernel32 = ctypes.windll.kernel32
    keep_awake = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    if kernel32.SetThreadExecutionState(keep_awake) == 0:
        print("WARNING: SetThreadExecutionState failed; PC may still sleep.")
        return
    print("This PC will stay awake and unlocked while this window is open.")
    print("Leave it open during the testing session.")
    print("Press Ctrl+C (or just close this window) when you're done.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        # Drop the keep-awake request -> normal power behavior resumes.
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print("\nReleased -- PC returns to its normal sleep/lock behavior.")


if __name__ == "__main__":
    main()
