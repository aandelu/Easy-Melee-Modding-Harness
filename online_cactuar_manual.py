"""
online_cactuar_manual.py -- online Cactuar test with MANUAL online entry.

Same as online_cactuar_test.py but does NOT synthesize F4/Enter (that path broke
after the Slippi update -- the savestate loads fine manually, so synthetic key
delivery is the suspect). Instead: launch + hook in ONE process, then YOU press
F4 (load slot 4) and Enter (connect) on the Dolphin window this opens; the script
polls until in-game, installs the veto cave (threshold frame 4), and observes.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_cactuar_manual.py
"""
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
from melee_harness import Harness, finalize_payload
import instr_writer as iw
from online_cactuar_test import (
    assemble, CAVE_ASM, HOOK, CAVE, DISPLACED, STB_X, STB_Y,
    THRESH_INIT_WORD, thresh_word, THRESH_FRAMES, mm, SCENE_WORD,
    observe_both, kill_stale,
)


def scene_now(h):
    try:
        return Counter(mm(h.read_word(SCENE_WORD)) for _ in range(11)).most_common(1)[0]
    except Exception:
        return (None, 0)


def main():
    payload = finalize_payload(assemble(CAVE_ASM), HOOK, CAVE, DISPLACED)
    THRESH_ADDR = CAVE + payload.index(THRESH_INIT_WORD) * 4
    payload.index(STB_X); payload.index(STB_Y)
    print(f"[cm] cave {len(payload)} words; THRESH @0x{THRESH_ADDR:08X} "
          f"(frame {THRESH_FRAMES})", flush=True)

    kill_stale()
    h = Harness()
    print("[cm] launching harness Dolphin ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)

    print("\n" + "=" * 64, flush=True)
    print(">>> ON THE DOLPHIN WINDOW I JUST OPENED:  press F4  (load slot 4),", flush=True)
    print(">>> then press Enter (connect). Then start running + reversing.", flush=True)
    print("=" * 64 + "\n", flush=True)

    online = False
    last = None
    t_end = time.time() + 150         # generous: time to F4 + Enter + connect
    while time.time() < t_end:
        top, cc = scene_now(h)
        if top != last:
            print(f"[cm] scene 0x{top:04X} ({cc}/11)" if top is not None
                  else "[cm] read failed", flush=True)
            last = top
        if top == 0x0208 and cc >= 7:
            online = True; break
        time.sleep(1.5)
    if not online:
        print(f"[cm] never reached in-game (last scene 0x{last:04X}). If the savestate "
              f"didn't load, the manual F4 didn't register on this window; if it loaded "
              f"but stayed at CSS (0x0008), the peer wasn't ready.", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[cm] meta-flush NOT present at 0x803775C0 -- slot 4 lost it; re-bake slot 4. "
              "abort", flush=True)
        dme.un_hook(); return 1

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    iw.write_instrs(h, THRESH_ADDR, [thresh_word(THRESH_FRAMES)])
    hookw = h.read_word(HOOK)
    print(f"[cm] hook = 0x{hookw:08X} "
          f"({'installed' if (hookw & 0xFC000000) == 0x48000000 else 'NOT a branch?!'}); "
          f"THRESH frame {THRESH_FRAMES}", flush=True)
    time.sleep(1.0)

    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    print(f"[cm] watching both ports P1=0x{pds[1]:08X} P2=0x{pds[2]:08X}", flush=True)
    cnt, crouch = observe_both(h, pds, 30, "VETO ON: full-run then reverse, repeat")

    print("\n[cm] === VERDICT ===", flush=True)
    top, cc = scene_now(h)
    print(f"[cm] still online: {top == 0x0208} (scene 0x{top:04X})", flush=True)
    act = max((1, 2), key=lambda p: sum(cnt[p].values()))
    cl = crouch[act]
    sq = cnt[act].get("Squat(0x27,crouch)", 0)
    tr = cnt[act].get("TurnRun(0x13,slow)", 0)
    print(f"[cm] active port P{act}: {dict(cnt[act])}", flush=True)
    if cl:
        print(f"[cm] crouch avg {sum(cl)/len(cl):.1f}f at threshold frame {THRESH_FRAMES}",
              flush=True)
    if sq > 0 and tr == 0:
        print(f"[cm] [PASS] {sq} reversals -> crouch, 0 TurnRun.", flush=True)
    else:
        print(f"[cm] crouches={sq} TurnRun={tr} -- if 0 crouches, do FULL-run reversals.",
              flush=True)
    print("[cm] DONE. Dolphin left running. >>> feel + any desync? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
