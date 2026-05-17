"""
Phase-2 dme debugger: software breakpoints.

Built on `instr_writer.write_instrs` (Phase 1). A breakpoint at `target_addr`
overwrites the instruction there with a branch to a per-slot handler in
scratch memory. The handler:

  1. allocates a 16-byte mini-frame on the game's stack
  2. snapshots r0..r31, LR, CTR, CR to a fixed scratch RAM region (the
     "save area" -- one per BP slot, so dme can read it without knowing
     r1 at hit time)
  3. signals BP_HIT_FLAG (slot-specific) so dme observes the hit
  4. spins on BP_CONT_FLAG (slot-specific) until dme releases
  5. clears both flags, restores all registers (potentially with dme-edited
     values from the snapshot), executes the displaced original
     instruction, and branches back to target_addr + 4

While the handler spins, the emulated PPC core is parked in our loop, so
the game is effectively frozen. Dolphin's other threads (audio, graphics,
window) continue to run, so the application stays responsive.

Limits:
  - 16 simultaneous BPs (per-slot scratch carved from debug-menu tables).
  - Slippi netplay-safe? NO. The spin halts the entire PPC core; Slippi's
    netcode would desync. This is a dev/offline tool only.
  - Doesn't work for instructions in branch delay slots -- PowerPC has
    none, so this isn't an issue.
  - Doesn't work for instructions whose semantics are tied to their PC
    (relative branches/calls/`mflr`-after-`bl`). Patching such an
    instruction off-pc and running it in the cave will produce wrong
    branch targets / wrong LR. Future work: detect & rewrite these.
"""
import time

from instr_writer import write_instrs, patch_branch, FLUSH_REQUEST


# ---------------------------------------------------------------------------
# Memory layout for BP slots. All carved from the "More debug-menu tables"
# region (0x803FC420..0x803FDC1C, 0x17FC bytes) per Free_Memory.csv, plus
# slot-flag/snapshot areas in 0x803FB000..0x803FBFFF (also unused).
#
# 8 slots * 512 bytes/slot = 4 KB of handler code. We need 512 bytes/slot
# rather than 256 because each handler now embeds an inline flush servicer
# (so step() can install successor BPs while another BP is parked in spin).
# ---------------------------------------------------------------------------
MAX_SLOTS = 8

SNAPSHOT_BASE = 0x803FB000   # 8 slots * 256 bytes = 2 KB
FLAGS_BASE    = 0x803FB400   # 8 slots * 8 bytes = 64 bytes
HANDLER_BASE  = 0x803FC420   # 8 slots * 512 bytes = 4 KB
HANDLER_SLOT_SIZE = 512

# Snapshot layout (144 bytes used per slot, 256 reserved):
#    0..127   r0..r31      (32 GPRs * 4 bytes)
#    128      LR
#    132      CTR
#    136      CR
#    140      reserved
SNAP_LR  = 128
SNAP_CTR = 132
SNAP_CR  = 136


def snapshot_addr(slot):  return SNAPSHOT_BASE + slot * 256
def hit_flag_addr(slot):  return FLAGS_BASE + slot * 8 + 0
def cont_flag_addr(slot): return FLAGS_BASE + slot * 8 + 4
def handler_addr(slot):   return HANDLER_BASE + slot * HANDLER_SLOT_SIZE


# ---------------------------------------------------------------------------
# Per-BP handler builder. 77 instructions, all encoded inline (no assembler
# dependency). See the file docstring for the high-level flow.
#
# Register conventions inside the handler:
#   r3  -- pointer base for hit/cont/save loads/stores
#   r4  -- working scratch
#   r5  -- pointer to FLUSH_REQUEST (set once before spin, used during inline flush)
#   r6  -- 0xDEADBEEF magic word for flush compare (set once before spin)
#   r7  -- FLUSH_START (loaded each time a flush is serviced)
#   r8  -- FLUSH_END
#   r9  -- flush cursor (dcbf/icbi loop counter)
#   r0  -- saved to mini-frame at +8 immediately, then free for restore last.
#
# All of r4..r31 are saved to the per-slot save area via stmw BEFORE we
# clobber any of them, so subsequent use is safe.
# ---------------------------------------------------------------------------
def build_bp_handler(slot, target_addr, displaced_word):
    save = snapshot_addr(slot)
    hit  = hit_flag_addr(slot)
    cont = cont_flag_addr(slot)
    handler_pc = handler_addr(slot)

    save_hi, save_lo = (save >> 16) & 0xFFFF, save & 0xFFFF
    hit_hi,  hit_lo  = (hit  >> 16) & 0xFFFF, hit  & 0xFFFF
    cont_hi, cont_lo = (cont >> 16) & 0xFFFF, cont & 0xFFFF
    flush_hi, flush_lo = (FLUSH_REQUEST >> 16) & 0xFFFF, FLUSH_REQUEST & 0xFFFF

    # 77 instructions total: 75 fixed + displaced + branch-back. The
    # branch-back returns to target_addr + 4.
    n_instrs = 77
    branch_back_pc = handler_pc + (n_instrs - 1) * 4
    branch_back_offset = ((target_addr + 4) - branch_back_pc) & 0x03FFFFFC
    branch_back = 0x48000000 | branch_back_offset

    # Each branch offset below is hand-derived from indices; mistakes here
    # would brick the handler. capstone disassembly in the smoke test
    # catches them.
    return [
        # ===== idx 0..18: save-state ==================================
        0x9421FFF0,                         #  0: stwu r1, -16(r1)
        0x90010008,                         #  1: stw  r0, 8(r1)
        0x9061000C,                         #  2: stw  r3, 12(r1)
        0x3C600000 | save_hi,               #  3: lis  r3, save_hi
        0x60630000 | save_lo,               #  4: ori  r3, r3, save_lo
        0xBC830010,                         #  5: stmw r4, 16(r3)
        0x80810008,                         #  6: lwz  r4, 8(r1)
        0x90830000,                         #  7: stw  r4, 0(r3)
        0x38810010,                         #  8: addi r4, r1, 16
        0x90830004,                         #  9: stw  r4, 4(r3)
        0x90430008,                         # 10: stw  r2, 8(r3)
        0x8081000C,                         # 11: lwz  r4, 12(r1)
        0x9083000C,                         # 12: stw  r4, 12(r3)
        0x7C8802A6,                         # 13: mflr  r4
        0x90830080,                         # 14: stw   r4, 128(r3)
        0x7C8902A6,                         # 15: mfctr r4
        0x90830084,                         # 16: stw   r4, 132(r3)
        0x7C800026,                         # 17: mfcr  r4
        0x90830088,                         # 18: stw   r4, 136(r3)
        # ===== idx 19..22: signal hit ================================
        0x3C600000 | hit_hi,                # 19: lis  r3, hit_hi
        0x60630000 | hit_lo,                # 20: ori  r3, r3, hit_lo
        0x38800001,                         # 21: li   r4, 1
        0x90830000,                         # 22: stw  r4, 0(r3)
        # ===== idx 23..28: spin pre-setup ============================
        0x3C600000 | cont_hi,               # 23: lis  r3, cont_hi
        0x60630000 | cont_lo,               # 24: ori  r3, r3, cont_lo
        0x3C000000 | (5 << 21) | flush_hi,  # 25: lis  r5, flush_hi
        0x60000000 | (5 << 21) | (5 << 16) | flush_lo,  # 26: ori  r5, r5, flush_lo
        0x3C000000 | (6 << 21) | 0xDEAD,    # 27: lis  r6, 0xDEAD
        0x60000000 | (6 << 21) | (6 << 16) | 0xBEEF,    # 28: ori  r6, r6, 0xBEEF
        # ===== idx 29..34: spin loop (cont + flush poll) ============
        0x80830000,                         # 29: [SPIN_TOP] lwz r4, 0(r3)
        0x2C040000,                         # 30: cmpwi r4, 0
        0x40820068,                         # 31: bne EXIT_SPIN  (target idx 57, +0x68)
        0x80850000,                         # 32: lwz  r4, 0(r5)        ; check flush req
        0x7C043000,                         # 33: cmpw r4, r6
        0x4082FFEC,                         # 34: bne SPIN_TOP   (back -20 bytes to idx 29)
        # ===== idx 35..55: inline flush ============================
        0x80E50004,                         # 35: lwz r7, 4(r5)         ; FLUSH_START
        0x81050008,                         # 36: lwz r8, 8(r5)         ; FLUSH_END
        0x54E70034,                         # 37: rlwinm r7, r7, 0, 0, 26
        0x3908001F,                         # 38: addi r8, r8, 31
        0x55080034,                         # 39: rlwinm r8, r8, 0, 0, 26
        0x7CE93B78,                         # 40: mr r9, r7
        0x7C094040,                         # 41: [DCBF_LOOP] cmplw r9, r8
        0x40800010,                         # 42: bge DCBF_DONE  (idx 46)
        0x7C0048AC,                         # 43: dcbf 0, r9
        0x39290020,                         # 44: addi r9, r9, 32
        0x4BFFFFF0,                         # 45: b DCBF_LOOP    (back -16 to idx 41)
        0x7C0004AC,                         # 46: [DCBF_DONE] sync
        0x7CE93B78,                         # 47: mr r9, r7
        0x7C094040,                         # 48: [ICBI_LOOP] cmplw r9, r8
        0x40800010,                         # 49: bge ICBI_DONE  (idx 53)
        0x7C004FAC,                         # 50: icbi 0, r9
        0x39290020,                         # 51: addi r9, r9, 32
        0x4BFFFFF0,                         # 52: b ICBI_LOOP    (back -16 to idx 48)
        0x4C00012C,                         # 53: [ICBI_DONE] isync
        0x38800000,                         # 54: li r4, 0
        0x90850000,                         # 55: stw r4, 0(r5)         ; clear magic
        0x4BFFFF94,                         # 56: b SPIN_TOP     (back -108 to idx 29)
        # ===== idx 57..61: exit spin + clear flags ==================
        0x38800000,                         # 57: [EXIT_SPIN] li r4, 0
        0x90830000,                         # 58: stw r4, 0(r3)         ; *cont = 0
        0x3C600000 | hit_hi,                # 59: lis r3, hit_hi
        0x60630000 | hit_lo,                # 60: ori r3, r3, hit_lo
        0x90830000,                         # 61: stw r4, 0(r3)         ; *hit  = 0
        # ===== idx 62..74: restore phase ===========================
        0x3C600000 | save_hi,               # 62: lis r3, save_hi
        0x60630000 | save_lo,               # 63: ori r3, r3, save_lo
        0x80830080,                         # 64: lwz r4, 128(r3)
        0x7C8803A6,                         # 65: mtlr r4
        0x80830084,                         # 66: lwz r4, 132(r3)
        0x7C8903A6,                         # 67: mtctr r4
        0x80830088,                         # 68: lwz r4, 136(r3)
        0x7C8FF120,                         # 69: mtcr r4
        0x80430008,                         # 70: lwz r2, 8(r3)
        0xB8830010,                         # 71: lmw r4, 16(r3)
        0x80030000,                         # 72: lwz r0, 0(r3)
        0x8063000C,                         # 73: lwz r3, 12(r3)
        0x38210010,                         # 74: addi r1, r1, 16
        # ===== idx 75..76: displaced + branch back =================
        displaced_word,                     # 75
        branch_back,                        # 76
    ]


# ---------------------------------------------------------------------------
# Python-side BP API
# ---------------------------------------------------------------------------
_used_slots = set()


class Breakpoint:
    def __init__(self, harness, target_addr, slot, original_word):
        self.h = harness
        self.target = target_addr
        self.slot = slot
        self.handler = handler_addr(slot)
        self.original_word = original_word
        self.armed = True

    def __repr__(self):
        state = "armed" if self.armed else "removed"
        return (f"<Breakpoint slot={self.slot} target=0x{self.target:08X} "
                f"orig=0x{self.original_word:08X} {state}>")


def set_breakpoint(harness, target_addr, slot=None):
    """Install a software BP at `target_addr`. Returns a Breakpoint handle.

    The target's vanilla instruction is captured + saved into the handler's
    displaced-original slot so it executes normally when we continue.
    """
    if slot is None:
        for s in range(MAX_SLOTS):
            if s not in _used_slots:
                slot = s
                break
        else:
            raise RuntimeError(f"all {MAX_SLOTS} BP slots in use")
    elif slot in _used_slots:
        raise RuntimeError(f"BP slot {slot} already in use")

    original = harness.read_word(target_addr)
    handler = build_bp_handler(slot, target_addr, original)

    # Zero hit/cont flags before installing, so a stale value doesn't make
    # us think the BP fired before it actually did.
    harness.write_words(hit_flag_addr(slot), [0])
    harness.write_words(cont_flag_addr(slot), [0])

    # Write the handler into its slot (+ flush), then patch the target hook.
    write_instrs(harness, handler_addr(slot), handler)
    patch_branch(harness, target_addr, handler_addr(slot))

    _used_slots.add(slot)
    return Breakpoint(harness, target_addr, slot, original)


def remove_breakpoint(bp, settle_timeout_s=2.0):
    """Restore the original instruction (+ flush). BP no longer fires.

    Releases the spin first if the BP is currently hit -- otherwise the
    write_instrs below would call flush_range, which would deadlock
    waiting for either the meta-flush gecko OR this BP's inline flush
    servicer to fire. We don't want to rely on the inline servicer for
    teardown; cleanest is to release and wait for the handler to exit
    its spin entirely.
    """
    if not bp.armed:
        return
    # Set cont = 1 so any current OR future entry into this BP's spin
    # exits immediately. (Each spin iteration that sees cont != 0 takes
    # the bne to EXIT_SPIN, clears the flags, and runs displaced.)
    bp.h.write_words(cont_flag_addr(bp.slot), [1])
    if is_hit(bp):
        deadline = time.time() + settle_timeout_s
        while time.time() < deadline:
            if not is_hit(bp):
                break
            time.sleep(0.001)
    # Now safe to flush.
    write_instrs(bp.h, bp.target, [bp.original_word])
    bp.h.write_words(hit_flag_addr(bp.slot), [0])
    bp.h.write_words(cont_flag_addr(bp.slot), [0])
    _used_slots.discard(bp.slot)
    bp.armed = False


def is_hit(bp):
    """True iff the BP has fired and is currently spinning."""
    return bp.h.read_word(hit_flag_addr(bp.slot)) != 0


def wait_for_hit(bp, timeout_s=10.0, poll_interval=0.005):
    """Block until `bp` fires. Raises TimeoutError otherwise."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_hit(bp):
            return
        time.sleep(poll_interval)
    raise TimeoutError(
        f"BP at 0x{bp.target:08X} did not fire within {timeout_s:.1f}s")


def read_snapshot(bp):
    """Return a dict of the CPU state captured at the BP hit.

    Keys: r0..r31, lr, ctr, cr, pc (== target_addr). Only valid while the
    BP is in the hit state (i.e. between wait_for_hit and continue_).
    Values dme reads from the snapshot, so any change you make via
    write_snapshot() before continue_() is restored back into the CPU.
    """
    base = snapshot_addr(bp.slot)
    out = {f"r{i}": bp.h.read_word(base + i * 4) for i in range(32)}
    out["lr"]  = bp.h.read_word(base + SNAP_LR)
    out["ctr"] = bp.h.read_word(base + SNAP_CTR)
    out["cr"]  = bp.h.read_word(base + SNAP_CR)
    out["pc"]  = bp.target
    return out


def write_snapshot(bp, **regs):
    """Edit register values in the snapshot before continuing. Accepts keyword
    args like r3=0xCAFE, lr=0x80000000. The handler reads from this same
    memory on the way out, so the CPU resumes with the modified values.
    """
    base = snapshot_addr(bp.slot)
    for name, val in regs.items():
        val &= 0xFFFFFFFF
        if name.startswith("r") and name[1:].isdigit():
            i = int(name[1:])
            bp.h.write_words(base + i * 4, [val])
        elif name == "lr":
            bp.h.write_words(base + SNAP_LR, [val])
        elif name == "ctr":
            bp.h.write_words(base + SNAP_CTR, [val])
        elif name == "cr":
            bp.h.write_words(base + SNAP_CR, [val])
        else:
            raise ValueError(f"unknown register name: {name!r}")


def continue_(bp):
    """Release `bp`'s spin. Handler restores registers, executes the displaced
    original, and resumes at target_addr+4."""
    bp.h.write_words(cont_flag_addr(bp.slot), [1])


# ---------------------------------------------------------------------------
# Branch decoder + single-step.
#
# For step() to know where the CPU goes next, it has to decode the captured
# original PPC instruction. We handle the four branch families:
#   opcode 18 -- b   / bl   / ba   / bla   (immediate target)
#   opcode 16 -- bc  / bcl  / bca  / bcla  (immediate, conditional)
#   opcode 19 xo 16  -- bclr / bclrl       (LR-relative)
#   opcode 19 xo 528 -- bcctr / bcctrl     (CTR-relative)
# Everything else is treated as sequential (next PC = pc + 4).
#
# `sc` (system call, opcode 17) and `tw/twi` (trap) are NOT modeled --
# step() will treat them as sequential, which is wrong but rare in game
# code. Future work.
# ---------------------------------------------------------------------------
def _bo_is_always(bo):
    """Per PPC ISA: BO[0]=1 means "ignore CR bit", BO[2]=1 means "don't
    decrement CTR". When both are set, the branch is unconditional."""
    return (bo & 0b10100) == 0b10100


def decode_successors(insn, pc, lr, ctr):
    """Return a list of possible next-PC addresses after executing `insn`
    at `pc`. List has 1 entry for unconditional flow, 2 for conditional.

    `lr` and `ctr` come from the captured snapshot at the BP hit -- needed
    only for bclr/bcctr (and only their LK=0 forms; LK=1 still uses LR/CTR
    for the branch but ALSO writes LR before the branch).
    """
    opcode = (insn >> 26) & 0x3F

    if opcode == 18:
        # b / bl / ba / bla
        li = insn & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        aa = (insn >> 1) & 1
        target = li & 0xFFFFFFFF if aa else (pc + li) & 0xFFFFFFFF
        return [target]

    if opcode == 16:
        # bc / bcl / bca / bcla
        bo = (insn >> 21) & 0x1F
        bd = insn & 0xFFFC
        if bd & 0x8000:
            bd -= 0x10000
        aa = (insn >> 1) & 1
        target = bd & 0xFFFFFFFF if aa else (pc + bd) & 0xFFFFFFFF
        fall = (pc + 4) & 0xFFFFFFFF
        if _bo_is_always(bo):
            return [target]
        return [fall] if target == fall else [fall, target]

    if opcode == 19:
        xo = (insn >> 1) & 0x3FF
        if xo == 16:                       # bclr / bclrl
            bo = (insn >> 21) & 0x1F
            target = lr & 0xFFFFFFFC
            fall = (pc + 4) & 0xFFFFFFFF
            if _bo_is_always(bo):
                return [target]
            return [fall] if target == fall else [fall, target]
        if xo == 528:                      # bcctr / bcctrl
            bo = (insn >> 21) & 0x1F
            target = ctr & 0xFFFFFFFC
            fall = (pc + 4) & 0xFFFFFFFF
            if _bo_is_always(bo):
                return [target]
            return [fall] if target == fall else [fall, target]
        # Other opcode-19 instructions (isync, mtcrf, crand, ...) are
        # sequential.

    # Sequential.
    return [(pc + 4) & 0xFFFFFFFF]


def step(bp, timeout_s=10.0):
    """Advance the CPU by one instruction from a BP-hit state.

    Computes successor PC(s) from the captured original instruction, sets a
    BP at each, releases the current spin, waits for one of the successors
    to fire, removes the unused successors, and returns the BP that fired.

    The CURRENT bp is left armed -- a future iteration of the same hook
    location will fire it again, which is usually what you want for
    "step in a loop." Call `remove_breakpoint(bp)` afterwards if you don't.

    LIMITATIONS:
      * Instructions with the LK bit set (bl, bcl, bclrl, bcctrl) write LR
        to the cave address rather than target+4. This usually self-heals
        because the handler's trailing `b target+4` sits at LR's value
        anyway -- if the called function returns via blr, control routes
        through our branch-back -- but a function that *inspects* LR will
        see the wrong value.
      * `sc` and `tw/twi` are treated as sequential; the actual next PC
        after a trap depends on the system handler.
      * If a successor address already has an active BP, set_breakpoint
        will raise (slot conflict). Workaround: don't BP an address
        twice. (Future: detect and reuse the existing BP.)
    """
    if not is_hit(bp):
        raise RuntimeError(f"step requires {bp} to be in the hit state")

    snap = read_snapshot(bp)
    successors = decode_successors(bp.original_word, bp.target,
                                   snap["lr"], snap["ctr"])

    new_bps = [set_breakpoint(bp.h, addr) for addr in successors]
    continue_(bp)

    deadline = time.time() + timeout_s
    hit = None
    while time.time() < deadline:
        for nbp in new_bps:
            if is_hit(nbp):
                hit = nbp
                break
        if hit is not None:
            break
        time.sleep(0.001)

    # Clean up the unused successor BPs.
    for nbp in new_bps:
        if nbp is not hit:
            remove_breakpoint(nbp)

    if hit is None:
        raise TimeoutError(
            f"step from 0x{bp.target:08X} (insn 0x{bp.original_word:08X}): "
            f"no successor BP fired within {timeout_s:.1f}s. Successors: "
            + ", ".join(f"0x{a:08X}" for a in successors))

    return hit


# ---------------------------------------------------------------------------
# Conditional breakpoints (Python-side predicate).
# ---------------------------------------------------------------------------
def wait_for_condition(bp, predicate, timeout_s=60.0, max_skips=10000):
    """Like wait_for_hit, but only returns when predicate(snapshot) is True.
    Hits where the predicate returns False are silently released via
    continue_(bp). Useful for "stop only when r3 == 0x18" or "stop only on
    the 5th time through."

    Per-hit cost: one snapshot read + (if skipped) one continue. That's
    a few ms per skipped hit -- cheap if you skip thousands, but if you
    expect 100k+ skips, build a handler-side conditional instead.
    """
    deadline = time.time() + timeout_s
    for _ in range(max_skips + 1):
        remaining = max(0.5, deadline - time.time())
        wait_for_hit(bp, timeout_s=remaining)
        snap = read_snapshot(bp)
        if predicate(snap):
            return snap
        continue_(bp)
        if time.time() >= deadline:
            break
    raise TimeoutError(
        f"BP at 0x{bp.target:08X} predicate did not match within "
        f"{timeout_s:.1f}s / {max_skips} skips")
