"""
offline_lcancel_anchor.py -- OFFLINE validation of the action-frame-ANCHORED
L-cancel cadence (the BUG-1 timing fix), runnable without the online setup.

Why this exists
---------------
The online macro's cadence used to be keyed to the GLOBAL frame counter
(`0x80479D60`) with a `frame % 7 == 0` gate. That cadence has a random 0-6 frame
phase relative to the aerial, so short / late / near-ground aerials could end
before any press frame fell inside them and never L-cancelled (BUG 1). The fix is
to anchor the cadence to the in-game **Action State Frame Counter**
(Player Data + 0x894, a FLOAT that resets to 1.0 on each new action state), which
is rollback-safe (part of game state) and gives a deterministic phase: the first
press lands on the first aerial frame.

0x894 is a float; the cave decodes it to an integer with PURE INTEGER ops (no
FPU): for an integer-valued single n.0 in [1, 2^24),
    n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exponent)
then presses Z when `(n - 1) % 7 == 0` (n in {1, 8, 15, ...}).

This rig reproduces the cadence + L-cancel mechanic OFFLINE (slot-2 savestate,
self-drive at the consumer HSD_PadRead hook 0x803775B8) -- the same decode +
modulo logic that ships in `make_online_lcancel_gecko.py`. It proves three things
without needing the user's online opponent:
  1. 0x894 really counts 1,2,3,... during a NAIR and resets on state change
     (Python reads the float directly).
  2. The in-cave INTEGER decode matches int(action_frame) in-situ (the cave
     stores its computed n + remainder to scratch; Python compares).
  3. The anchored Z cadence L-cancels: landing duration ~15f -> ~7f AND
     LCancelStatus (Player Data + 0x25FF) == 1 (success), with no airdodge.

NOTE: offline has no 2-frame netplay delay, so this validates the decode/cadence
LOGIC, not the online delay/netplay-safety (those are online-only -- run
`online_lcancel_selfdrive.py` for the full online confirmation).

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_lcancel_anchor.py
"""
import struct
import subprocess
import sys
import time
from collections import Counter

import keystone

from melee_harness import DEFAULT_CAVE, finalize_payload, Harness
import instr_writer as iw

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000          # lhz r0, 0(r25)
RIG_CAVE = DEFAULT_CAVE + 0x200  # 0x803FA5E8 (past the meta-flush body)

# Scratch (between the meta-flush control plane and the cave; addresses proven
# safe by auto_lcancel/lcancel_rig.py + online_lcancel_selfdrive.py).
Z_FLAG_ADDR = 0x803FA460        # 0 -> baseline (no Z); 1 -> Z on
HOOK_FIRE_ADDR = 0x803FA464     # incremented every hook fire (liveness)
FULL_HOP_FLAG_ADDR = 0x803FA471 # 1 -> press X in KneeBend (full hop)
DBG_N_ADDR = 0x803FA480         # cave stores its decoded integer action_frame
DBG_REM_ADDR = 0x803FA484       # cave stores (n-1) % 7

TARGET_PORT = 2                 # Fox = P2 in slot 2 (0-indexed port = 1)
OFF_ACTION_STATE = 0x10
OFF_ACTION_FRAME = 0x894        # FLOAT, resets to 1.0 each action state
OFF_LCANCEL_STATUS = 0x25FF     # u8: 0=none 1=success 2=fail
S_WAIT = 0x000E
AERIAL = set(range(0x41, 0x46))
LANDING = set(range(0x46, 0x4B))
AIRDODGE = 0x00EC

# Self-drive + anchored-Z cave (consumer HSD_PadRead; offline-only). Mirrors the
# online cave's decode+cadence, but injects via the (r25) pad halfword and
# hardcodes P2 (no ODB offline).
CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    cmpwi 24, {TARGET_PORT - 1}     # only the target port's pass
    bne  end

    lis  9, 0x{(HOOK_FIRE_ADDR >> 16):04X}   # liveness counter
    ori  9, 9, 0x{(HOOK_FIRE_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    addi 8, 8, 1
    stb  8, 0(9)

    lis  6, 0x{((0x80453130 + (TARGET_PORT-1)*0xE90) >> 16):04X}
    ori  6, 6, 0x{((0x80453130 + (TARGET_PORT-1)*0xE90) & 0xFFFF):04X}
    lwz  6, 0(6)            # GObj ptr
    cmpwi 6, 0
    beq  end
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  end
    lwz  6, 0x2C(6)         # Player Data
    cmpwi 6, 0
    beq  end
    srwi 9, 6, 24
    cmplwi 9, 0x80
    bne  end

    lwz  7, 0x10(6)        # action state
    rlwinm 7, 7, 0, 16, 31

    cmpwi 7, 0x0E          # Wait -> X
    bne  n_wait
    lhz  0, 0(25)
    ori  0, 0, 0x0400
    sth  0, 0(25)
    b    end
n_wait:
    cmpwi 7, 0x18          # KneeBend -> X iff FULL_HOP flag (full hop)
    bne  n_knee
    lis  9, 0x{(FULL_HOP_FLAG_ADDR >> 16):04X}
    ori  9, 9, 0x{(FULL_HOP_FLAG_ADDR & 0xFFFF):04X}
    lbz  9, 0(9)
    cmpwi 9, 0
    beq  end
    lhz  0, 0(25)
    ori  0, 0, 0x0400
    sth  0, 0(25)
    b    end
n_knee:
    cmpwi 7, 0x19          # jump/fall -> A (start aerial)
    blt  n_jump
    cmpwi 7, 0x22
    bgt  n_jump
    lhz  0, 0(25)
    ori  0, 0, 0x0100
    sth  0, 0(25)
    b    end
n_jump:
    cmpwi 7, 0x41          # aerial?
    blt  end
    cmpwi 7, 0x45
    bgt  end

    # --- anchored cadence: decode the FLOAT action_frame at pdata+0x894 ---
    lwz  8, 0x894(6)       # r8 = action_frame float bits
    rlwinm 9, 8, 9, 24, 31 # r9 = exponent (IEEE bits 1..8)
    rlwinm 8, 8, 0, 9, 31  # r8 = mantissa (low 23)
    oris 8, 8, 0x0080      # r8 |= 0x800000 (implicit leading 1)
    subfic 9, 9, 150       # r9 = 150 - exponent
    srw  8, 8, 9           # r8 = (int) action_frame
    lis  9, 0x{(DBG_N_ADDR >> 16):04X}        # DEBUG: store decoded n
    ori  9, 9, 0x{(DBG_N_ADDR & 0xFFFF):04X}
    stw  8, 0(9)
    addi 8, 8, -1
    li   9, 7
    divw 6, 8, 9
    mulli 6, 6, 7
    subf 8, 6, 8           # r8 = (n-1) % 7
    lis  9, 0x{(DBG_REM_ADDR >> 16):04X}      # DEBUG: store remainder
    ori  9, 9, 0x{(DBG_REM_ADDR & 0xFFFF):04X}
    stw  8, 0(9)
    cmpwi 8, 0
    bne  end               # press only when (n-1)%7==0
    lis  9, 0x{(Z_FLAG_ADDR >> 16):04X}       # gate on Z flag (A/B)
    ori  9, 9, 0x{(Z_FLAG_ADDR & 0xFFFF):04X}
    lbz  9, 0(9)
    cmpwi 9, 0
    beq  end
    lhz  0, 0(25)          # press Z
    ori  0, 0, 0x0010
    sth  0, 0(25)
end:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
"""


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def read_float(h, addr):
    return struct.unpack(">f", h.read_bytes(addr, 4))[0]


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, RIG_CAVE, DISPLACED)
    print(f"[off] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)
    # capstone sanity is done in the build-check; here just confirm Z present once
    assert payload.count(0x64000010) == 0, "this offline cave injects via (r25), not oris"

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[off] launching Dolphin ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[off] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[off] P2 player data invalid -- abort", flush=True); return 1
    print(f"[off] P{TARGET_PORT} pd=0x{pd:08X}", flush=True)

    for a in (Z_FLAG_ADDR, HOOK_FIRE_ADDR, FULL_HOP_FLAG_ADDR, DBG_N_ADDR, DBG_REM_ADDR):
        h.write_bytes(a, b"\x00\x00\x00\x00"[: 4 if a in (DBG_N_ADDR, DBG_REM_ADDR) else 1])

    iw.write_instrs(h, RIG_CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, RIG_CAVE)
    print(f"[off] hook 0x{HOOK_ADDR:08X} = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(HOOK_FIRE_ADDR, 1)[0]
    print(f"[off] hook fires after 15f: {fires} (must be >0)", flush=True)

    def run_phase(label, z_on, full_hop, seconds=12):
        print("\n" + "=" * 66, flush=True)
        print(label, flush=True)
        print("=" * 66, flush=True)
        h.write_bytes(Z_FLAG_ADDR, b"\x01" if z_on else b"\x00")
        h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x01" if full_hop else b"\x00")
        durations, lc = [], Counter()
        in_land, land_start = False, 0
        seen = set()
        # decode validation: collect (python_int(0x894), cave_n) pairs in aerials
        decode_pairs = []
        frame_seq = []     # (action_frame_float) during aerials, to show counting
        t_end = time.time() + seconds
        last_state = None
        while time.time() < t_end:
            st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
            seen.add(st)
            if st in AERIAL:
                af = read_float(h, pd + OFF_ACTION_FRAME)
                cave_n = h.read_word(DBG_N_ADDR)
                decode_pairs.append((int(af), cave_n))
                if last_state not in AERIAL:
                    frame_seq.append([])     # new aerial
                if frame_seq:
                    frame_seq[-1].append(round(af, 2))
            f = h.read_word(0x80479D60)
            if st in LANDING and not in_land:
                in_land, land_start = True, f
                lc[h.read_bytes(pd + OFF_LCANCEL_STATUS, 1)[0]] += 1
            elif st not in LANDING and in_land:
                in_land = False
                d = f - land_start
                if 0 < d < 60:
                    durations.append(d)
            last_state = st
            time.sleep(0.012)
        ad = AIRDODGE in seen
        print(f"  landing durations: {durations}", flush=True)
        print(f"  LCancelStatus (1=success,2=fail): {dict(lc)}", flush=True)
        if frame_seq:
            print(f"  action_frame sequence (first aerial): {frame_seq[0][:12]}",
                  flush=True)
        # decode match rate (allow +-1 for frame skew between the two reads)
        if decode_pairs:
            ok = sum(1 for pyn, cn in decode_pairs if abs(pyn - cn) <= 1)
            print(f"  in-cave decode match: {ok}/{len(decode_pairs)} "
                  f"(cave n vs int(0x894), +-1 skew)", flush=True)
        if ad:
            print("  *** AIRDODGE (0x00EC) observed ***", flush=True)
        return durations, lc, ad, decode_pairs, frame_seq

    d0, lc0, ad0, _, fs0 = run_phase("PHASE 1: full hop / Z OFF (baseline ~15f)",
                                     z_on=False, full_hop=True)
    d1, lc1, ad1, dp1, fs1 = run_phase("PHASE 2: full hop / Z ON anchored (expect ~7f)",
                                       z_on=True, full_hop=True)

    print("\n" + "=" * 66 + "\nSUMMARY\n" + "=" * 66, flush=True)
    a0 = sum(d0) / len(d0) if d0 else None
    a1 = sum(d1) / len(d1) if d1 else None
    print(f"  baseline avg = {a0}  Z-on avg = {a1}", flush=True)
    print(f"  baseline LCancelStatus {dict(lc0)}  ->  Z-on {dict(lc1)}", flush=True)

    fails = []
    # 1) action_frame (0x894) counts UP within each aerial and RESETS between
    #    aerials. NOTE: ~12ms Python polling can't reliably catch frame 1 of any
    #    given aerial (it samples wherever the poll lands), so we don't require a
    #    sequence to *start* at 1 -- we require monotonic counting within an aerial
    #    and a reset across aerials. (The cave runs every frame and presses on
    #    n in {1,8,15,..} deterministically; the L-cancel success rate below is the
    #    real proof the first press lands on frame 1.)
    allfs = [s for s in (fs0 + fs1) if s]
    counts_up = any(len(s) >= 3 and s == sorted(s) and (s[-1] - s[0]) >= 3
                    for s in allfs)
    starts = [s[0] for s in allfs]
    maxes = [max(s) for s in allfs]
    reset_seen = len(allfs) >= 2 and starts and min(starts) < max(maxes) - 3
    if not (counts_up and reset_seen):
        fails.append(f"action_frame (0x894) counting unclear "
                     f"(counts_up={counts_up} reset_seen={reset_seen}); "
                     f"per-aerial (start,end)={[(s[0], s[-1]) for s in allfs[:6]]}")
    # 2) decode matches
    if dp1:
        ok = sum(1 for pyn, cn in dp1 if abs(pyn - cn) <= 1)
        if ok < 0.9 * len(dp1):
            fails.append(f"in-cave decode mismatch ({ok}/{len(dp1)})")
    else:
        fails.append("no aerial decode samples captured")
    # 3) L-cancel works
    if a0 and a1:
        if a1 / a0 > 0.65:
            fails.append(f"Z-on didn't L-cancel ({a0:.1f}f -> {a1:.1f}f)")
    else:
        fails.append(f"missing landings (baseline d0={d0}, Z-on d1={d1})")
    if lc1.get(1, 0) == 0:
        fails.append(f"no LCancelStatus=success with Z on ({dict(lc1)})")
    if ad1:
        fails.append("airdodge observed with Z on")

    if fails:
        for fmsg in fails:
            print(f"  [FAIL] {fmsg}", flush=True)
        return 1
    print(f"\n  [PASS] anchored cadence L-cancels offline: {a0:.1f}f -> {a1:.1f}f, "
          f"LCancelStatus successes={lc1.get(1,0)}, in-cave decode verified, "
          f"no airdodge.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
