"""
online_analog_selfdrive.py -- ONLINE self-drive test of the ANALOG-L L-cancel.

The macro now PULSES a light ANALOG L (value 0x80, below the 0xAA digital
threshold) every other frame during aerials, instead of digital Z. Offline this
L-cancelled at 6.8f with ZERO misfires (offline_analog_lcancel.py). Because the
analog value is < 0xAA it sets no digital button bit and presses no Z, so it
CANNOT airdodge or re-nair -- this is what fixes the trailing-spill (BUG 2) and,
because the every-other-frame pulse is keyed to the global frame counter (which
keeps ticking through hitlag, unlike action_frame), it also fixes the hitlag miss.
No float decode, no cadence anchor, no hitlag override needed.

INJECTION POINT (producer-side, found by disasm -- disasm_lcancel_analog.py):
PAD_Read finalizes the analog L byte 6(r4) at 0x8034E67C (per-port calibration);
the report-builder returns at 0x8034E69C. So we hook 0x8034E680 (analog L is final,
function hasn't returned, well upstream of TriggerSendInput's EXI scrape) and write
6(r4). r4 = the local PADStatus; r3 = calibration ptr (preserved for the displaced
lbz r0,7(r3)).

This self-drives the LOCAL player (jump->nair->land) by ORing buttons into 0(r4)
and pulses analog L into 6(r4), both at the one hook. A/B: the analog store is
toggled nop<->stb to compare baseline vs analog-on in one session.

Run (peer must be in an active in-game match):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_analog_selfdrive.py
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
HOOK = 0x8034E680
DISPLACED = 0x88030007          # lbz r0, 7(r3)  (R-trigger calibration load)
CAVE = 0x803FA600

OFF_ACTION_STATE = 0x10
OFF_LCANCEL_STATUS = 0x25FF
OFF_HITLAG = 0x195C
AERIAL = set(range(0x41, 0x46))
LANDING = set(range(0x46, 0x4B))
GRAB = set(range(0xD4, 0xD7))
AIRDODGE = 0x00EC
ANALOG_VAL = 0x80               # light analog L (< 0xAA -> no digital bit)

CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)

    lwz  8, -0x49E4(13)     # ODB ptr -> local player
    cmpwi 8, 0
    beq  done
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  done
    lbz  9, 0(8)
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)        # r5 = local Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done

    lwz  7, 0x10(5)       # action state
    rlwinm 7, 7, 0, 16, 31

    cmpwi 7, 0x0E         # Wait -> X
    bne  sd1
    lhz  6, 0(4)
    ori  6, 6, 0x0400
    sth  6, 0(4)
    b    done
sd1:
    cmpwi 7, 0x18         # KneeBend -> X (full hop)
    bne  sd2
    lhz  6, 0(4)
    ori  6, 6, 0x0400
    sth  6, 0(4)
    b    done
sd2:
    cmpwi 7, 0x19         # jump/fall -> A (nair)
    blt  sd3
    cmpwi 7, 0x22
    bgt  sd3
    lhz  6, 0(4)
    ori  6, 6, 0x0100
    sth  6, 0(4)
    b    done
sd3:
    cmpwi 7, 0x41         # aerial?
    blt  done
    cmpwi 7, 0x45
    bgt  done
    # --- pulse analog L every other frame (global parity) ---
    lis  9, 0x8047
    ori  9, 9, 0x9D60
    lwz  9, 0(9)
    andi. 9, 9, 1
    bne  done            # odd frame -> release (leave 6(r4))
    li   6, 0x{ANALOG_VAL:02X}
    stb  6, 6(4)         # ANALOG L into local PADStatus (toggled nop<->stb for A/B)
done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
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
    durations, lc = [], Counter()
    in_land, land_start = False, 0
    seen = set()
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = rd(h, pd + OFF_ACTION_STATE)
        f = rd(h, FRAME)
        if st is None or f is None:
            time.sleep(0.02); continue
        st &= 0xFFFF
        seen.add(st)
        if st in LANDING and not in_land:
            in_land, land_start = True, f
            ls = rdb(h, pd + OFF_LCANCEL_STATUS)
            if ls is not None:
                lc[ls] += 1
        elif st not in LANDING and in_land:
            in_land = False
            d = f - land_start
            if 0 < d < 60:
                durations.append(d)
        time.sleep(0.012)
    misf = sorted({s for s in seen if s in (GRAB | {AIRDODGE})})
    return durations, lc, seen, misf


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)
    STB = 0x98C40006        # stb r6, 6(r4)  -- the analog injection (toggle)
    NOP = 0x60000000
    i_stb = payload.index(STB)
    STB_ADDR = CAVE + i_stb * 4
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print(f"[ana] cave verify ({len(payload)} words); analog stb @ 0x{STB_ADDR:08X}", flush=True)
    code = b"".join(w.to_bytes(4, 'big') for w in payload)
    for i in md.disasm(code, CAVE):
        if "6(r4)" in i.op_str or i.bytes.hex().upper() in ("88030007",) or i.address >= CAVE + len(payload)*4 - 12:
            print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)
    assert payload[-2] == DISPLACED, "displaced lbz r0,7(r3) must precede the branch-back"

    kill_stale()
    h = Harness()
    print("[ana] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[ana] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(5):
        top, cc = scene_maj(h)
        print(f"[ana] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:
            print("[ana] at online CSS; peer must be IN-GAME (0x0208). waiting ...", flush=True)
            time.sleep(8.0); continue
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[ana] not in-game; abort (peer must be in an active match)", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[ana] meta-flush not present; abort", flush=True); dme.un_hook(); return 1
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    print(f"[ana] hook 0x{HOOK:08X} = 0x{h.read_word(HOOK):08X}", flush=True)
    time.sleep(2.0)

    port, pd, seen = find_local_port(h, 6)
    if port is None:
        print(f"[ana] no aerials on either port; states {seen}; abort", flush=True)
        dme.un_hook(); return 1
    print(f"[ana] LOCAL port = {port}, pd = 0x{pd:08X}", flush=True)

    print("\n[ana] === PHASE 1: analog OFF (baseline) ~12s ===", flush=True)
    iw.write_instrs(h, STB_ADDR, [NOP])
    print(f"[ana] analog instr = 0x{h.read_word(STB_ADDR):08X} (nop)", flush=True)
    d0, lc0, s0, m0 = measure(h, pd, 12)
    a0 = sum(d0)/len(d0) if d0 else None
    print(f"[ana] baseline durations {d0}  avg={a0}  LCancelStatus={dict(lc0)}", flush=True)

    print("\n[ana] === PHASE 2: analog L ON (pulsed 0x%02X) ~12s ===" % ANALOG_VAL, flush=True)
    if not ensure_hooked(h):
        print("[ana] dme detached; abort", flush=True); return 1
    iw.write_instrs(h, STB_ADDR, [STB])
    print(f"[ana] analog instr = 0x{h.read_word(STB_ADDR):08X} (stb r6,6(r4))", flush=True)
    d1, lc1, s1, m1 = measure(h, pd, 12)
    a1 = sum(d1)/len(d1) if d1 else None
    misf = sorted((s1 - s0) & (GRAB | {AIRDODGE}))
    print(f"[ana] analog-on durations {d1}  avg={a1}  LCancelStatus={dict(lc1)}", flush=True)
    print(f"[ana] misfire states (only with analog on): {[hex(x) for x in misf] or 'NONE'}",
          flush=True)

    print("\n[ana] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[ana] still online: {top==0x0208} (scene 0x{top:04X})", flush=True)
    print(f"[ana] baseline LCancelStatus {dict(lc0)} -> analog {dict(lc1)}", flush=True)
    ok = (a0 and a1 and a1 <= 0.65 * a0 and lc1.get(1, 0) > 0 and not misf)
    if ok:
        print(f"[ana] [PASS] analog L L-cancels online ({a0:.1f}f -> {a1:.1f}f), "
              f"{lc1.get(1,0)} successes, no misfire", flush=True)
    else:
        print("[ana] [?] inspect (durations/LCancelStatus/misfire above)", flush=True)
    print("[ana] DONE. Dolphin left running. >>> any desync on your screen? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
