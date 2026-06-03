"""
online_metaflush_validate.py -- is meta-flush (baked into slot-4 savestate)
present & functional ONLINE, and does using it desync?

We do NOT boot-stage meta-flush; its ONLY source is the slot-4 savestate. So:
  - menu:   0x803775C0 should be VANILLA (not present yet)
  - online: 0x803775C0 should be BRANCH (restored from savestate by F4)

Then test, low risk first:
  Q1 present online?      census
  Q2 responds online?     one armed flush (zero-length ping) clears magic
  Q3 desync on arm?       repeated pings while user watches; scene/frame stable
  Q4 stays responsive?    pings keep clearing across rollbacks
  Q5 debug-region scratch survives in-match rollback?  write marker, monitor
     (informs whether the L-cancel code cave can live at 0x803FAxxx)

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_metaflush_validate.py
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
FRAME = 0x80479D60
MARKER_ADDR = 0x803FA460        # free scratch, NOT the meta-flush control plane


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def scene_maj(h, n=15):
    vals = [mm(h.read_word(SCENE_WORD)) for _ in range(n)]
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


def word_maj(h, addr, n=11):
    vals = []
    for _ in range(n):
        try:
            vals.append(h.read_word(addr))
        except Exception:
            vals.append(-1)
        time.sleep(0.008)
    top, c = Counter(vals).most_common(1)[0]
    return top, c


def is_branch(w):
    return (w & 0xFC000000) == 0x48000000


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def ping(h, timeout_s=1.5):
    """One zero-length armed flush. Returns ms to clear, or None on timeout."""
    t0 = time.time()
    try:
        iw.flush_range(h, iw.FLUSH_REQUEST, iw.FLUSH_REQUEST, timeout_s=timeout_s)
        return (time.time() - t0) * 1000
    except TimeoutError:
        return None


def main():
    kill_stale()
    h = Harness()                       # NO install_meta_flush -- savestate only
    print("[mf] launching (meta-flush NOT boot-staged; only in slot-4 SS) ...",
          flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    w, c = word_maj(h, iw.META_FLUSH_HOOK)
    print(f"[mf] menu  0x803775C0 = 0x{w:08X} "
          f"({'BRANCH' if is_branch(w) else 'vanilla/other'}) ({c}/11)", flush=True)

    print("\n[mf] entering online: F4 (load slot 4), +3s, Enter, +15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)

    online = False
    for _ in range(4):
        top, cc, nn = scene_maj(h)
        print(f"[mf] scene 0x{top:04X} ({cc}/{nn})", flush=True)
        if top == 0x0208 and cc >= nn * 0.6:
            online = True
            break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)
    if not online:
        print("[mf] NOT online; aborting", flush=True)
        dme.un_hook(); return 1
    print("[mf] CONFIRMED online (0x0208)", flush=True)

    # Q1: present online?
    w, c = word_maj(h, iw.META_FLUSH_HOOK)
    print(f"\n[mf] Q1 online 0x803775C0 = 0x{w:08X} "
          f"({'BRANCH (present!)' if is_branch(w) else 'VANILLA (NOT present)'}) ({c}/11)",
          flush=True)
    if not is_branch(w):
        print("[mf] meta-flush NOT present online -- savestate didn't carry it? abort",
              flush=True)
        dme.un_hook(); return 1

    # Q2: responds?
    print("\n[mf] Q2 first armed ping (WATCH SCREEN) ...", flush=True)
    ms = ping(h, timeout_s=2.0)
    print(f"[mf] -> {'cleared in %.0f ms' % ms if ms is not None else 'TIMEOUT (no response)'}",
          flush=True)
    if ms is None:
        print("[mf] present but not firing online. abort", flush=True)
        dme.un_hook(); return 1

    # Q3/Q4: repeated pings + monitor over ~12s
    print("\n[mf] Q3/Q4 repeated pings + monitor (~12s, WATCH FOR DESYNC):", flush=True)
    ok = 0
    last = h.read_word(FRAME)
    for i in range(6):
        results = [ping(h, 1.5) for _ in range(3)]
        ok += sum(1 for r in results if r is not None)
        top, cc, nn = scene_maj(h, 7)
        f = h.read_word(FRAME)
        print(f"  t+{i*2}s pings_ok+={sum(1 for r in results if r is not None)}/3 "
              f"scene 0x{top:04X}({cc}/{nn}) frame 0x{f:08X} (+{f-last})", flush=True)
        last = f
        time.sleep(1.0)
    print(f"[mf] total successful pings: {ok}/18", flush=True)

    # Q5: data-marker persistence across rollbacks
    print("\n[mf] Q5 write marker 0xCAFEF00D @ 0x803FA460, monitor ~14s:", flush=True)
    h.write_words(MARKER_ADDR, [0xCAFEF00D])
    persisted = True
    for i in range(7):
        time.sleep(2.0)
        v = h.read_word(MARKER_ADDR)
        keep = (v == 0xCAFEF00D)
        persisted &= keep
        print(f"  t+{i*2}s marker=0x{v:08X} {'OK' if keep else 'CHANGED'}", flush=True)
    print(f"[mf] marker persisted across rollbacks: {persisted} "
          f"({'debug region SURVIVES -> cave OK there' if persisted else 'debug region in rollback SS -> cave must go elsewhere'})",
          flush=True)

    print("\n[mf] DONE. Dolphin left running. >>> did your screen desync at any point? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
