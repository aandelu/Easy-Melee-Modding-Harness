"""
Phase-1 dme debugger: a "meta-flush" gecko that, on dme request, runs
`dcbf` / `sync` / `icbi` / `isync` over a caller-specified [start, end) byte
range.

Why this exists
---------------
dme writes to MEM1 take effect at the byte level (re-reads return the new
bytes) but the emulated CPU's instruction-fetch path does NOT observe writes
to *instruction memory* even in pure interpreter mode -- proven by
`old&unused/diag_inject_no_savestate.log`. Slippi's bootloader successfully
installs gecko codes because it runs dcbf/icbi/isync via the emulated CPU,
which invalidates Dolphin's internal block cache.

We exploit the same primitive: install ONE persistent gecko at boot whose only
job is to flush a dme-controlled range on demand. After that, the harness can
write new PPC instructions anywhere in MEM1 and have them take effect within
~1 frame (~17 ms) without rebooting per candidate.

Lifecycle
---------
    h = Harness()
    install_meta_flush(h)        # before launch
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive()
    # ... now patch at will:
    write_instrs(h, cave, [insn_word, insn_word, ...])
    patch_branch(h, hook_addr, cave)   # writes a `b cave` at hook_addr + flush
"""
import time

# ---------------------------------------------------------------------------
# Meta-flush gecko: hooks 0x803775C0 (per-frame pad-process loop). Vanilla
# instruction there is `lbz r0, 2(r25)` = 0x88190002; r0 will be clobbered by
# it after our logic runs, so r0 is free. r9/r10/r11/r12 are also clobbered
# (all volatile in PPC EABI). r25 (PADStatus ptr) is untouched.
#
# r0 trap: in `addi rD, rA, simm` (and a handful of other forms), rA=0
# reads as the literal value 0, not the contents of GPR0 -- so we can't
# use r0 as the cursor register in the dcbf/icbi loops. We use r9 for
# that and reserve r0 for the magic-word load only.
# ---------------------------------------------------------------------------
META_FLUSH_HOOK = 0x803775C0
META_FLUSH_ORIG = 0x88190002

# Control-plane scratch (debug-menu tables region; safe-to-clobber per
# Free_Memory.csv). Magic-word gating defends against the gecko firing on
# garbage MEM1 contents before dme has set anything up.
FLUSH_REQUEST = 0x803FA440   # word: 0xDEADBEEF = request, anything else = idle
FLUSH_START   = 0x803FA444   # word: start byte address (inclusive)
FLUSH_END     = 0x803FA448   # word: end   byte address (exclusive)
FLUSH_MAGIC   = 0xDEADBEEF

META_FLUSH_LOGIC = [
    # idx 0..1: r12 = &FLUSH_REQUEST
    0x3D80803F,   # lis    r12, 0x803F
    0x618CA440,   # ori    r12, r12, 0xA440
    # idx 2..6: if (*r12 != 0xDEADBEEF) goto END
    0x800C0000,   # lwz    r0, 0(r12)
    0x3D60DEAD,   # lis    r11, 0xDEAD
    0x616BBEEF,   # ori    r11, r11, 0xBEEF
    0x7C005800,   # cmpw   r0, r11
    0x40820058,   # bne    END   (target = idx 28, offset (28-6)*4 = 0x58)
    # idx 7..8: load range  (r10 = FLUSH_END, NOT r0 -- see r0 trap above)
    0x816C0004,   # lwz    r11, 4(r12)   ; r11 = FLUSH_START
    0x814C0008,   # lwz    r10, 8(r12)   ; r10 = FLUSH_END
    # idx 9..11: align start down, end up, to 32-byte cache-line boundary
    0x556B0034,   # rlwinm r11, r11, 0, 0, 26    ; r11 &= ~0x1F
    0x394A001F,   # addi   r10, r10, 31
    0x554A0034,   # rlwinm r10, r10, 0, 0, 26    ; r10 &= ~0x1F
    # idx 12..18: dcbf loop, then sync (r9 = cursor)
    0x7D695B78,   # mr     r9, r11
    0x7C095040,   # cmplw  r9, r10       [DCBF_LOOP]
    0x40800010,   # bge    DCBF_DONE     (target = idx 18, offset 0x10)
    0x7C0048AC,   # dcbf   0, r9
    0x39290020,   # addi   r9, r9, 32
    0x4BFFFFF0,   # b      DCBF_LOOP     (back 4 instructions = -0x10)
    0x7C0004AC,   # sync                 [DCBF_DONE]
    # idx 19..25: icbi loop, then isync
    0x7D695B78,   # mr     r9, r11
    0x7C095040,   # cmplw  r9, r10       [ICBI_LOOP]
    0x40800010,   # bge    ICBI_DONE     (target = idx 25, offset 0x10)
    0x7C004FAC,   # icbi   0, r9
    0x39290020,   # addi   r9, r9, 32
    0x4BFFFFF0,   # b      ICBI_LOOP
    0x4C00012C,   # isync                [ICBI_DONE]
    # idx 26..27: clear magic so dme observes completion
    0x38000000,   # li     r0, 0
    0x900C0000,   # stw    r0, 0(r12)
    # idx 28 (END): gecko_c2_lines appends displaced original + branch back.
]
assert len(META_FLUSH_LOGIC) == 28


def install_meta_flush(harness):
    """Stage the meta-flush gecko on `harness`. MUST be called before launch()."""
    harness.install_gecko_c2(
        name="meta-flush",
        hook_addr=META_FLUSH_HOOK,
        logic_words=META_FLUSH_LOGIC,
        displaced_orig=META_FLUSH_ORIG,
    )


def wait_for_meta_flush_alive(harness, timeout_s=15.0, poll_interval=0.2):
    """Block until the meta-flush gecko is responsive.

    Slippi's codehandler installs gecko codes AFTER the CPU has started
    ticking -- there's a window of ~0.5-1.5 s where dme can hook + read
    memory but the gecko hook isn't patched yet. flush_range() called too
    early will time out even though everything is correct.

    This helper arms the magic and polls until cleared, re-arming each
    iteration so a transient miss doesn't waste the whole timeout.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        harness.write_words(FLUSH_END, [FLUSH_REQUEST])
        harness.write_words(FLUSH_START, [FLUSH_REQUEST])
        harness.write_words(FLUSH_REQUEST, [FLUSH_MAGIC])
        time.sleep(poll_interval)
        if harness.read_word(FLUSH_REQUEST) == 0:
            return
    raise TimeoutError(
        f"meta-flush gecko did not respond within {timeout_s:.1f}s -- "
        "gecko probably not installed (check hook 0x803775C0 reads as a "
        "branch; check GameSettings/GALE01r2.ini was staged correctly).")


def flush_range(harness, start, end, timeout_s=1.0, poll_interval=0.005):
    """Ask the meta-flush gecko to dcbf+sync+icbi+isync the [start, end) range.

    Returns when the gecko has cleared FLUSH_REQUEST (proof it executed),
    raises TimeoutError otherwise. Caller-side cache-line alignment isn't
    required -- the gecko aligns internally to 32-byte boundaries.

    Zero-length ranges (start == end) are valid as a "ping" -- the gecko
    will skip both loops, still clear the magic, and return ~1 frame later.
    """
    # End/start first so the gecko never observes a partially-updated
    # control block (it gates on FLUSH_REQUEST, written last).
    harness.write_words(FLUSH_END, [end])
    harness.write_words(FLUSH_START, [start])
    harness.write_words(FLUSH_REQUEST, [FLUSH_MAGIC])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if harness.read_word(FLUSH_REQUEST) == 0:
            return
        time.sleep(poll_interval)
    raise TimeoutError(
        f"flush_range({start:#010x}, {end:#010x}) timed out after "
        f"{timeout_s:.2f}s. Meta-flush gecko not installed or not firing?")


def write_instrs(harness, addr, words):
    """Write one or more 32-bit instruction words at `addr` and flush.

    Use this for any patch to executable code memory. After the call returns,
    the emulated CPU will observe the new bytes on its next fetch from the
    affected cache lines.

    For batched patches across disjoint regions, write each region with
    write_instrs(); each call does its own flush.
    """
    if not words:
        return
    harness.write_words(addr, words)
    flush_range(harness, addr, addr + len(words) * 4)


def patch_branch(harness, src_addr, dst_addr, link=False):
    """Overwrite the instruction at `src_addr` with an unconditional branch
    to `dst_addr` (`bl` if link=True) and flush so the patch is observed.

    Convenience wrapper -- equivalent to encoding the branch yourself and
    calling write_instrs(harness, src_addr, [encoded]).
    """
    offset = (dst_addr - src_addr) & 0x03FFFFFC
    word = 0x48000000 | offset | (1 if link else 0)
    write_instrs(harness, src_addr, [word])
