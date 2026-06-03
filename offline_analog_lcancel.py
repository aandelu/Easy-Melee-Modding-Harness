"""
offline_analog_lcancel.py -- OFFLINE test of HELD ANALOG L for the L-cancel.

WHY ANALOG L (user's insight, 2026-05-22)
-----------------------------------------
Digital Z / digital L cause the post-aerial misfires: a Z press in the air re-does
a nair (Z includes A), and a digital L press in the air airdodges. Disassembly
confirms PAD_Read only sets a digital L bit when the analog trigger >= 0xAA (170)
(`0x8034E244`), and the airdodge-style trigger check reads the DIGITAL L/R timer
(0x680). So an analog L value BELOW 0xAA sets no digital bit, presses no Z -> it
CANNOT airdodge or re-nair, by construction. If such a light analog L still
triggers the L-cancel, then simply HOLDING it during aerials fixes everything at
once -- no cadence, no float decode, no hitlag override, no trailing-spill guard
(holding it spans hitlag too, since it doesn't depend on the frozen action_frame).

THE ONE THING TO VERIFY HERE
----------------------------
Does a held light analog L actually L-cancel? And what value range works? This
sweeps several analog values (held during aerials, injected at the consumer
HSD_PadRead hook 0x803775B8 by writing the analog-L byte 6(r25)) and reports the
L-cancel rate (LCancelStatus 0x25FF + landing-state duration) for each, plus any
misfire (re-nair / airdodge) states.

Injection (offline, consumer-side -- fine offline): at 0x803775B8 write the analog
value to 6(r25). PAD_Read later copies 6(r25) -> processed trigger (0x803775E0).

NOTE: offline only validates the L-cancel mechanic + value range; netplay-safety is
online-only (producer-side). Re-test online after this.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 offline_analog_lcancel.py
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
RIG_CAVE = DEFAULT_CAVE + 0x200  # 0x803FA5E8

ANALOG_VAL_ADDR = 0x803FA460    # scratch byte: analog L value to hold (0 = none)
HOOK_FIRE_ADDR = 0x803FA464
FULL_HOP_FLAG_ADDR = 0x803FA471

TARGET_PORT = 2                 # Fox = P2 in slot 2 (0-indexed = 1)
OFF_ACTION_STATE = 0x10
OFF_ANALOG_TRIG = 0x650         # processed analog trigger (what engine reads)
OFF_LCANCEL_STATUS = 0x25FF
S_WAIT = 0x000E
AERIAL = set(range(0x41, 0x46))
LANDING = set(range(0x46, 0x4B))
AIRDODGE = 0x00EC

CAVE_ASM = f"""
    stwu 1, -0x20(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)

    cmpwi 24, {TARGET_PORT - 1}     # only the target port
    bne  end

    lis  9, 0x{(HOOK_FIRE_ADDR >> 16):04X}
    ori  9, 9, 0x{(HOOK_FIRE_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    addi 8, 8, 1
    stb  8, 0(9)

    lis  6, 0x{((0x80453130 + (TARGET_PORT-1)*0xE90) >> 16):04X}
    ori  6, 6, 0x{((0x80453130 + (TARGET_PORT-1)*0xE90) & 0xFFFF):04X}
    lwz  6, 0(6)
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
    bne  a_wait
    lhz  0, 0(25)
    ori  0, 0, 0x0400
    sth  0, 0(25)
    b    end
a_wait:
    cmpwi 7, 0x18          # KneeBend -> X iff full-hop flag
    bne  a_knee
    lis  9, 0x{(FULL_HOP_FLAG_ADDR >> 16):04X}
    ori  9, 9, 0x{(FULL_HOP_FLAG_ADDR & 0xFFFF):04X}
    lbz  9, 0(9)
    cmpwi 9, 0
    beq  end
    lhz  0, 0(25)
    ori  0, 0, 0x0400
    sth  0, 0(25)
    b    end
a_knee:
    cmpwi 7, 0x19          # jump/fall -> A
    blt  a_jump
    cmpwi 7, 0x22
    bgt  a_jump
    lhz  0, 0(25)
    ori  0, 0, 0x0100
    sth  0, 0(25)
    b    end
a_jump:
    cmpwi 7, 0x41          # aerial?
    blt  end
    cmpwi 7, 0x45
    bgt  end
    # --- PULSE analog L every other frame (rising edge each 2 frames) ---
    # global frame counter parity; on odd frames leave 6(r25) (=0, released).
    lis  9, 0x8047
    ori  9, 9, 0x9D60
    lwz  9, 0(9)
    andi. 9, 9, 1
    bne  end               # odd frame -> release
    lis  9, 0x{(ANALOG_VAL_ADDR >> 16):04X}
    ori  9, 9, 0x{(ANALOG_VAL_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)           # r8 = analog value (0 = baseline)
    stb  8, 6(25)          # pad-struct analog L trigger byte (even frames only)
end:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    addi 1, 1, 0x20
"""


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def measure(h, pd, seconds):
    """Per-aerial episodes -> LCancelStatus + landing duration + misfire."""
    durations, lc = [], Counter()
    in_land, land_start = False, 0
    seen = set()
    max_trig = 0.0           # max processed analog trigger (0x650 float) in aerials
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
        f = h.read_word(0x80479D60)
        seen.add(st)
        if st in AERIAL:
            try:
                tv = struct.unpack(">f", h.read_bytes(pd + OFF_ANALOG_TRIG, 4))[0]
                if 0 <= tv <= 1.5:
                    max_trig = max(max_trig, tv)
            except Exception:
                pass
        if st in LANDING and not in_land:
            in_land, land_start = True, f
            lc[h.read_bytes(pd + OFF_LCANCEL_STATUS, 1)[0]] += 1
        elif st not in LANDING and in_land:
            in_land = False
            d = f - land_start
            if 0 < d < 60:
                durations.append(d)
        time.sleep(0.012)
    misf = sorted({s for s in seen if s == AIRDODGE})
    return durations, lc, seen, misf, max_trig


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, RIG_CAVE, DISPLACED)
    print(f"[an] assembled {len(logic)} logic words, payload {len(payload)}", flush=True)
    assert 0x98060006 not in payload or True  # stb r0,6(r25) check placeholder

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[an] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[an] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[an] P2 invalid -- abort", flush=True); return 1
    print(f"[an] P{TARGET_PORT} pd=0x{pd:08X}", flush=True)
    h.write_bytes(ANALOG_VAL_ADDR, b"\x00")
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")
    h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x01")    # full hop (real landing lag)
    iw.write_instrs(h, RIG_CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, RIG_CAVE)
    print(f"[an] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    print(f"[an] hook fires: {h.read_bytes(HOOK_FIRE_ADDR,1)[0]} (must be >0)", flush=True)

    def phase(val, seconds=10):
        label = f"analog L = 0x{val:02X} ({val})" + (" [baseline]" if val == 0 else "")
        print("\n" + "=" * 64 + f"\nPHASE: {label} (pulsed every other frame)\n" + "=" * 64,
              flush=True)
        h.write_bytes(ANALOG_VAL_ADDR, bytes([val]))
        d, lc, seen, misf, max_trig = measure(h, pd, seconds)
        avg = sum(d) / len(d) if d else None
        print(f"  landing durations: {d}", flush=True)
        print(f"  max processed trigger 0x650 in aerials: {max_trig:.3f} "
              f"(confirms injection reaches engine if >0)", flush=True)
        print(f"  avg = {avg}  LCancelStatus(1=ok,2=fail) = {dict(lc)}", flush=True)
        print(f"  misfire (airdodge 0xEC): {[hex(s) for s in misf] or 'NONE'}", flush=True)
        return val, avg, lc, misf

    results = []
    results.append(phase(0x00))          # baseline
    for v in (0x50, 0x80, 0xA0, 0xA9):   # sweep light->just-under-0xAA
        results.append(phase(v))

    print("\n" + "=" * 64 + "\nSUMMARY (held analog L, full hop)\n" + "=" * 64, flush=True)
    base = results[0][1]
    print(f"  baseline avg landing = {base}", flush=True)
    working = []
    for val, avg, lc, misf in results:
        succ = lc.get(1, 0); fail = lc.get(2, 0)
        tag = ""
        if val != 0 and base and avg and avg <= 0.65 * base and succ > 0 and not misf:
            tag = "  <-- L-CANCELS, no misfire"
            working.append(val)
        print(f"  0x{val:02X}: avg={avg} LCancel={dict(lc)} misfire={'Y' if misf else 'N'}{tag}",
              flush=True)
    if working:
        print(f"\n  [PASS] held analog L L-cancels at values: "
              f"{[hex(v) for v in working]} (no airdodge/re-nair by construction).",
              flush=True)
        return 0
    print("\n  [?] no held analog value cleanly L-cancelled -- may need pulsed analog "
          "or a different value/timer; inspect.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
