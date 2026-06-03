"""
online_cactuar_attach.py -- attach to an ALREADY-RUNNING online Dolphin and install
the Cactuar dash veto WITHOUT relaunching (so the live match isn't disrupted).

Use when a previous run launched Dolphin + got online but aborted before installing
(e.g. the scene check tore during connection), leaving you in a match with no cave.
This re-hooks dme to that Dolphin and installs the cave + producer hook + early-release
threshold, then briefly observes.

Reuses the verified cave from online_cactuar_test.py (threshold = THRESH_FRAMES).

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_cactuar_attach.py
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
    observe_both,
)


def main():
    payload = finalize_payload(assemble(CAVE_ASM), HOOK, CAVE, DISPLACED)
    THRESH_ADDR = CAVE + payload.index(THRESH_INIT_WORD) * 4
    payload.index(STB_X); payload.index(STB_Y)   # sanity: markers present
    print(f"[at] cave {len(payload)} words; THRESH @0x{THRESH_ADDR:08X} "
          f"(frame {THRESH_FRAMES})", flush=True)

    h = Harness()                       # no launch -- attach to the running Dolphin
    print("[at] attaching dme to running Dolphin ...", flush=True)
    for _ in range(30):
        dme.hook()
        if dme.is_hooked():
            break
        time.sleep(0.3)
    if not dme.is_hooked():
        print("[at] could not attach dme -- is Dolphin running? abort", flush=True)
        return 1

    # confirm in-game (majority vote -- reads tear during rollback)
    scene = Counter(mm(h.read_word(SCENE_WORD)) for _ in range(15)).most_common(1)[0]
    print(f"[at] scene 0x{scene[0]:04X} ({scene[1]}/15)", flush=True)
    if scene[0] != 0x0208:
        print("[at] not reading in-game (0x0208). Installing anyway -- cave is dormant "
              "until you're in a run; if it doesn't fire, re-run while in the match.",
              flush=True)

    # meta-flush must be present (baked into slot 4) for the dme code patch to take
    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[at] meta-flush NOT present at 0x803775C0 -- slot 4 may have lost it after "
              "the Slippi update. Re-bake slot 4 (enter a match normally, save state to "
              "slot 4 with the meta-flush gecko on). abort", flush=True)
        return 1

    # install: cave body, then the hook branch, then patch the threshold
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    iw.write_instrs(h, THRESH_ADDR, [thresh_word(THRESH_FRAMES)])
    hookw = h.read_word(HOOK)
    print(f"[at] hook 0x{HOOK:08X} = 0x{hookw:08X} "
          f"({'BRANCH-installed' if (hookw & 0xFC000000) == 0x48000000 else 'NOT a branch?!'})",
          flush=True)
    print(f"[at] THRESH lis = 0x{h.read_word(THRESH_ADDR):08X} (frame {THRESH_FRAMES})",
          flush=True)
    time.sleep(1.0)

    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    print(f"[at] watching both ports P1=0x{pds[1]:08X} P2=0x{pds[2]:08X}", flush=True)
    cnt, crouch = observe_both(h, pds, 30,
                               "VETO ON (attached): full-run then reverse, repeat")
    act = max((1, 2), key=lambda p: sum(cnt[p].values()))
    cl = crouch[act]
    print(f"\n[at] active port P{act}: {dict(cnt[act])}", flush=True)
    if cl:
        print(f"[at] crouch avg {sum(cl)/len(cl):.1f}f at threshold frame {THRESH_FRAMES}",
              flush=True)
    sq = cnt[act].get("Squat(0x27,crouch)", 0)
    tr = cnt[act].get("TurnRun(0x13,slow)", 0)
    if sq > 0 and tr == 0:
        print(f"[at] [PASS] veto fires: {sq} reversals -> crouch, 0 TurnRun.", flush=True)
    else:
        print(f"[at] crouches={sq} TurnRun={tr} -- if 0 crouches, do FULL-run reversals.",
              flush=True)
    print("[at] DONE. Dolphin left running. >>> feel + any desync? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
