"""
online_cactuar_test.py -- ONLINE (netplay) test of the Cactuar dash veto.

Ports the offline-proven veto (offline_cactuar_macro.py) to the producer-side hook
so it's netplay-safe, and A/B tests it live. MANUAL drive: YOU run back and forth
and try to turn around (no self-drive -> no risk of SD-ing off a ledge in a real
match). The script toggles the override on/off (code toggle, rollback-safe) and
counts how the local player's run-reversals resolve.

Netplay rules honored (ONLINE_MACRO_GUIDE):
- Producer-side hook 0x8034E680 (PAD_Read, after stick calibration, upstream of the
  EXI scrape). Displaced lbz r0,7(r3) = 0x88030007; preserve r3 (calib ptr), r4
  (PADStatus), r13. Write stick bytes 2(r4)=X, 3(r4)=Y.
- Gate on the LOCAL player via ODB: port = *(*(r13-0x49E4)+0).
- Thresholds baked as IMMEDIATES (data scratch in 0x803FAxxx isn't rollback-safe).
- A/B by toggling the two override stb <-> nop (code, rollback-safe).
- One dme process; throttled, majority-vote, re-hook-on-fail reads.
- Cave 0x803FA600 (clear of the meta-flush control plane).

The veto state machine (same as offline):
  state==Squat 0x27                              -> override (X=0, Y=full down)  [HOLD]
  state in Run 0x15 / RunBrake 0x17 AND
    stickX extreme-opposite-to-facing AND |Y|<=YDEAD -> override               [TRIGGER]
  else                                           -> release (do nothing)

Online delay note: action state is read delayed (ODB_DELAY_FRAMES). We keep the
state-anchored logic (release at observed SquatWait onset) and MEASURE the result
here; if the real-time crouch runs long, compensate later.

Run (peer must be in an active in-game match; slot 4 baked with meta-flush):
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_cactuar_test.py
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
DISPLACED = 0x88030007          # lbz r0, 7(r3)
CAVE = 0x803FA600

R13 = 0x804DB6A0                 # SDA base (NTSC 1.02); ODB ptr at *(R13-0x49E4)
ODB_PTR_SLOT = R13 - 0x49E4     # 0x804D6CBC

OFF_ACTION_STATE = 0x10
OFF_FACING = 0x2C

S_TURN, S_TURNRUN = 0x0012, 0x0013
S_DASH, S_RUN, S_RUNBRAKE = 0x0014, 0x0015, 0x0017
S_SQUAT, S_SQUATWAIT = 0x0027, 0x0028

XTHRESH = 0x60                  # |stickX| opposite-extreme
YDEAD   = 0x18                  # |stickY| max for "Y=0"
DOWN_IMM = -128                 # full down (stb stores 0x80)

CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)

    # --- local player via ODB ---
    lwz  8, -0x49E4(13)
    cmpwi 8, 0
    beq  done
    srwi 9, 8, 24
    cmplwi 9, 0x80
    bne  done
    lbz  9, 0(8)               # local port (0-indexed)
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130
    add  5, 5, 9
    lwz  5, 0(5)               # GObj
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)            # local Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done

    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31     # action state

    # phase HOLD: in Squat(0x27) -> keep crouching, RELEASE once the squat frame
    # counter 0x894 >= (6 - ODB_DELAY_FRAMES). Reading the delay at runtime makes the
    # release land on the first actionable frame regardless of the machine's delay
    # (1-frame -> threshold 5, 2-frame -> threshold 4). 0x894 is a float (resets to
    # 1.0 per action state); decode to int without FPU.
    cmpwi 7, 0x27
    bne  chk_trig
    lbz  9, 0x21(8)          # ODB_DELAY_FRAMES (r8 = ODB ptr)
    subfic 9, 9, 6           # r9 = 6 - delay  (release threshold, integer)
    lwz  6, 0x894(5)         # squat frame counter (float bits)
    rlwinm 10, 6, 9, 24, 31  # exp = (bits >> 23) & 0xFF
    rlwinm 6, 6, 0, 9, 31    # mantissa = bits & 0x7FFFFF
    oris 6, 6, 0x0080        #          | 0x800000
    subfic 10, 10, 150       # shift = 150 - exp
    srw  6, 6, 10            # n = (int) squat frame
    cmpw 6, 9                # n vs threshold
    bge  done                # crouched long enough -> release (dash out)
    b    do_override

chk_trig:
    # phase TRIGGER: Run 0x15 / RunBrake 0x17 ?
    cmpwi 7, 0x15
    beq  trig
    cmpwi 7, 0x17
    bne  done
trig:
    # |stickY| <= YDEAD
    lbz  6, 3(4)
    extsb 6, 6
    srawi 9, 6, 31
    xor  6, 6, 9
    subf 6, 9, 6              # abs(stickY)
    cmpwi 6, {YDEAD}
    bgt  done
    # stickX opposite-extreme vs facing
    lwz  10, 0x2C(5)          # facing (sign = dir)
    lbz  6, 2(4)
    extsb 6, 6               # stickX signed
    cmpwi 10, 0
    blt  fac_left
    # facing right -> opposite LEFT: stickX <= -XTHRESH
    cmpwi 6, -{XTHRESH}
    bgt  done
    b    do_override
fac_left:
    # facing left -> opposite RIGHT: stickX >= +XTHRESH
    cmpwi 6, {XTHRESH}
    blt  done

do_override:
    li   6, 0
    stb  6, 2(4)             # stickX = 0   [TOGGLE A/B]
    li   6, {DOWN_IMM}
    stb  6, 3(4)             # stickY = down [TOGGLE A/B]

done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    addi 1, 1, 0x20
"""

STB_X = 0x98C40002              # stb r6, 2(r4)
STB_Y = 0x98C40003              # stb r6, 3(r4)
NOP = 0x60000000

THRESH_FRAMES = 4               # release crouch at squat frame >= this (TUNABLE)
THRESH_INIT_WORD = 0x3D2040A0   # lis r9, 0x40A0 == 5.0, the marker we patch


def thresh_word(frames):
    """lis r9, hi16(float(frames)) -- the early-release threshold instruction."""
    bits = struct.unpack(">I", struct.pack(">f", float(frames)))[0]
    return 0x3D200000 | (bits >> 16)


def assemble(asm):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(asm)
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0] for i in range(0, len(raw), 4)]


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


def maj_word(h, addr, n=5):
    vals = [rd(h, addr) for _ in range(n)]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


ACTIVE = {S_TURN, S_TURNRUN, S_DASH, S_RUN, S_RUNBRAKE, S_SQUAT, S_SQUATWAIT}


def find_local_by_activity(h, timeout=60):
    """Detect the LOCAL port by which one the human is moving (the cave gates on the
    real r13, so we just need to watch whichever port shows run/turn/crouch play)."""
    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    changes = {1: 0, 2: 0}
    prev = {1: None, 2: None}
    print("[ct] detecting which port you're playing (start running) ...", flush=True)
    t_end = time.time() + timeout
    while time.time() < t_end:
        for p in (1, 2):
            if pds[p] == -1:
                continue
            st = rd(h, pds[p] + OFF_ACTION_STATE)
            if st is None:
                continue
            st &= 0xFFFF
            if st != prev[p] and st in ACTIVE:
                changes[p] += 1
            prev[p] = st
        # decide once one port clearly shows movement
        if max(changes.values()) >= 4:
            break
        time.sleep(0.012)
    p = max(changes, key=changes.get)
    if changes[p] == 0:
        return None, None
    return p, pds[p]


CROUCH = {S_SQUAT, S_SQUATWAIT}


def observe_both(h, pds, seconds, label):
    """Watch BOTH ports (local port flips between connections). Count rising-edge
    entries into TurnRun/Turn/Squat and measure each crouch episode's length in
    FRAMES (enter Squat -> leave crouch) for each port."""
    print(f"\n  >>> {label} (~{seconds:.0f}s) <<<", flush=True)
    cnt = {1: Counter(), 2: Counter()}
    crouch = {1: [], 2: []}
    prev = {1: None, 2: None}
    inc = {1: False, 2: False}
    cs = {1: 0, 2: 0}
    t_end = time.time() + seconds
    while time.time() < t_end:
        f = rd(h, FRAME)
        if f is None:
            time.sleep(0.02); continue
        for p in (1, 2):
            if pds[p] == -1:
                continue
            st = rd(h, pds[p] + OFF_ACTION_STATE)
            if st is None:
                continue
            st &= 0xFFFF
            if st != prev[p]:
                if st == S_TURNRUN:
                    cnt[p]["TurnRun(0x13,slow)"] += 1
                elif st == S_TURN:
                    cnt[p]["Turn(0x12,pivot)"] += 1
                elif st == S_SQUAT:
                    cnt[p]["Squat(0x27,crouch)"] += 1
                prev[p] = st
            if st in CROUCH and not inc[p]:
                inc[p], cs[p] = True, f
            elif st not in CROUCH and inc[p]:
                inc[p] = False
                d = f - cs[p]
                if 0 < d < 60:
                    crouch[p].append(d)
        time.sleep(0.02)        # throttled to minimize dme-induced Dolphin hitching
    for p in (1, 2):
        avg = f"  avg={sum(crouch[p])/len(crouch[p]):.1f}f" if crouch[p] else ""
        print(f"  P{p} edges: {dict(cnt[p])}   crouch lens: {crouch[p]}{avg}", flush=True)
    return cnt, crouch


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)
    # locate the two override stores to toggle for A/B
    ix = payload.index(STB_X)
    iy = payload.index(STB_Y)
    STBX_ADDR, STBY_ADDR = CAVE + ix * 4, CAVE + iy * 4
    print(f"[ct] cave {len(payload)} words; override stb X@0x{STBX_ADDR:08X} "
          f"Y@0x{STBY_ADDR:08X} (release threshold = 6 - delay, computed in-cave)",
          flush=True)
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    code = b"".join(w.to_bytes(4, "big") for w in payload)
    for i in md.disasm(code, CAVE):
        if i.address in (STBX_ADDR, STBY_ADDR) or i.address >= CAVE + len(payload) * 4 - 8:
            print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
                  flush=True)
    assert payload[-2] == DISPLACED, "displaced lbz r0,7(r3) must precede branch-back"

    kill_stale()
    h = Harness()
    print("[ct] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[ct] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(12.0)
    online = False
    for i in range(12):                       # patient: ~12 tries
        top, cc = scene_maj(h)
        print(f"[ct] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        if top == 0x0008:                     # online CSS -> press Enter to start search
            print("[ct] online CSS; pressing Enter, waiting (peer must be ready) ...",
                  flush=True)
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
            time.sleep(8.0); continue
        # garbage/transition (e.g. mid-connect): just wait; re-press Enter every few tries
        if i % 3 == 2:
            mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
        time.sleep(6.0)
    if not online:
        print("[ct] not in-game; abort. The 2nd machine must be ready to direct-connect "
              "(in/awaiting a match), and close any Slippi you opened yourself.", flush=True)
        dme.un_hook(); return 1

    if (h.read_word(iw.META_FLUSH_HOOK) & 0xFC000000) != 0x48000000:
        print("[ct] meta-flush not present in slot 4; abort", flush=True)
        dme.un_hook(); return 1

    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    print(f"[ct] hook 0x{HOOK:08X} = 0x{h.read_word(HOOK):08X}", flush=True)
    time.sleep(2.0)

    # veto ON (the cave's stb are already real; make sure)
    iw.write_instrs(h, STBX_ADDR, [STB_X])
    iw.write_instrs(h, STBY_ADDR, [STB_Y])
    # read the connection's input delay (cave releases the crouch at squat frame 6-delay)
    delay = None
    try:
        odb = maj_word(h, ODB_PTR_SLOT)
        if odb and (odb >> 24) == 0x80:
            delay = h.read_bytes(odb + 0x21, 1)[0]
    except Exception:
        pass
    print(f"[ct] veto ON; ODB_DELAY_FRAMES = {delay} -> release at squat frame "
          f"{6 - delay if delay is not None else '?'}", flush=True)

    pds = {1: h.player_data_ptr(1), 2: h.player_data_ptr(2)}
    print(f"[ct] watching both ports: P1=0x{pds[1]:08X} P2=0x{pds[2]:08X}", flush=True)
    print("[ct] NOTE: get into a FULL RUN (hold one direction until sprinting), THEN "
          "slam the opposite way. Dash-dancing won't trigger it (Dash is excluded).",
          flush=True)

    cnt, crouch = observe_both(h, pds, 35,
                               "VETO ON: full-run then reverse, repeat")

    print("\n[ct] === VERDICT ===", flush=True)
    top, cc = scene_maj(h)
    print(f"[ct] still online: {top == 0x0208} (scene 0x{top:04X})", flush=True)
    # the active port is whichever saw movement
    act = max((1, 2), key=lambda p: sum(cnt[p].values()))
    on_turn = cnt[act].get("TurnRun(0x13,slow)", 0)
    on_squat = cnt[act].get("Squat(0x27,crouch)", 0)
    cl = crouch[act]
    print(f"[ct] active port P{act}: TurnRun={on_turn} Squat={on_squat}", flush=True)
    if cl:
        print(f"[ct] online crouch avg {sum(cl)/len(cl):.1f}f (delay-comp: release at "
              f"squat frame 6-delay)", flush=True)
    if on_squat > 0 and on_turn == 0:
        print(f"[ct] [PASS] veto fires online: {on_squat} run-reversals -> crouch, "
              f"0 slow TurnRun. (offline A/B already proved the swap.)", flush=True)
    elif on_squat > 0:
        print(f"[ct] [PARTIAL] {on_squat} crouches + {on_turn} TurnRun slipped "
              f"through (stick not full extreme, or delay; inspect).", flush=True)
    else:
        print("[ct] [?] no crouches -- get into a FULL RUN (not dash-dance) then "
              "reverse; inspect edges above.", flush=True)
    print("[ct] DONE. Dolphin left running. >>> any desync on your screen? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
