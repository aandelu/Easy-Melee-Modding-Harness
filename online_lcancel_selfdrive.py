"""
online_lcancel_selfdrive.py -- self-driving L-cancel at 0x8034E2AC, tested ONLINE.

Cave (producer-side, injects buttons into r0 via oris; proven to propagate):
  - read local action state via the ODB local port -> GObj -> +0x2C -> +0x10
  - Wait (0x0E) / KneeBend (0x18) -> press X (jump / full hop)  oris r0,r0,0x0400
  - JumpF/Fall (0x19..22)         -> press A (nair)             oris r0,r0,0x0100
  - aerial (0x41..0x45) AND (action_frame-1)%7==0 -> press Z    oris r0,r0,0x0010
  then displaced rlwinm + branch back.

The Z cadence is ANCHORED to the aerial via the in-game Action State Frame
Counter (Player Data + 0x894, a float that resets to 1.0 each new action state),
NOT the global frame counter -- so the first press lands on the first aerial
frame (fixes slow-uptake) and the cadence is rollback-safe. The float is decoded
to an integer with pure integer ops (no FPU); see CAVE_ASM.

The Z press instruction is toggled nop<->oris at runtime so we measure baseline
(no Z) vs cancelled (Z on) in ONE online session. Two observables:
  * LCancelStatus (Player Data + 0x25FF: 1=success, 2=fail) -- the direct flag.
  * Landing-state (0x46..0x4A) duration via game-frame deltas (~15f -> ~7f NAIR).

keystone-assembled, capstone-verified before flushing.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_lcancel_selfdrive.py
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
from melee_harness import Harness, DEFAULT_CAVE, finalize_payload
import instr_writer as iw

VK_F4 = 118
VK_RETURN = 36
SCENE_WORD = 0x80479D30
FRAME = 0x80479D60
HOOK = 0x8034E2AC
DISPLACED = 0x540084BE
# NOTE: must NOT overlap the meta-flush control plane (0x803FA440-0x803FA44C) or
# the L flag (0x803FA470). DEFAULT_CAVE (0x803FA3E8) is too close -- a 57-word
# cave runs into 0x803FA440 and flush_range corrupts it -> crash. Place well past.
CAVE = 0x803FA600
L_FLAG_ADDR = 0x803FA470
OFF_ACTION_STATE = 0x10
OFF_LCANCEL_STATUS = 0x25FF           # Player Data + 0x25FF (u8): 0=none 1=success 2=fail
OFF_ACTION_FRAME = 0x894             # Action State Frame Counter (FLOAT, resets to 1.0)
LANDING = set(range(0x46, 0x4B))     # LandingAir N/F/B/Hi/Lw
AERIAL = set(range(0x41, 0x46))
LR_TIMER = 0x680                      # Char Data + 0x680 (frames since L/R pressed)

CAVE_ASM = """
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    # DEBUG: capture r13 / ODB ptr / both port-offset candidates to 0x803FA480..
    lis  6, 0x803F
    ori  6, 6, 0xA480
    stw  13, 0(6)           # [+0] r13
    lwz  8, -0x49E4(13)
    stw  8, 4(6)            # [+4] ODB ptr
    li   9, -1
    stw  9, 8(6)
    stw  9, 12(6)
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  dbg_done
    lbz  9, 2(8)
    stw  9, 8(6)            # [+8] *(ODB+2) ODB_INPUT_SOURCE_INDEX
    lbz  9, 0(8)
    stw  9, 12(6)           # [+12] *(ODB+0) ODB_LOCAL_PLAYER_INDEX
dbg_done:

    lwz  8, -0x49E4(13)     # r8 = ODB ptr (*(r13 + OFST_R13_ODB_ADDR))
    cmpwi 8, 0
    beq  ldone
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  ldone
    lbz  9, 0(8)            # r9 = local player port (ODB_LOCAL_PLAYER_INDEX)
    mulli 9, 9, 0xE90       # port * GObj stride
    lis  6, 0x8045
    ori  6, 6, 0x3130       # 0x80453130 (P1 GObj ptr base)
    add  6, 6, 9            # &GObj_ptr[local port]
    lwz  6, 0(6)            # local GObj ptr
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  6, 0x2C(6)         # local Player Data
    cmpwi 6, 0
    beq  ldone
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  ldone
    lwz  7, 0x10(6)         # action state word
    rlwinm 7, 7, 0, 16, 31  # low 16

    cmpwi 7, 0x0E           # Wait -> X (start jump)
    bne  n_wait
    oris 0, 0, 0x0400
    b    ldone
n_wait:
    cmpwi 7, 0x18           # KneeBend -> X (hold for FULL hop)
    bne  n_knee
    oris 0, 0, 0x0400
    b    ldone
n_knee:
    cmpwi 7, 0x19           # JumpF/Fall -> A
    blt  n_jump
    cmpwi 7, 0x22
    bgt  n_jump
    oris 0, 0, 0x0100
    b    ldone
n_jump:
    cmpwi 7, 0x41           # aerial?
    blt  ldone
    cmpwi 7, 0x45
    bgt  ldone
    # --- cadence ANCHORED to the aerial via Action State Frame Counter ---
    # (Player Data + 0x894). r6 still = pdata here. The field is a FLOAT (n.0,
    # resets to 1.0 each new action state); decode to int with integer ops:
    #   n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exponent)
    lwz  8, 0x894(6)        # r8 = action_frame float bits
    rlwinm 9, 8, 9, 24, 31  # r9 = exponent (IEEE bits 1..8)
    rlwinm 8, 8, 0, 9, 31   # r8 = mantissa (low 23)
    oris 8, 8, 0x0080       # r8 |= 0x800000 (implicit leading 1)
    subfic 9, 9, 150        # r9 = 150 - exponent (right-shift amount)
    srw  8, 8, 9            # r8 = (int) action_frame  (1, 2, 3, ...)
    addi 8, 8, -1
    li   9, 7
    divw 6, 8, 9            # r6 = (n-1)/7 (r6 free after the state read)
    mulli 6, 6, 7
    subf 8, 6, 8            # r8 = (n-1) % 7
    cmpwi 8, 0
    bne  ldone              # press only on (n-1)%7==0 (n in {1,8,15,..}); RELEASE the rest
    oris 0, 0, 0x0010      # Z (one rising edge per 7 aerial frames); toggled nop/oris
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
    """Re-attach dme if it has detached (heavy polling can drop it)."""
    if not dme.is_hooked():
        for _ in range(20):
            dme.hook()
            if dme.is_hooked():
                return True
            time.sleep(0.2)
        return False
    return True


def rd(h, addr):
    """Throttled, detach-tolerant read_word. Returns None on failure."""
    try:
        return h.read_word(addr)
    except Exception:
        if ensure_hooked(h):
            try:
                return h.read_word(addr)
            except Exception:
                return None
        return None


def wr(h, addr, data):
    if not ensure_hooked(h):
        return False
    try:
        h.write_bytes(addr, data); return True
    except Exception:
        return False


def find_local_port(h, seconds=6):
    """Observe both ports; the LOCAL one is whichever the self-drive loops through
    aerials. Returns (port, pd, {port: states_set})."""
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


def observe_states(h, pd, seconds):
    """Light poll (~60Hz) returning the sequence of (frame, state) and distinct
    states seen -- to confirm P1 is actually looping jump->nair->land."""
    seq = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = rd(h, pd + OFF_ACTION_STATE)
        f = rd(h, FRAME)
        if st is not None and f is not None:
            seq.append((f, st & 0xFFFF))
        time.sleep(0.015)
    return seq


GRAB = set(range(0xD4, 0xD7))     # Catch / CatchDash / CatchTurn (Z misfire on ground)
AIRDODGE = 0x00EC                   # EscapeAir (Z/L misfire in airborne non-attack)


def measure(h, pd, seconds, sample_lr=False):
    """Throttled watch of P1; landing-state durations via game-frame deltas.
    Also returns the set of all action states seen (for misfire detection) and a
    Counter of LCancelStatus values (pd+0x25FF: 1=success, 2=fail) read on each
    landing-aerial entry -- the direct per-landing L-cancel observable."""
    durations, max_lr, cycles = [], 0, 0
    in_land, land_start = False, 0
    seen = set()
    lc_status = Counter()
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = rd(h, pd + OFF_ACTION_STATE)
        f = rd(h, FRAME)
        if st is None or f is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        seen.add(st)
        if sample_lr and st in AERIAL:
            try:
                max_lr = max(max_lr, h.read_bytes(pd + LR_TIMER, 1)[0])
            except Exception:
                pass
        if st in LANDING and not in_land:
            in_land, land_start = True, f
            try:                                     # LCancelStatus set at landing
                lc_status[h.read_bytes(pd + OFF_LCANCEL_STATUS, 1)[0]] += 1
            except Exception:
                pass
        elif st not in LANDING and in_land:
            in_land = False
            d = f - land_start
            if 0 < d < 60:
                durations.append(d); cycles += 1
        time.sleep(0.012)
    return durations, max_lr, cycles, seen, lc_status


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)
    ORIS_Z = 0x64000010                        # oris r0,r0,0x10 (Z), unique
    NOP = 0x60000000
    l_idx = payload.index(ORIS_Z)              # X=...0400, A=...0100, Z=...0010
    L_ORIS_ADDR = CAVE + l_idx * 4
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print(f"[lc] cave verify ({len(payload)} words):", flush=True)
    code = b"".join(w.to_bytes(4, 'big') for w in payload)
    for i in md.disasm(code, CAVE):
        print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
              flush=True)
    # branch-at-hook
    bh = 0x48000000 | ((CAVE - HOOK) & 0x03FFFFFC)
    print(f"   hook branch: 0x{bh:08X} = {next(md.disasm(bh.to_bytes(4,'big'),HOOK)).op_str}",
          flush=True)

    kill_stale()
    h = Harness()
    print("\n[lc] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[lc] L oris instruction at 0x{L_ORIS_ADDR:08X} (toggle nop/oris per phase)",
          flush=True)

    print("[lc] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(5):
        top, cc = scene_maj(h)
        print(f"[lc] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[lc] at online CSS; waiting for match to start ...", flush=True)
            time.sleep(8.0); continue
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[lc] not in-game; abort (other machine may need to be in a match)", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[lc] meta-flush not present; abort", flush=True); dme.un_hook(); return 1
    print("[lc] meta-flush present; installing self-drive cave (L OFF) ...", flush=True)
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    print(f"[lc] hook = 0x{h.read_word(HOOK):08X}", flush=True)
    time.sleep(2.0)   # let the loop spin up

    for off, label in [(0, "r13"), (4, "ODB ptr"),
                       (8, "port +2 (INPUT_SOURCE)"), (12, "port +0 (LOCAL)")]:
        v = rd(h, 0x803FA480 + off)
        print(f"[lc] DEBUG 0x{0x803FA480+off:08X} {label:24s} = "
              f"{'0x%08X' % v if v is not None else '<read fail>'}", flush=True)

    # The local port can be 1 or 2 (host/guest). The self-drive loops whichever
    # the ODB says is local; find it by which port shows aerial activity.
    print("\n[lc] finding local port (self-drive loops the local player) ...", flush=True)
    port, pd, seen = find_local_port(h, 6)
    print(f"   port1 states: {[hex(s) for s in sorted(seen[1])]}", flush=True)
    print(f"   port2 states: {[hex(s) for s in sorted(seen[2])]}", flush=True)
    if port is None:
        print("[lc] no port shows aerials -- ODB local-port lookup may be wrong "
              "(try ODB offset +0 vs +2); abort", flush=True)
        dme.un_hook(); return 1
    print(f"[lc] LOCAL port = {port}, player data = 0x{pd:08X}", flush=True)

    print("\n[lc] === PHASE 1: L OFF (baseline) ~12s ===", flush=True)
    iw.write_instrs(h, L_ORIS_ADDR, [NOP])      # disable L
    print(f"[lc] L instr now 0x{h.read_word(L_ORIS_ADDR):08X} (nop)", flush=True)
    d0, maxlr0, c0, seen0, lc0 = measure(h, pd, 12, sample_lr=True)
    print(f"[lc] baseline landing durations: {d0}", flush=True)
    print(f"[lc] baseline LCancelStatus (1=success,2=fail): {dict(lc0)}", flush=True)
    print(f"[lc] baseline: cycles={c0} avg={sum(d0)/len(d0):.1f}f max_LR_timer={maxlr0}"
          if d0 else "[lc] baseline: no landings observed", flush=True)

    print("\n[lc] === PHASE 2: Z ON (pulsed every other aerial frame) ~12s ===", flush=True)
    if not ensure_hooked(h):
        print("[lc] dme detached, can't re-enable Z; abort", flush=True); return 1
    iw.write_instrs(h, L_ORIS_ADDR, [ORIS_Z])   # enable Z pulse
    print(f"[lc] Z instr now 0x{h.read_word(L_ORIS_ADDR):08X} (oris Z)", flush=True)
    d1, maxlr, c1, seen1, lc1 = measure(h, pd, 12, sample_lr=True)
    misfire = sorted((seen1 - seen0) & (GRAB | {AIRDODGE}))
    print(f"[lc] Z-on states: {[hex(s) for s in sorted(seen1)]}", flush=True)
    print(f"[lc] misfire states (grab/airdodge appearing only with Z on): "
          f"{[hex(s) for s in misfire] or 'NONE'}", flush=True)
    print(f"[lc] Z-on LCancelStatus (1=success,2=fail): {dict(lc1)}", flush=True)
    print(f"[lc] L-on landing durations: {d1}", flush=True)
    print(f"[lc] L-on: cycles={c1} avg={sum(d1)/len(d1):.1f}f max_LR_timer={maxlr}"
          if d1 else "[lc] L-on: no landings observed", flush=True)

    # verdict
    print("\n[lc] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[lc] still online: {top==0x0208} (scene 0x{top:04X})", flush=True)
    if d0 and d1:
        a0, a1 = sum(d0)/len(d0), sum(d1)/len(d1)
        print(f"[lc] baseline avg {a0:.1f}f  ->  L-on avg {a1:.1f}f", flush=True)
        if a1 <= 0.65 * a0:
            print(f"[lc] [PASS] landing lag ~halved -> L-cancel WORKS online "
                  f"(max LR timer {maxlr} <= 7: {maxlr<=7})", flush=True)
        else:
            print("[lc] [?] landing lag not clearly reduced -- inspect", flush=True)
    # Direct observable: LCancelStatus successes with Z on vs baseline.
    succ1, fail1 = lc1.get(1, 0), lc1.get(2, 0)
    print(f"[lc] LCancelStatus: baseline {dict(lc0)}  ->  Z-on {dict(lc1)}", flush=True)
    if succ1 and not misfire:
        print(f"[lc] [PASS] {succ1} success / {fail1} fail landings with Z on, "
              f"no grab/airdodge misfire (anchored cadence)", flush=True)
    elif misfire:
        print(f"[lc] [?] misfires present: {[hex(s) for s in misfire]} "
              f"-- trailing-spill guard may be needed (BUG 2)", flush=True)
    print("[lc] DONE. Dolphin left running. >>> P1 jumping/nairing? any desync? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
