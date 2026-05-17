"""
auto_lcancel/lcancel_rig.py

Test rig that drives Fox through a jump+nair cycle via a PadRead hook AND
toggles the L bit during the aerial portion. Two trials are run:

  Trial 1 (L disabled): expected lag_div ~= 1.0 (baseline ~0.96).
  Trial 2 (L enabled):  expected lag_div ~= 2.0 if the L-cancel mechanic
                        works through the PadRead hook path.

Why this rig
------------
The discovery run (`discover_lcancel.py`) proved that dme writes to
0x804C1FAC (Dolphin's processed controller-data region) lose the race with
Dolphin's input pipeline -- the L bit gets clobbered before the engine reads
it. The fix is the same path the JC-shine macro uses: hook HSD_PadRead at
0x803775B8 and modify the per-port pad struct (r25) AFTER Dolphin has filled
it but BEFORE the engine reads it. This rig installs that hook at runtime
through the meta-flush primitive (no boot-time gecko, no reboot per change).

Encoding
--------
Logic is written in PPC asm and assembled with keystone, matching the
verify_v2_with_keystone.py pattern -- no hand-encoded branch offsets.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/lcancel_rig.py
"""
import os
import struct
import subprocess
import sys
import time

import keystone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import (
    Harness, CONTROLLER_DIGITAL, CONTROLLER_STRIDE, DEFAULT_CAVE,
    POWERON_COUNT, finalize_payload,
)
import instr_writer as iw

OFF_ACTION_STATE = 0x0010
OFF_LR_TIMER = 0x0680
OFF_LANDING_LAG_DIVISOR = 0x2354
OFF_ACT_OUT_OF_LANDING = 0x2358

S_WAIT = 0x000E
LANDING_AERIAL_STATES = set(range(0x46, 0x4B))
AERIAL_STATES = set(range(0x41, 0x46))

TARGET_PORT = 2

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000              # lhz r0, 0(r25)

# meta-flush body lives in the first ~120 bytes of DEFAULT_CAVE. Park our
# hook well past it.
RIG_CAVE = DEFAULT_CAVE + 0x200     # 0x803FA5E8

# Scratch byte the hook polls to decide whether to inject L. 0 = skip,
# non-zero = run the cycle-7 L logic. Lives well past the meta-flush
# control plane (0x803FA440..0x803FA44C).
RIG_FLAG_ADDR = 0x803FA460
# Hook-fire counter -- incremented every time our hook runs, BEFORE any
# port check. If this never advances, the hook isn't firing at all.
HOOK_FIRE_ADDR = 0x803FA464
# Per-aerial L-cycle counter, matching auto_lcancel.COUNTER_ADDR.
# Cycles 0..6 while in an aerial; 0 means "press L this frame".
L_CYCLE_COUNTER_ADDR = 0x803FA470
# When set non-zero, the rig also presses X during KneeBend (0x18) so Fox
# does a full hop instead of a short hop -- exposes the aerial->FallAerial
# transition for airdodge-edge testing.
FULL_HOP_FLAG_ADDR = 0x803FA471

SRC = f"""
    # Unconditional hook-fire counter: increment a byte at HOOK_FIRE_ADDR
    # every time we run. Lets Python verify the hook is actually executing.
    lis   9, 0x{(HOOK_FIRE_ADDR >> 16):04X}
    ori   9, 9, 0x{(HOOK_FIRE_ADDR & 0xFFFF):04X}
    lbz   10, 0(9)
    addi  10, 10, 1
    stb   10, 0(9)

    cmpwi 24, 1                  # is r24 (port) == 1 (P2)?
    bne   end

    # P2 GObj pointer at 0x80453FC0 (= 0x80453130 + 0xE90).
    lis   12, 0x8045
    ori   12, 12, 0x3FC0
    lwz   12, 0(12)
    cmpwi 12, 0
    beq   end
    srwi  9, 12, 24              # sanity: high byte should be 0x80
    cmplwi 9, 0x80
    bne   end

    # Player Data = *(GObj + 0x2C).
    lwz   12, 0x2C(12)
    cmpwi 12, 0
    beq   end
    srwi  9, 12, 24
    cmplwi 9, 0x80
    bne   end

    lwz   11, 0x10(12)           # r11 = action state (word)
    lhz   0, 0(25)               # r0 = current pad buttons (16-bit)

    # state == Wait (0x0E) -> press X (start a jump)
    cmpwi 11, 0x000E
    bne   not_wait
    ori   0, 0, 0x0400           # X bit (16-bit pad buttons layout)
    b     reset_L_counter
not_wait:

    # state == KneeBend (0x18) -> press X iff FULL_HOP_FLAG set, so X stays
    # held through jumpsquat and Fox does a full hop instead of short hop.
    cmpwi 11, 0x0018
    bne   not_kneebend
    lis   9, 0x{(FULL_HOP_FLAG_ADDR >> 16):04X}
    ori   9, 9, 0x{(FULL_HOP_FLAG_ADDR & 0xFFFF):04X}
    lbz   10, 0(9)
    cmpwi 10, 0
    beq   reset_L_counter        # not full-hop mode -> short hop
    ori   0, 0, 0x0400
    b     reset_L_counter
not_kneebend:

    # state in [0x19..0x22] (jumps + falls) -> press A (start aerial)
    cmpwi 11, 0x0019
    blt   not_jump
    cmpwi 11, 0x0022
    bgt   not_jump
    ori   0, 0, 0x0100           # A bit
    b     reset_L_counter
not_jump:

    # state in [0x41..0x45] (aerials) -> cycle-7 L (if RIG_FLAG set)
    cmpwi 11, 0x0041
    blt   reset_L_counter
    cmpwi 11, 0x0045
    bgt   reset_L_counter

    # Gated by RIG_FLAG byte (so trial 1 with L disabled can use the same hook).
    lis   9, 0x{(RIG_FLAG_ADDR >> 16):04X}
    ori   9, 9, 0x{(RIG_FLAG_ADDR & 0xFFFF):04X}
    lbz   10, 0(9)
    cmpwi 10, 0
    beq   apply                  # L disabled -> just write buttons untouched

    # Cycle-7: counter byte at L_CYCLE_COUNTER_ADDR. Press on counter==0,
    # increment after. counter wraps 0..6.
    lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
    ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
    lbz   10, 0(9)
    cmpwi 10, 0
    bne   inc_counter
    ori   0, 0, 0x0040           # press L (rising edge from prev release)
inc_counter:
    addi  10, 10, 1
    cmpwi 10, 7
    blt   store_counter
    li    10, 0
store_counter:
    stb   10, 0(9)
    b     apply

reset_L_counter:
    # Non-aerial frame: reset cycle counter so the next aerial entry starts
    # on a press frame (counter == 0).
    li    10, 0
    lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
    ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
    stb   10, 0(9)

apply:
    sth   0, 0(25)               # write modified buttons back

end:
"""


def assemble(src):
    ks = keystone.Ks(
        keystone.KS_ARCH_PPC,
        keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN,
    )
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone asm returned None")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale_dolphins():
    """Kill any process named exactly 'Dolphin' (our hardlinked instances) and
    wait until they're actually gone. Slippi launcher's running process is
    'Slippi Dolphin' (with a space) so it's not matched -- safe."""
    r = subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    if r.returncode == 0:
        print("[rig] SIGKILLed stale Dolphin process(es); "
              "waiting for them to disappear ...", flush=True)
        for _ in range(40):
            pgrep = subprocess.run(["pgrep", "-x", "Dolphin"],
                                   capture_output=True, text=True)
            if not pgrep.stdout.strip():
                print("[rig] stale Dolphin(s) gone", flush=True)
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin process refused to die within 10s")


OFF_DIGITAL_BUTTONS = 0x065C    # word: processed digital buttons (player data)


def read_state(h, pd):
    a = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
    lr = h.read_bytes(pd + OFF_LR_TIMER, 1)[0]
    ld = struct.unpack(">f", h.read_bytes(pd + OFF_LANDING_LAG_DIVISOR, 4))[0]
    ao = h.read_bytes(pd + OFF_ACT_OUT_OF_LANDING, 1)[0]
    btn = h.read_word(pd + OFF_DIGITAL_BUTTONS) & 0xFFFFFFFF
    fires = h.read_bytes(HOOK_FIRE_ADDR, 1)[0]
    return a, lr, ld, ao, btn, fires


def watch_for_landing(h, pd, max_frames=240, print_each=True, print_stride=8):
    """Run until we observe a landing-aerial state. Return the row at that
    moment. Prints either on state change, every `print_stride` frames, or
    when in a notable state."""
    landing_data = None
    last_state = None
    rows = []
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")     # reset fire counter
    for i in range(max_frames):
        a, lr, ld, ao, btn, fires = read_state(h, pd)
        rows.append((i, a, lr, ld, ao, btn, fires))
        changed = (last_state is None or last_state != a)
        notable = (a in AERIAL_STATES or a in LANDING_AERIAL_STATES
                   or a == 0x18 or 0x19 <= a <= 0x22 or a == 0x2A)
        if print_each and (changed or notable or i % print_stride == 0):
            tag = ""
            if a in LANDING_AERIAL_STATES:
                tag = "  <-- LANDING-AERIAL"
            elif a in AERIAL_STATES:
                tag = "  [aerial]"
            elif a == 0x18:
                tag = "  [kneebend]"
            elif 0x19 <= a <= 0x22:
                tag = "  [jump/fall]"
            elif a == 0x2A:
                tag = "  [plain landing]"
            print(f"    i={i:3d}  state=0x{a:04X}  lr={lr:3d}  "
                  f"lag_div={ld:5.3f}  ao={ao}  btn=0x{btn:08X}  "
                  f"fires={fires}{tag}", flush=True)
        last_state = a
        if a in LANDING_AERIAL_STATES and landing_data is None:
            landing_data = {"landing_index": i, "landing_state": a,
                            "landing_lr": lr, "landing_lag_div": ld,
                            "landing_act_out": ao}
        if landing_data is not None and a == S_WAIT:
            landing_data["cycle_complete_index"] = i
            return landing_data, rows
        try:
            h.wait_frames(1, timeout_s=2.0)
        except TimeoutError:
            time.sleep(0.02)
    return landing_data, rows


def install_rig(h):
    logic = assemble(SRC)
    payload = finalize_payload(logic, HOOK_ADDR, RIG_CAVE, DISPLACED)
    print(f"[rig] assembled {len(logic)} logic words; "
          f"full payload {len(payload)} words", flush=True)
    iw.write_instrs(h, RIG_CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, RIG_CAVE)
    # Sanity dump: hook word + first 4 cave words + the META-FLUSH hook word
    # (the meta-flush gecko-codehandler doesn't necessarily live at our
    # caller's chosen cave addr -- see the harness log "gecko cave @ ..." --
    # so let's verify both branches landed on something live).
    word_at_hook = h.read_word(HOOK_ADDR)
    cave_words = [h.read_word(RIG_CAVE + 4 * i) for i in range(6)]
    word_at_meta = h.read_word(0x803775C0)
    print(f"[rig] hook 0x{HOOK_ADDR:08X} = 0x{word_at_hook:08X} "
          f"(expect 0x4808xxxx -> 0x{RIG_CAVE:08X})", flush=True)
    print(f"[rig] cave 0x{RIG_CAVE:08X} first 6 words: "
          + " ".join(f"0x{w:08X}" for w in cave_words), flush=True)
    print(f"[rig] meta-flush hook 0x803775C0 = 0x{word_at_meta:08X}",
          flush=True)
    # Reset hook-fire counter -- Python will read it to confirm the hook
    # actually executes once we get in-game.
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")


def main():
    kill_stale_dolphins()

    h = Harness()
    iw.install_meta_flush(h)
    print("[rig] launching Dolphin ...", flush=True)
    h.launch()
    h.hook_dme()

    print("[rig] waiting for CPU + meta-flush gecko ...", flush=True)
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[rig] meta-flush alive", flush=True)

    print("[rig] seeding scenario (loading slot 2) ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)

    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[rig] P2 player data invalid -- abort", flush=True)
        return 1
    print(f"[rig] P{TARGET_PORT} pd=0x{pd:08X}", flush=True)

    # Init scratch bytes BEFORE installing the hook so the hook never reads
    # garbage from the post-savestate MEM1.
    h.write_bytes(RIG_FLAG_ADDR, b"\x00")
    h.write_bytes(HOOK_FIRE_ADDR, b"\x00")
    h.write_bytes(L_CYCLE_COUNTER_ADDR, b"\x00")
    h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x00")

    # CRITICAL: install AFTER seed_snapshot. seed_snapshot loads slot 2,
    # which restores MEM1 and would wipe any runtime patches installed
    # before it. Install now so the hook lives in the post-seed MEM1.
    install_rig(h)

    # Give Fox a few frames so the hook has a chance to start cycling him
    # through jump/nair before we start measuring.
    h.wait_frames(15)

    def run_trial(label, l_enabled, full_hop, max_frames=320):
        print("\n" + "=" * 70, flush=True)
        print(f"{label}", flush=True)
        print("=" * 70, flush=True)
        h.write_bytes(RIG_FLAG_ADDR, b"\x01" if l_enabled else b"\x00")
        h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x01" if full_hop else b"\x00")
        h.write_bytes(L_CYCLE_COUNTER_ADDR, b"\x00")
        # Wait for Fox to be in Wait so the X press registers cleanly.
        for _ in range(120):
            a = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
            if a == S_WAIT:
                break
            h.wait_frames(1)
        r, rows = watch_for_landing(h, pd, max_frames=max_frames)
        # Did Fox enter EscapeAir (0x00EC) at any point? That's airdodge.
        states_seen = sorted({row[1] for row in rows})
        airdodged = 0x00EC in states_seen
        print(f"  >> result: {r}", flush=True)
        print(f"  states seen: {[f'0x{s:04X}' for s in states_seen]}", flush=True)
        if airdodged:
            print(f"  *** AIRDODGE detected (state 0x00EC observed) ***",
                  flush=True)
        return r, rows, airdodged

    r1, _, ad1 = run_trial("TRIAL 1: short hop / L off      (expect ~15f)",
                            l_enabled=False, full_hop=False)
    r2, _, ad2 = run_trial("TRIAL 2: short hop / L cycle-7  (expect ~7f)",
                            l_enabled=True,  full_hop=False)
    r3, _, ad3 = run_trial("TRIAL 3: FULL hop  / L off      (expect ~15f, "
                            "no airdodge)",
                            l_enabled=False, full_hop=True)
    r4, _, ad4 = run_trial("TRIAL 4: FULL hop  / L cycle-7  (expect ~7f, "
                            "no airdodge)",
                            l_enabled=True,  full_hop=True)

    # ---- SUMMARY ----
    # Real L-cancel observable is LANDING-STATE DURATION. Duration =
    # cycle_complete_index - landing_index, i.e. number of frames Fox spent
    # in a LandingAir{N,F,B,Hi,Lw} state before returning to Wait.
    def duration(r):
        if not r or r.get("landing_index") is None or r.get("cycle_complete_index") is None:
            return None
        return r["cycle_complete_index"] - r["landing_index"]

    d1, d2, d3, d4 = (duration(r) for r in (r1, r2, r3, r4))

    print("\n" + "=" * 70, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"  TRIAL 1 short hop / L off     duration={d1}f  airdodge={ad1}", flush=True)
    print(f"  TRIAL 2 short hop / L cycle-7 duration={d2}f  airdodge={ad2}", flush=True)
    print(f"  TRIAL 3 FULL  hop / L off     duration={d3}f  airdodge={ad3}", flush=True)
    print(f"  TRIAL 4 FULL  hop / L cycle-7 duration={d4}f  airdodge={ad4}", flush=True)

    failures = []
    if d1 and d2:
        if d2 / d1 > 0.6:
            failures.append(f"short hop cycle-7 didn't L-cancel "
                            f"({d1}f -> {d2}f, ratio {d2/d1:.3f})")
    else:
        failures.append(f"short-hop trials missing landings (d1={d1}, d2={d2})")
    if d3 and d4:
        if d4 / d3 > 0.6:
            failures.append(f"full hop cycle-7 didn't L-cancel "
                            f"({d3}f -> {d4}f, ratio {d4/d3:.3f})")
    else:
        failures.append(f"full-hop trials missing landings (d3={d3}, d4={d4})")
    if ad2 or ad4:
        failures.append(f"unintended airdodge observed with cycle-7 "
                        f"(short hop ad2={ad2}, full hop ad4={ad4})")

    if failures:
        print(flush=True)
        for f in failures:
            print(f"  [FAIL] {f}", flush=True)
        return 1
    print(f"\n  [PASS] cycle-7 L-cancels both short hops ({d1}f -> {d2}f) and "
          f"full hops ({d3}f -> {d4}f). No airdodge observed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
