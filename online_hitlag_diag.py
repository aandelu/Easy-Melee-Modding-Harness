"""
online_hitlag_diag.py -- diagnose + fix the HITLAG L-cancel miss, ONLINE.

THE BUG (user-reported, 2026-05-22)
-----------------------------------
The v3 cadence anchors the Z pulse to the Action State Frame Counter (Player Data
+0x894). But +0x894 **freezes during hitlag** (the move animation pauses while the
hit "freezes" both characters). So when a nair CONNECTS:
  * 0x894 stops advancing -> our `(action_frame-1) % 7 == 0` gate is stuck on one
    value for the whole hitlag. If that value isn't a press frame, we pulse Z ZERO
    times for the entire hitlag.
  * Meanwhile "Frames Since Z Pressed" (0x67F) keeps counting REAL frames, so it
    runs past the 7-frame L-cancel window during a long hitlag -> the L-cancel
    misses if you land shortly after hitlag.
(The old v2 global-frame cadence kept ticking through hitlag, so it didn't have
this hole -- but it had the uptake bug v3 fixed.)

THE FIX (under test here)
-------------------------
Also pulse Z whenever the character is in hitlag (Hitlag counter 0x195C != 0).
Per the Melee mechanic: an L-cancel input during ANY hitlag frame is buffered and
re-applied through the rest of hitlag, staying active ~6 frames after hitlag ends
-- so a single during-hitlag press gives a fresh L-cancel window for the landing.

THE TEST (needs YOU)
--------------------
This script SELF-DRIVES P1 (local Fox) through full-hop nairs and logs P1's hitlag
interaction. YOU play P2 on the other machine and WALK INTO P1'S NAIRS so they
connect (causing hitlag). It runs two phases while you keep walking in:
  PHASE A: hitlag-override OFF  (= current v3) -> expect MISSES on aerials that hit.
  PHASE B: hitlag-override ON   (= the fix)    -> expect those to L-cancel; watch
                                                  for airdodge misfires (buffer).
Per-aerial it records: did hitlag occur, LCancelStatus (0x25FF) at landing, and any
grab/airdodge misfire. Reports cancel rate for HIT vs NO-HIT aerials each phase.

Run (peer must be in an active in-game match):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_hitlag_diag.py
"""
import struct
import subprocess
import sys
import time
from collections import Counter

import capstone
import keystone
import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness, finalize_payload
import instr_writer as iw

VK_F4, VK_RETURN = 118, 36
SCENE_WORD = 0x80479D30
FRAME = 0x80479D60
HOOK = 0x8034E2AC
DISPLACED = 0x540084BE
CAVE = 0x803FA600

OFF_ACTION_STATE = 0x10
OFF_ACTION_FRAME = 0x894     # float, freezes during hitlag
OFF_HITLAG = 0x195C          # float, counts down each frame; !=0 -> in hitlag
OFF_Z_TIMER = 0x67F          # u8, frames since Z pressed
OFF_LCANCEL_STATUS = 0x25FF  # u8: 0=none 1=success 2=fail

AERIAL = set(range(0x41, 0x46))
LANDING = set(range(0x46, 0x4B))
GRAB = set(range(0xD4, 0xD7))
AIRDODGE = 0x00EC

CAVE_ASM = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    lwz  8, -0x49E4(13)     # ODB ptr -> local player
    cmpwi 8, 0
    beq  ldone
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  ldone
    lbz  9, 0(8)
    mulli 9, 9, 0xE90
    lis  6, 0x8045
    ori  6, 6, 0x3130
    add  6, 6, 9
    lwz  6, 0(6)
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  6, 0x2C(6)         # r6 = local Player Data
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone

    lwz  7, 0x10(6)         # action state
    rlwinm 7, 7, 0, 16, 31

    cmpwi 7, 0x0E           # Wait -> X (jump)
    bne  sd1
    oris 0, 0, 0x0400
    b    ldone
sd1:
    cmpwi 7, 0x18           # KneeBend -> X (full hop)
    bne  sd2
    oris 0, 0, 0x0400
    b    ldone
sd2:
    cmpwi 7, 0x19           # jump/fall -> A (nair)
    blt  sd3
    cmpwi 7, 0x22
    bgt  sd3
    oris 0, 0, 0x0100
    b    ldone
sd3:
    cmpwi 7, 0x41           # aerial?
    blt  ldone
    cmpwi 7, 0x45
    bgt  ldone

    # --- HITLAG OVERRIDE (the fix): in hitlag -> always press Z ---
    lwz  9, 0x195C(6)       # hitlag counter (float bits); !=0 -> in hitlag
    cmpwi 9, 0
    beq  cadence
    oris 0, 0, 0x0010      # OVERRIDE Z press (1st) -- toggled nop<->oris for A/B
    b    ldone
cadence:
    # --- anchored cadence (float action_frame -> int, no FPU) ---
    lwz  8, 0x894(6)
    rlwinm 9, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 9, 9, 150
    srw  8, 8, 9           # r8 = (int) action_frame
    addi 8, 8, -1
    li   9, 7
    divw 6, 8, 9
    mulli 6, 6, 7
    subf 8, 6, 8           # (n-1) % 7
    cmpwi 8, 0
    bne  ldone
    oris 0, 0, 0x0010      # cadence Z press (2nd)
ldone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
"""


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def scene_maj(h, n=15):
    return Counter(mm(h.read_word(SCENE_WORD)) for _ in range(n)).most_common(1)[0]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def ensure_hooked(h):
    if dme.is_hooked():
        return True
    for _ in range(20):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.2)
    return False


def rd(h, addr):
    try:
        return h.read_word(addr)
    except Exception:
        if ensure_hooked(h):
            try:
                return h.read_word(addr)
            except Exception:
                return None
        return None


def rdf(h, addr):
    """read a big-endian float; None on failure."""
    try:
        return struct.unpack(">f", h.read_bytes(addr, 4))[0]
    except Exception:
        return None


def rdb(h, addr):
    try:
        return h.read_bytes(addr, 1)[0]
    except Exception:
        return None


def find_local_port(h, seconds=6):
    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    seen = {1: set(), 2: set()}
    t_end = time.time() + seconds
    while time.time() < t_end:
        for p in (1, 2):
            if pds[p] != -1:
                st = rd(h, pds[p] + OFF_ACTION_STATE)
                if st is not None:
                    seen[p].add(st & 0xFFFF)
        time.sleep(0.015)
    for p in (1, 2):
        if seen[p] & AERIAL:
            return p, pds[p], seen
    return None, None, seen


def measure(h, pd, seconds):
    """Track per-aerial episodes: (had_hitlag, lcancel_status, misfire?).
    An episode runs from aerial entry to the next landing. We sample hitlag
    (0x195C) across the aerial and read LCancelStatus (0x25FF) at landing entry."""
    episodes = []      # list of dicts
    cur = None         # current aerial episode
    in_land = False
    seen = set()
    z_at_land = []     # Z-timer (0x67F) sampled at landing entry (hit episodes)
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = rd(h, pd + OFF_ACTION_STATE)
        if st is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        seen.add(st)
        hl = rdf(h, pd + OFF_HITLAG)
        if st in AERIAL:
            if cur is None:
                cur = {"had_hitlag": False, "max_hl": 0.0, "misfire": False}
            if hl and hl > 0:
                cur["had_hitlag"] = True
                cur["max_hl"] = max(cur["max_hl"], hl)
        if st in (GRAB | {AIRDODGE}) and cur is not None:
            cur["misfire"] = True
        if st in LANDING and not in_land:
            in_land = True
            ls = rdb(h, pd + OFF_LCANCEL_STATUS)
            zt = rdb(h, pd + OFF_Z_TIMER)
            if cur is not None:
                cur["lcancel"] = ls
                cur["z_timer"] = zt
                episodes.append(cur)
                if cur["had_hitlag"]:
                    z_at_land.append(zt)
                cur = None
        elif st not in LANDING and in_land:
            in_land = False
        # an aerial that ends WITHOUT landing (airdodge / fall) -> close it
        if cur is not None and st not in AERIAL and st not in LANDING:
            if st in (GRAB | {AIRDODGE}):
                cur["misfire"] = True
            cur["lcancel"] = None
            cur["z_timer"] = None
            episodes.append(cur)
            cur = None
        time.sleep(0.012)
    return episodes, seen, z_at_land


def summarize(label, episodes, seen):
    hit = [e for e in episodes if e["had_hitlag"]]
    nohit = [e for e in episodes if not e["had_hitlag"]]
    def rate(eps):
        landed = [e for e in eps if e.get("lcancel") in (1, 2)]
        succ = sum(1 for e in landed if e["lcancel"] == 1)
        return succ, len(landed), len(eps)
    hs, hl, ht = rate(hit)
    ns, nl, nt = rate(nohit)
    misf = sorted({s for s in seen if s in (GRAB | {AIRDODGE})})
    print(f"\n  --- {label} ---", flush=True)
    print(f"  aerials: {len(episodes)} total  ({len(hit)} HIT / {len(nohit)} no-hit)",
          flush=True)
    print(f"  HIT  aerials L-cancel: {hs}/{hl} success of landed "
          f"(LCancelStatus); {[round(e['max_hl']) for e in hit][:12]} max-hitlag",
          flush=True)
    print(f"  NO-HIT aerials L-cancel: {ns}/{nl} success of landed", flush=True)
    print(f"  Z-timer at landing (hit aerials): "
          f"{[e.get('z_timer') for e in hit if e.get('z_timer') is not None][:12]}",
          flush=True)
    print(f"  misfire states seen this phase: {[hex(s) for s in misf] or 'NONE'}",
          flush=True)
    return hs, hl, ns, nl, misf


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)
    ORIS_Z, NOP = 0x64000010, 0x60000000
    i_override = payload.index(ORIS_Z)              # 1st = hitlag override press
    i_cadence = payload.index(ORIS_Z, i_override + 1)  # 2nd = cadence press
    OVERRIDE_ADDR = CAVE + i_override * 4
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print(f"[hl] cave verify ({len(payload)} words); override Z @ 0x{OVERRIDE_ADDR:08X}, "
          f"cadence Z @ 0x{CAVE + i_cadence*4:08X}", flush=True)
    code = b"".join(w.to_bytes(4, 'big') for w in payload)
    for i in md.disasm(code, CAVE):
        if i.bytes.hex().upper() in ("8106195C", "8106089481060894") or "195c" in i.op_str.lower():
            print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)

    kill_stale()
    h = Harness()
    print("[hl] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[hl] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(5):
        top, cc = scene_maj(h)
        print(f"[hl] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[hl] at online CSS; peer must be IN-GAME (0x0208). waiting ...", flush=True)
            time.sleep(8.0); continue
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[hl] not in-game; abort (peer must be in an active match)", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[hl] meta-flush not present; abort", flush=True); dme.un_hook(); return 1
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    print(f"[hl] hook = 0x{h.read_word(HOOK):08X}", flush=True)
    time.sleep(2.0)

    port, pd, seen = find_local_port(h, 6)
    if port is None:
        print("[hl] no aerials seen on either port; abort", flush=True)
        dme.un_hook(); return 1
    print(f"[hl] LOCAL port = {port}, pd = 0x{pd:08X}", flush=True)

    print("\n" + "#" * 68, flush=True)
    print("#  WALK P2 INTO P1'S NAIRS CONTINUOUSLY FOR THE NEXT ~40 SECONDS.", flush=True)
    print("#  (P1 is self-driving full-hop nairs; make them CONNECT to cause hitlag.)", flush=True)
    print("#" * 68, flush=True)
    time.sleep(3.0)

    print("\n[hl] === PHASE A: hitlag-override OFF (current v3) ~18s ===", flush=True)
    iw.write_instrs(h, OVERRIDE_ADDR, [NOP])
    print(f"[hl] override instr = 0x{h.read_word(OVERRIDE_ADDR):08X} (nop)", flush=True)
    epA, seenA, zA = measure(h, pd, 18)
    a = summarize("PHASE A (override OFF)", epA, seenA)

    print("\n[hl] === PHASE B: hitlag-override ON (the fix) ~18s ===", flush=True)
    if not ensure_hooked(h):
        print("[hl] dme detached; abort", flush=True); return 1
    iw.write_instrs(h, OVERRIDE_ADDR, [ORIS_Z])
    print(f"[hl] override instr = 0x{h.read_word(OVERRIDE_ADDR):08X} (oris Z)", flush=True)
    epB, seenB, zB = measure(h, pd, 18)
    b = summarize("PHASE B (override ON = fix)", epB, seenB)

    print("\n[hl] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[hl] still online: {top == 0x0208} (scene 0x{top:04X})", flush=True)
    hsA, hlA, _, _, mA = a
    hsB, hlB, _, _, mB = b
    print(f"[hl] HIT-aerial L-cancel success: A(off) {hsA}/{hlA}  ->  B(fix) {hsB}/{hlB}",
          flush=True)
    print(f"[hl] misfires: A {[hex(s) for s in mA] or 'NONE'}  "
          f"B {[hex(s) for s in mB] or 'NONE'}", flush=True)
    if hlB and hsB >= 0.8 * hlB and not mB:
        print("[hl] [PASS] hitlag override L-cancels hit-aerials, no misfire", flush=True)
    elif mB:
        print("[hl] [?] override works but MISFIRES present (buffer airdodge?) -- "
              "inspect; may need a trailing/air-end guard", flush=True)
    else:
        print("[hl] [?] inconclusive -- need more hit-aerials (walk in more) or inspect",
              flush=True)
    print("[hl] DONE. Dolphin left running. >>> any desync on your screen? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
