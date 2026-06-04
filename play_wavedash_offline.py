"""
play_wavedash_offline.py -- install the wavedash macro OFFLINE and hand YOU the
controls (slot 2, WASD/keyboard or pad). No simulated input: the cave reads your
real stick and only adds the wavedash when you hold up.

vs offline_wavedash_macro.py (which self-drove a simulated held-up): this is the
playable build --
  * NO sim preamble: reads your actual stickY/stickX.
  * applies to ALL ports (play Marth P1 or Fox P2 -- gated on up-held, so the
    other player is unaffected).
  * CHARACTER-AGNOSTIC jumpsquat: reads "Jump startup time" (Player Data 0x148)
    per character and fires the airdodge on asfc == jumpsquat-1, so Marth's 4f
    jumpsquat works the same as Fox's 3f. (Handles int OR float encoding.)

Logic (per port, every frame):
  * up held (stickY >= UP_THRESH)? else pass through.
  * KneeBend(0x18) AND asfc == (jumpsquat-1) -> press L + airdodge stick
    (right=(0x6A,0xE0)/left=(0x96,0xE0)/down=(0,0x90) from held stickX) -> wavedash.
  * grounded-actionable (0x0E..0x17) -> press Y (jump): drives the first jump,
    repeats while up is held, and buffers out of landing lag.

Offline only (consumer hook 0x803775B8); delay=0. Installs, prints how to play,
and LEAVES Dolphin running so you can play. Re-run to reinstall after a reset.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 play_wavedash_offline.py
"""
import struct
import subprocess
import sys
import time

import keystone

from melee_harness import finalize_payload, Harness
import instr_writer as iw

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000            # lhz r0, 0(r25)
CAVE = 0x803FA600

UPTHRESH_ADDR = 0x803FA460        # byte: up-trigger threshold (tunable live)
FIRE_ADDR     = 0x803FA461        # byte: liveness
WD_PEND_BASE  = 0x803FA470        # 4 bytes: per-port "wavedash pending" latch
WD_DIR_BASE   = 0x803FA474        # 4 signed bytes: per-port latched direction (stickX)

OFF_JUMPSQUAT = 0x148             # "Jump startup time" (per-character jumpsquat)

CAVE_ASM = f"""
    stwu 1, -0x30(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)
    stw  11, 0x20(1)
    stw  12, 0x24(1)

    # liveness
    lis  9, 0x{(FIRE_ADDR >> 16):04X}
    ori  9, 9, 0x{(FIRE_ADDR & 0xFFFF):04X}
    lbz  8, 0(9)
    addi 8, 8, 1
    stb  8, 0(9)

    # resolve this port's Player Data via r24 (the port being pad-read)
    lis  5, 0x8045
    ori  5, 5, 0x3130
    mulli 9, 24, 0xE90
    add  5, 5, 9
    lwz  5, 0(5)               # GObj
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done
    lwz  5, 0x2C(5)            # Player Data
    cmpwi 5, 0
    beq  done
    srwi 9, 5, 24
    cmplwi 9, 0x80
    bne  done

    # per-port latch address: r11 = &WD_PEND[port]  (direction is read LIVE, not latched)
    lis  11, 0x{(WD_PEND_BASE >> 16):04X}
    ori  11, 11, 0x{(WD_PEND_BASE & 0xFFFF):04X}
    add  11, 11, 24

    # state -> r7
    lwz  7, 0x10(5)
    rlwinm 7, 7, 0, 16, 31

    # ===== KneeBend: airdodge iff a wavedash is latched (NOT gated on current up) =====
    cmpwi 7, 0x18
    bne  not_kneebend
    lbz  6, 0(11)              # WD_PEND[port]
    cmpwi 6, 0
    beq  done                 # plain jump (not from up) -> no airdodge
    # jumpsquat (0x148): robust int-or-float -> r8
    lwz  8, 0x148(5)
    cmplwi 8, 0x100
    blt  js_int
    rlwinm 10, 8, 9, 24, 31
    rlwinm 8, 8, 0, 9, 31
    oris 8, 8, 0x0080
    subfic 10, 10, 150
    srw  8, 8, 10
js_int:
    addi 8, 8, -1             # LJF = jumpsquat - 1
    lwz  6, 0x894(5)          # asfc -> r6
    rlwinm 10, 6, 9, 24, 31
    rlwinm 6, 6, 0, 9, 31
    oris 6, 6, 0x0080
    subfic 10, 10, 150
    srw  6, 6, 10
    cmpw 6, 8                 # asfc == LJF ?
    bne  done
    # AIRDODGE: digital L + airdodge stick from the LATCHED direction
    lhz  9, 0(25)
    ori  9, 9, 0x0040
    sth  9, 0(25)
    lbz  10, 2(25)           # CURRENT stickX read LIVE here (last jumpsquat frame)
    extsb 10, 10             # -> you can switch direction any time during jumpsquat
    cmpwi 10, 0x30
    bge  ad_right
    cmpwi 10, -0x30
    ble  ad_left
    li   8, 0                 # down: (0, -0x70)
    li   6, -112
    b    ad_set
ad_right:
    li   8, 0x6A              # right: (+0x6A, -0x20)
    li   6, -32
    b    ad_set
ad_left:
    li   8, -0x6A             # left: (-0x6A, -0x20)
    li   6, -32
ad_set:
    stb  8, 2(25)
    stb  6, 3(25)
    li   6, 0
    stb  6, 0(11)            # consume the latch
    b    done

not_kneebend:
    # failsafe: clear a stale latch if we left the ground without airdodging
    lbz  6, 0(11)
    cmpwi 6, 0
    beq  chk_jump
    cmpwi 7, 0x0E
    blt  clear_pend
    cmpwi 7, 0x17
    bgt  clear_pend
    b    chk_jump             # grounded-actionable: keep latch (re-set below)
clear_pend:
    li   6, 0
    stb  6, 0(11)
    b    done

chk_jump:
    # up held? stickY (3(25), signed) >= UP_THRESH
    lbz  6, 3(25)
    extsb 6, 6
    lis  9, 0x{(UPTHRESH_ADDR >> 16):04X}
    ori  9, 9, 0x{(UPTHRESH_ADDR & 0xFFFF):04X}
    lbz  10, 0(9)
    cmpw 6, 10
    blt  done
    # grounded-actionable (0x0E..0x17) ?
    cmpwi 7, 0x0E
    blt  done
    cmpwi 7, 0x17
    bgt  done
    # press Y (jump) + LATCH wavedash pending and the held direction
    lhz  9, 0(25)
    ori  9, 9, 0x0800
    sth  9, 0(25)
    li   6, 1
    stb  6, 0(11)            # WD_PEND[port] = 1 (commit wavedash; direction stays LIVE)

done:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    lwz  12, 0x24(1)
    addi 1, 1, 0x30
"""


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned no output")
    return [struct.unpack(">I", bytes(raw[i:i + 4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"],
                      capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def read_jumpsquat(h, port):
    pd = h.player_data_ptr(port)
    if pd == -1:
        return None, None, None
    raw = struct.unpack(">I", h.read_bytes(pd + OFF_JUMPSQUAT, 4))[0]
    as_int = raw
    as_float = struct.unpack(">f", struct.pack(">I", raw))[0]
    return raw, as_int, as_float


def main():
    logic = assemble(CAVE_ASM)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED)
    print(f"[play] assembled {len(logic)} logic words, payload {len(payload)}",
          flush=True)

    kill_stale()
    h = Harness()
    iw.install_meta_flush(h)
    print("[play] launching ...", flush=True)
    h.launch(); h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[play] meta-flush alive; seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    # confirm the jumpsquat offset/encoding on both players
    print("\n[play] jumpsquat (Player Data 0x148) per port:", flush=True)
    for port in (1, 2):
        raw, ai, af = read_jumpsquat(h, port)
        if raw is None:
            print(f"  P{port}: <no player>", flush=True)
            continue
        cid = h.char_id(port) & 0xFF
        print(f"  P{port}: char=0x{cid:02X}  raw=0x{raw:08X}  as_int={ai}  "
              f"as_float={af:.3f}", flush=True)
    print("  (expect Marth=4, Fox=3 -- as small ints OR floats 4.0/3.0)",
          flush=True)

    h.write_bytes(UPTHRESH_ADDR, bytes([0x40]))
    h.write_bytes(FIRE_ADDR, b"\x00")
    h.write_bytes(WD_PEND_BASE, b"\x00" * 8)   # zero WD_PEND[0..3] + WD_DIR[0..3]
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    print(f"\n[play] hook = 0x{h.read_word(HOOK_ADDR):08X}", flush=True)
    h.wait_frames(15)
    fires = h.read_bytes(FIRE_ADDR, 1)[0]
    print(f"[play] hook fires: {fires} (>0 = cave live)", flush=True)
    if fires == 0:
        print("[play] cave not firing -- abort", flush=True)
        return 1

    print("\n" + "=" * 64, flush=True)
    print("  WAVEDASH MACRO INSTALLED -- PLAY NOW (Dolphin stays running)", flush=True)
    print("=" * 64, flush=True)
    print("  * Click the Dolphin window to focus it.", flush=True)
    print("  * Hold UP (W) to wavedash. Add LEFT/RIGHT (A/D) for direction;", flush=True)
    print("    UP alone = straight-down wavedash. HOLD up = repeats.", flush=True)
    print("  * Release up = normal play (macro does nothing).", flush=True)
    print("  * Works on whatever character you play (jumpsquat read per-char).", flush=True)
    print("  * Do NOT load a savestate (F2) -- it wipes the macro; re-run to", flush=True)
    print("    reinstall. When done, tell me how it felt.", flush=True)
    print("=" * 64, flush=True)
    # Leave Dolphin running; do NOT close.
    try:
        import dolphin_memory_engine as dme
        dme.un_hook()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
