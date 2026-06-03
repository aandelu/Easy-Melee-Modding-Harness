"""
online_analog_hitlag.py -- confirm the ANALOG-L L-cancel through HITLAG + air-ending
aerials, ONLINE, with the user walking P2 into P1's nairs.

Uses the same producer-side analog-L cave as online_analog_selfdrive.py (hook
0x8034E680: self-drive the local player's jump->nair->land via 0(r4) buttons, pulse
a light analog L 0x80 every other frame during aerials via 6(r4)). Analog is ON the
whole run. While the script self-drives P1's nairs, YOU walk P2 in so they CONNECT
(hitlag) and try to make some aerials finish in the air (FallAerial).

Per aerial it records: did hitlag occur, LCancelStatus (0x25FF) at landing, and any
grab/airdodge/re-nair misfire. Expectation (analog < 0xAA can't set a digital bit
or press Z): hit-aerials L-cancel (the global-parity pulse keeps firing through
hitlag, unlike the frozen action_frame), and ZERO misfires even on air-ending aerials.

Run (peer in an active in-game match; you play P2):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_analog_hitlag.py
"""
import sys
import time
from collections import Counter

import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness, finalize_payload
import instr_writer as iw

# reuse the verified analog-L self-drive cave + helpers
import online_analog_selfdrive as A

VK_F4, VK_RETURN = 118, 36
OFF_ACTION_STATE = A.OFF_ACTION_STATE
OFF_HITLAG = A.OFF_HITLAG
OFF_LCANCEL = A.OFF_LCANCEL_STATUS
AERIAL = A.AERIAL
LANDING = A.LANDING
GRAB = A.GRAB
AIRDODGE = A.AIRDODGE


def measure(h, pd, seconds):
    """Per-aerial episodes: (had_hitlag, lcancel_status, ended_in_air, misfire)."""
    episodes = []
    cur = None
    in_land = False
    seen = set()
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = A.rd(h, pd + OFF_ACTION_STATE)
        if st is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        seen.add(st)
        try:
            hlraw = h.read_bytes(pd + OFF_HITLAG, 4)
            import struct
            hl = struct.unpack(">f", hlraw)[0]
        except Exception:
            hl = 0.0
        if st in AERIAL:
            if cur is None:
                cur = {"had_hitlag": False, "max_hl": 0.0, "misfire": False,
                       "ended_in_air": False, "lcancel": None}
            if 0 < hl < 60:
                cur["had_hitlag"] = True
                cur["max_hl"] = max(cur["max_hl"], hl)
        if st in (GRAB | {AIRDODGE}) and cur is not None:
            cur["misfire"] = True
        if st in LANDING and not in_land:
            in_land = True
            if cur is not None:
                cur["lcancel"] = A.rdb(h, pd + OFF_LCANCEL)
                episodes.append(cur); cur = None
        elif st not in LANDING and in_land:
            in_land = False
        # aerial ended WITHOUT landing (Fall/FallAerial/airdodge) -> air-ended
        if cur is not None and st not in AERIAL and st not in LANDING:
            cur["ended_in_air"] = True
            if st in (GRAB | {AIRDODGE}):
                cur["misfire"] = True
            episodes.append(cur); cur = None
        time.sleep(0.012)
    return episodes, seen


def main():
    logic = A.assemble(A.CAVE_ASM)
    payload = finalize_payload(logic, A.HOOK, A.CAVE, A.DISPLACED)
    STB = 0x98C40006
    assert payload.count(STB) == 1 and payload[-2] == A.DISPLACED
    print(f"[ah] analog-L cave ready ({len(payload)} words), hook 0x{A.HOOK:08X}", flush=True)

    A.kill_stale()
    h = Harness()
    print("[ah] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[ah] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(5):
        top, cc = A.scene_maj(h)
        print(f"[ah] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[ah] at online CSS; peer must be IN-GAME. waiting ...", flush=True)
            time.sleep(8.0); continue
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[ah] not in-game; abort", flush=True); dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[ah] meta-flush not present; abort", flush=True); dme.un_hook(); return 1
    iw.write_instrs(h, A.CAVE, payload)
    iw.patch_branch(h, A.HOOK, A.CAVE)
    print(f"[ah] hook = 0x{h.read_word(A.HOOK):08X} (analog ON)", flush=True)
    time.sleep(2.0)

    port, pd, seen = A.find_local_port(h, 6)
    if port is None:
        print(f"[ah] no aerials; abort. states={seen}", flush=True); dme.un_hook(); return 1
    print(f"[ah] LOCAL port = {port}, pd = 0x{pd:08X}", flush=True)

    print("\n" + "#" * 68, flush=True)
    print("#  WALK P2 INTO P1'S NAIRS for ~30s. Make some aerials hit (hitlag),", flush=True)
    print("#  and try to let some finish in the AIR (don't land) to test spill.", flush=True)
    print("#" * 68, flush=True)
    time.sleep(3.0)

    eps, seen = measure(h, pd, 30)
    hit = [e for e in eps if e["had_hitlag"]]
    air = [e for e in eps if e["ended_in_air"]]
    landed_hit = [e for e in hit if e["lcancel"] in (1, 2)]
    succ = sum(1 for e in landed_hit if e["lcancel"] == 1)
    misf = sorted({s for s in seen if s in (GRAB | {AIRDODGE})})

    print("\n[ah] === RESULT ===", flush=True)
    top, cc = A.scene_maj(h)
    print(f"[ah] still online: {top==0x0208} (scene 0x{top:04X})", flush=True)
    print(f"[ah] aerials: {len(eps)} total, {len(hit)} HIT (hitlag), "
          f"{len(air)} ended-in-air", flush=True)
    print(f"[ah] HIT-aerial L-cancel: {succ}/{len(landed_hit)} success "
          f"(LCancelStatus); max-hitlag seen {[round(e['max_hl']) for e in hit][:12]}",
          flush=True)
    print(f"[ah] misfire states (grab 0xD4-6 / airdodge 0xEC): "
          f"{[hex(s) for s in misf] or 'NONE'}", flush=True)
    if landed_hit and succ >= 0.8 * len(landed_hit) and not misf:
        print("[ah] [PASS] analog L L-cancels through hitlag, no misfire (incl. air-ends)",
              flush=True)
    elif misf:
        print("[ah] [?] misfire observed -- unexpected for analog<0xAA; inspect", flush=True)
    else:
        print("[ah] [?] few hit-aerials or inconclusive -- walk in more / inspect", flush=True)
    print("[ah] DONE. Dolphin left running. >>> any desync? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
