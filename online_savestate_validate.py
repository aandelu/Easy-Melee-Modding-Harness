"""
online_savestate_validate.py -- does baking a gecko into the F4 savestate make
it survive into online?  (User overwrote slot 4 with Altimor's Swap X/Z.)

Differential design:
  - meta-flush (0x803775C0): BOOT-installed by us via harness INI, NOT in the
    savestate. Predict: BRANCH at menu, VANILLA online (F4 wipes it).
  - Altimor (0x8034E2AC): NOT installed by us; ONLY in the slot-4 savestate.
    Predict: VANILLA at menu, BRANCH online (F4 restores it).

If they swap as predicted, the savestate-carries-gecko mechanism is proven.

Single process (reliable reads). Read-only except staging meta-flush at boot.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_savestate_validate.py
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

HOOKS = [
    (0x8034E2AC, "Altimor (savestate-only)", 0x540084BE),
    (0x803775C0, "meta-flush (boot-only)",   0x88190002),
    (0x801A4FA4, "Extract Menu Info",        None),
    (0x80376A28, "TriggerSendInput (Slippi)",None),
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


def stt(w, vanilla):
    if (w & 0xFC000000) == 0x48000000:
        return "BRANCH"
    if vanilla is not None and w == vanilla:
        return "VANILLA"
    return "other"


def census(h, tag):
    print(f"\n--- census [{tag}] ---", flush=True)
    res = {}
    for addr, label, vanilla in HOOKS:
        w, c, n = maj(h, addr)
        res[addr] = stt(w, vanilla)
        print(f"  0x{addr:08X}  0x{w:08X}  {res[addr]:<8} {label} ({c}/{n})",
              flush=True)
    return res


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
    iw.install_meta_flush(h)        # boot control (NOT in savestate)
    print("[val] launching (meta-flush boot-staged; Altimor only in slot-4 SS) ...",
          flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    top, c, n = scene_maj(h)
    print(f"[val] menu scene 0x{top:04X} ({c}/{n})", flush=True)
    menu = census(h, "MENU")

    print("\n[val] entering online: F4 (load slot 4), +3s, Enter, +15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)

    online = False
    for attempt in range(4):
        top, c, n = scene_maj(h)
        print(f"[val] scene 0x{top:04X} ({c}/{n})", flush=True)
        if top == 0x0208 and c >= n * 0.6:
            online = True
            break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)
    print(f"[val] {'CONFIRMED online (0x0208)' if online else 'NOT confirmed online'}",
          flush=True)
    onl = census(h, "ONLINE" if online else "post-F4 (unconfirmed)")

    print("\n[val] desync watch (~12s):", flush=True)
    last = h.read_word(0x80479D60)
    for i in range(6):
        time.sleep(2.0)
        t, cc, nn = scene_maj(h, 7)
        f = h.read_word(0x80479D60)
        print(f"  t+{i*2}s scene 0x{t:04X}({cc}/{nn}) frame 0x{f:08X} (+{f-last})",
              flush=True)
        last = f

    # verdict
    print("\n[val] === VERDICT ===", flush=True)
    a_menu, a_onl = menu.get(0x8034E2AC), onl.get(0x8034E2AC)
    m_menu, m_onl = menu.get(0x803775C0), onl.get(0x803775C0)
    print(f"  Altimor 0x8034E2AC : menu={a_menu} -> online={a_onl}", flush=True)
    print(f"  meta-flush 0x803775C0: menu={m_menu} -> online={m_onl}", flush=True)
    if a_onl == "BRANCH":
        print("  [PASS] savestate carried Altimor's gecko through F4 into online!",
              flush=True)
    else:
        print("  [FAIL] Altimor not present online -- savestate did not carry it.",
              flush=True)
    print("\n[val] DONE. Dolphin left running. >>> still synced on your end? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
