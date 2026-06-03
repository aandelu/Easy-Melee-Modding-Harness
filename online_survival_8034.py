"""
online_survival_8034.py -- does a HARNESS-installed boot gecko at 0x8034E2AC
survive entering online?  (Single process; all reads in-process = reliable.)

We KNOW meta-flush at 0x803775C0 reverts online. We ASSUME 0x8034E2AC survives
because Altimor's Swap X/Z works there -- but Altimor may install differently
than our harness. This test settles it: install Altimor's EXACT swap payload at
0x8034E2AC via our harness, plus meta-flush as the known-reverting control, and
read both at the menu and online.

Altimor payload (3 rlwimi swapping X<->Z), displaced original = the vanilla
rlwinm we disassembled (0x540084BE). With no controller input the swap is a
no-op and cannot desync.

Result interpretation:
  - 0x8034E2AC BRANCH online  -> our boot-gecko path works in that region; build
    the L-cancel here.
  - 0x8034E2AC VANILLA online -> it reverts like 0x803775C0; Altimor must install
    differently and we need to find out how.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_survival_8034.py
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

ALTIMOR_HOOK = 0x8034E2AC
ALTIMOR_DISPLACED = 0x540084BE          # vanilla rlwinm r0,r0,0x10,0x12,0x1f
ALTIMOR_LOGIC = [0x5000843E, 0x5000B56A, 0x500056F6]   # 3 rlwimi: swap X<->Z

HOOKS = [
    (0x8034E2AC, "Altimor slot (ours, NEW)",   ALTIMOR_DISPLACED),
    (0x803775C0, "meta-flush (ours, control)", 0x88190002),
    (0x801A4FA4, "Extract Menu Info (user)",   None),
    (0x80376A28, "TriggerSendInput (Slippi)",  None),
]


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


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


def st(w, vanilla):
    if (w & 0xFC000000) == 0x48000000:
        return "BRANCH"
    if vanilla is not None and w == vanilla:
        return "VANILLA"
    return "other"


def census(h, tag):
    print(f"\n--- census [{tag}] ---", flush=True)
    for addr, label, vanilla in HOOKS:
        w, c, n = maj(h, addr)
        print(f"  0x{addr:08X}  0x{w:08X}  {st(w, vanilla):<8} {label} ({c}/{n})",
              flush=True)


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def scene_maj(h, n=21):
    vals = [mm(h.read_word(SCENE_WORD)) for _ in range(n)]
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


def main():
    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)                       # control
    h.install_gecko_c2(name="altimor-xz-probe", hook_addr=ALTIMOR_HOOK,
                       logic_words=ALTIMOR_LOGIC, displaced_orig=ALTIMOR_DISPLACED)
    print("[surv] launching (meta-flush + Altimor-slot gecko staged) ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[surv] pid {pid}; CPU live (menu)", flush=True)

    top, c, n = scene_maj(h)
    print(f"[surv] menu scene 0x{top:04X} ({c}/{n})", flush=True)
    census(h, "MENU")

    print("\n[surv] entering online: F4, +3s, Enter, +15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)

    online = False
    for attempt in range(4):
        top, c, n = scene_maj(h)
        print(f"[surv] scene 0x{top:04X} ({c}/{n})", flush=True)
        if top == 0x0208 and c >= n * 0.6:
            online = True
            break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)

    if not online:
        print("[surv] could NOT confirm online; census anyway", flush=True)
    else:
        print("[surv] CONFIRMED online (0x0208)", flush=True)
    census(h, "ONLINE" if online else "post-F4 (unconfirmed)")

    # brief desync watch (frame advance + scene stable)
    print("\n[surv] desync watch (~16s):", flush=True)
    last = h.read_word(0x80479D60)
    for i in range(8):
        time.sleep(2.0)
        top, c, n = scene_maj(h, 7)
        f = h.read_word(0x80479D60)
        print(f"  t+{i*2:2d}s scene 0x{top:04X}({c}/{n}) frame 0x{f:08X} (+{f-last})",
              flush=True)
        last = f

    print("\n[surv] DONE. Dolphin left running. >>> did your screen desync? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
