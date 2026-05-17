"""
auto_lcancel/auto_lcancel.py

The shipped auto-L-cancel macro.

Hooks `HSD_PadRead` at `0x803775B8` (vanilla `lhz r0, 0(r25)` = `0xA0190000`)
on the target port's pad pass. While the character is in an aerial-attack
state (0x41..0x45 = NAIR/FAIR/BAIR/UAIR/DAIR) the hook presses L exactly
once every 7 frames -- 1 frame of press, 6 frames released, repeat. That
gives a single rising-edge L press per cycle and keeps the engine's
"Frames Since L/R Digital Pressed" timer (`Char Data + 0x680`) cycling
through values 0..6 — strictly less than the engine's 7-frame L-cancel
window (PlCo 0xA0C4 = 7; the engine compares with `bgt` at code address
0x8008E4CC, so `timer ≤ 7` succeeds).

Why "1 press / 6 released" instead of "toggle every frame"
----------------------------------------------------------
- The L-cancel check reads `0x680` once at landing detection. Both patterns
  keep that read inside the 7-frame window, so both achieve L-cancel.
- But L is ALSO an airdodge input: a rising-edge L press in an airborne
  non-attack state (e.g. `FallAerial` 0x20, reached when an aerial finishes
  in air) triggers an airdodge. Toggling L every frame gives ~50% of
  aerial-end frames a rising-edge L; pressing once per 7 frames drops that
  to ~14% AND only ever has L "freshly pressed" on the cycle-start frame.
  Combined with resetting the cycle counter on every non-aerial frame, the
  macro only injects L while the character is unambiguously in an aerial
  state.

Why the runtime path
--------------------
- dme writes to `0x804C1FAC` (Dolphin's processed controller region) lose
  the race with Dolphin's input pipeline -- proven by `discover_lcancel.py`.
- Direct writes to player-data fields would desync online; only pad-buffer
  modifications are network-safe. The PadRead hook (JC-shine pattern,
  `candidate_d2`) modifies r25's struct after Dolphin polls but before the
  engine reads -- the only writes that stick.

Install path
------------
Runtime, via the meta-flush primitive. `install(harness)` must be called
AFTER `harness.seed_snapshot()` (which loads slot 2 and wipes MEM1 patches
installed before it). For repeat reloads, re-`install()` each time.

Public API:
    install(harness, port=2) -> dict   # returns {hook, cave, payload_len}

Module constants (so other modules can build a verify-rig superset of this
hook):
    HOOK_ADDR, DISPLACED, NAME, CAVE_OFFSET, COUNTER_ADDR
    AERIAL_STATE_MIN, AERIAL_STATE_MAX, CYCLE_LEN
"""
import os
import struct
import sys

import keystone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import DEFAULT_CAVE, POWERON_COUNT, finalize_payload
import instr_writer as iw

NAME = "auto-l-cancel"
HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000          # lhz r0, 0(r25)

# Park the macro body well past the meta-flush gecko (which lives at
# DEFAULT_CAVE..DEFAULT_CAVE+~120) but inside the same free-memory region.
CAVE_OFFSET = 0x300
DEFAULT_INSTALL_CAVE = DEFAULT_CAVE + CAVE_OFFSET     # 0x803FA6E8

AERIAL_STATE_MIN = 0x0041
AERIAL_STATE_MAX = 0x0045

# Cycle length: press L on frame 0 of each cycle, release on frames 1..6.
# Counter wraps 0..(CYCLE_LEN - 1). With CYCLE_LEN = 7 and Char Data + 0x680
# (timer) incrementing each frame except when L rising-edges, the engine sees
# timer values 0, 1, 2, 3, 4, 5, 6, 0, 1, ... -- always ≤ 6, strictly under
# the 7-frame window.
CYCLE_LEN = 7

# Counter byte. Lives in the debug-menu free-memory region, well past the
# meta-flush control plane (0x803FA440..0x803FA44C). One byte per install.
COUNTER_ADDR = 0x803FA470

# Per-port P1 GObj pointer is at 0x80453130; P2 is +0xE90 = 0x80453FC0; etc.
# `port` argument to `install()` selects which GObj address is hardcoded.
_P1_GOBJ_PTR = 0x80453130
_PORT_STRIDE = 0xE90


def _build_src(port: int) -> str:
    """Build the keystone-asm source for the macro hook for `port` (1-indexed)."""
    gobj_ptr_addr = _P1_GOBJ_PTR + (port - 1) * _PORT_STRIDE
    # r24 at the hook holds the 0-indexed port; convert.
    r24_match = port - 1
    return f"""
        cmpwi 24, {r24_match}        # only act on the target port's pass
        bne   end

        lis   12, 0x{(gobj_ptr_addr >> 16):04X}
        ori   12, 12, 0x{(gobj_ptr_addr & 0xFFFF):04X}
        lwz   12, 0(12)              # r12 = GObj ptr
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24              # sanity: top byte should be 0x80 (MEM1)
        cmplwi 9, 0x80
        bne   end

        lwz   12, 0x2C(12)           # r12 = Player Data ptr (GObj + 0x2C)
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24
        cmplwi 9, 0x80
        bne   end

        lwz   11, 0x10(12)           # r11 = action state (word)
        cmpwi 11, 0x{AERIAL_STATE_MIN:04X}
        blt   not_aerial
        cmpwi 11, 0x{AERIAL_STATE_MAX:04X}
        bgt   not_aerial

        # === aerial path: cycle counter at COUNTER_ADDR. =====================
        # counter 0       -> press L (rising edge from previous frame's release)
        # counter 1..6    -> don't modify pad (L released by Dolphin's poll if
        #                    user isn't pressing it)
        # increment counter mod CYCLE_LEN ({CYCLE_LEN}) after each frame.
        lis   9, 0x{(COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(COUNTER_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)               # r10 = counter

        cmpwi 10, 0
        bne   inc_counter            # not on press-frame

        lhz   0, 0(25)               # r0 = current 16-bit pad buttons
        ori   0, 0, 0x0040           # L bit
        sth   0, 0(25)               # press L (displaced original re-reads it)

    inc_counter:
        addi  10, 10, 1
        cmpwi 10, {CYCLE_LEN}
        blt   store_counter
        li    10, 0
    store_counter:
        stb   10, 0(9)
        b     end

    not_aerial:
        # Reset counter so the next aerial entry starts on frame 0 (= press).
        li    10, 0
        lis   9, 0x{(COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(COUNTER_ADDR & 0xFFFF):04X}
        stb   10, 0(9)

    end:
    """


def assemble(port: int = 2) -> list:
    """Assemble the macro for `port`. Returns a list of 32-bit instruction
    words (big-endian natural ints) -- the body, NOT yet finalized with the
    displaced original + branch back."""
    ks = keystone.Ks(
        keystone.KS_ARCH_PPC,
        keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN,
    )
    raw, _ = ks.asm(_build_src(port))
    if raw is None:
        raise RuntimeError("keystone returned no output -- check assembly source")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0]
            for i in range(0, len(raw), 4)]


def install(harness, port: int = 2, cave: int = DEFAULT_INSTALL_CAVE) -> dict:
    """Assemble and install the macro at runtime.

    PREREQUISITES:
      - `instr_writer.install_meta_flush(harness)` was called pre-launch.
      - Harness has launched, hooked dme, and `wait_for_meta_flush_alive`
        has returned.
      - `harness.seed_snapshot()` has completed (it wipes MEM1, so any patch
        installed earlier is gone).

    Returns: {"hook": HOOK_ADDR, "cave": cave, "payload_len": int,
              "logic_len": int}
    """
    logic = assemble(port)
    payload = finalize_payload(logic, HOOK_ADDR, cave, DISPLACED)
    iw.write_instrs(harness, cave, payload)
    iw.patch_branch(harness, HOOK_ADDR, cave)
    return {"hook": HOOK_ADDR, "cave": cave,
            "payload_len": len(payload), "logic_len": len(logic)}
