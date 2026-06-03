"""
online_beforeafter.py -- single-process before/after hook census.

All reads happen in the SAME process as launch (re-attach is unreliable here).
Reads a set of hook addresses at the MENU (pre-online) and again ONLINE, so we
can tell, reliably:
  - does our meta-flush boot gecko install at boot?  (menu = branch?)
  - does it survive entering online?                 (online = branch or vanilla?)
  - do Slippi's own required hooks look installed at menu vs online?
  - does the user-INI Extract Menu Info code survive online?

This distinguishes "online strips custom/user codes" from "never installed".
Read-only; no writes.
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


def mm(word):
    return ((word << 8) | (word >> 24)) & 0xFFFF


def maj(h, addr, n=11):
    vals = []
    for _ in range(n):
        try:
            vals.append(h.read_word(addr))
        except Exception:
            vals.append(-1)
        time.sleep(0.008)
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


# addr, label, known vanilla opcode (or None)
HOOKS = [
    (0x803775C0, "meta-flush (ours)",          0x88190002),
    (0x803775B8, "auto_lcancel slot (ours)",   0xA0190000),
    (0x801A4FA4, "Extract Menu Info (user)",   None),
    (0x80376A20, "SkipNewInputFetch (Slippi)", None),
    (0x80376A24, "ApplyInGameDelay (Slippi)",  None),
    (0x80376A28, "TriggerSendInput (Slippi)",  None),
]


def state(word, vanilla):
    if (word & 0xFC000000) == 0x48000000:
        return "BRANCH"
    if vanilla is not None and word == vanilla:
        return "VANILLA"
    return "other"


def census(h, tag):
    print(f"\n--- hook census [{tag}] ---", flush=True)
    for addr, label, vanilla in HOOKS:
        w, c, n = maj(h, addr)
        print(f"  0x{addr:08X}  0x{w:08X}  {state(w, vanilla):<8} "
              f"{label} ({c}/{n})", flush=True)


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def scene_majority(h, n=21):
    vals = [mm(h.read_word(SCENE_WORD)) for _ in range(n)]
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


def main():
    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[ba] launching ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[ba] pid {pid}; CPU live (at menu)", flush=True)

    top, c, n = scene_majority(h)
    print(f"[ba] menu scene minorMajor 0x{top:04X} ({c}/{n})", flush=True)
    census(h, "MENU / pre-online")

    print("\n[ba] entering online: F4, +3s, Enter, +15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)

    # confirm online (retry Enter)
    online = False
    for attempt in range(4):
        top, c, n = scene_majority(h)
        print(f"[ba] scene 0x{top:04X} ({c}/{n})", flush=True)
        if top == 0x0208 and c >= n * 0.6:
            online = True
            break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)

    if not online:
        print("[ba] could not confirm online; running census anyway", flush=True)
    else:
        print("[ba] CONFIRMED online (0x0208)", flush=True)
    census(h, "ONLINE" if online else "post-F4 (NOT confirmed online)")

    print("\n[ba] DONE. Dolphin left running.", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
