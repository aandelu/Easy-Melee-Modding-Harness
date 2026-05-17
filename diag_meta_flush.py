"""Diagnostic: dump where the meta-flush gecko landed, compare to expected
LOGIC, and check that dme writes to FLUSH_REQUEST land + persist (or get
cleared by the gecko).

Three independent checks:
  1. After launch, is hook 0x803775C0 patched to a branch? -> if not, the
     gecko didn't install at all.
  2. Following that branch, does the cave content match META_FLUSH_LOGIC
     word-for-word? -> if not, the codehandler placed something else or
     our encoded bytes are wrong.
  3. After CPU is ticking, can we set FLUSH_REQUEST = 0xDEADBEEF via dme
     and observe it (a) staying set if the gecko is broken, or (b)
     getting cleared by the gecko within a few frames if the gecko works?
"""
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw


def main():
    h = Harness()
    try:
        print("install + launch")
        iw.install_meta_flush(h)
        h.launch()
        h.hook_dme()

        print("waiting for CPU")
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"  CPU live ({prev} -> {cur})")
                break
            prev = cur

        # Give the codehandler a beat past just-CPU-live so all geckos are in.
        time.sleep(1.0)

        # --- Check 1: hook patched? ---
        hook_word = h.read_word(iw.META_FLUSH_HOOK)
        print(f"\nhook 0x{iw.META_FLUSH_HOOK:08X} = 0x{hook_word:08X}")
        if (hook_word & 0xFC000000) != 0x48000000:
            print("  hook is NOT a branch -- gecko did NOT install")
            return 1

        disp = hook_word & 0x03FFFFFC
        if disp & 0x02000000:
            disp -= 0x04000000
        cave = (iw.META_FLUSH_HOOK + disp) & 0xFFFFFFFF
        print(f"  cave resolves to 0x{cave:08X}")

        # --- Check 2: cave bytes match LOGIC? ---
        n_logic = len(iw.META_FLUSH_LOGIC)
        n_total = n_logic + 2  # + displaced + branch-back
        words = [h.read_word(cave + i * 4) for i in range(n_total)]
        print(f"\ncave dump ({n_total} words; logic={n_logic} + "
              f"displaced + branch-back):")
        mismatches = 0
        for i in range(n_total):
            if i < n_logic:
                exp = iw.META_FLUSH_LOGIC[i]
                ok = words[i] == exp
                if not ok:
                    mismatches += 1
                tag = "OK" if ok else "**MISMATCH**"
                print(f"  [{i:2d}]  read 0x{words[i]:08X}  "
                      f"exp 0x{exp:08X}  {tag}")
            elif i == n_logic:
                exp = iw.META_FLUSH_ORIG
                ok = words[i] == exp
                tag = "OK" if ok else "**MISMATCH**"
                print(f"  [{i:2d}]  read 0x{words[i]:08X}  "
                      f"exp 0x{exp:08X}  (displaced)  {tag}")
            else:
                print(f"  [{i:2d}]  read 0x{words[i]:08X}  (branch back)")

        if mismatches:
            print(f"\n[FAIL] {mismatches} mismatches in cave -- encoded bytes "
                  "are not what landed in MEM1")
            return 1

        # --- Check 3: FLUSH_REQUEST round-trip ---
        print(f"\nFLUSH_REQUEST control plane @ 0x{iw.FLUSH_REQUEST:08X}")
        print(f"  initial read:        0x{h.read_word(iw.FLUSH_REQUEST):08X}")
        print(f"  FLUSH_START initial: 0x{h.read_word(iw.FLUSH_START):08X}")
        print(f"  FLUSH_END initial:   0x{h.read_word(iw.FLUSH_END):08X}")

        # First: prove dme writes to this region land at all.
        h.write_words(iw.FLUSH_REQUEST, [0xCAFEBABE])
        v = h.read_word(iw.FLUSH_REQUEST)
        print(f"\nafter dme-write 0xCAFEBABE -> FLUSH_REQUEST: read 0x{v:08X}")
        if v != 0xCAFEBABE:
            print("  [FAIL] dme write to FLUSH_REQUEST didn't land")
            return 1

        # Now arm the magic and watch for the gecko to clear it.
        h.write_words(iw.FLUSH_END, [iw.FLUSH_REQUEST])  # zero-length range
        h.write_words(iw.FLUSH_START, [iw.FLUSH_REQUEST])
        h.write_words(iw.FLUSH_REQUEST, [iw.FLUSH_MAGIC])
        print(f"\narmed magic, polling for gecko to clear (10 reads, 100ms apart)")
        for i in range(10):
            time.sleep(0.1)
            v = h.read_word(iw.FLUSH_REQUEST)
            tag = " <-- CLEARED" if v == 0 else ""
            print(f"  t={(i+1)*0.1:.1f}s  FLUSH_REQUEST=0x{v:08X}{tag}")
            if v == 0:
                print("\n[PASS] gecko fires and clears the magic. Phase 1 OK.")
                return 0

        print("\n[FAIL] gecko never cleared the magic in 1s. Cave bytes are "
              "correct, hook is a branch, dme writes land -- yet the gecko's "
              "logic isn't running OR the magic comparison fails. Likely "
              "issue: lis/ori or cmpw encoding subtlety. Inspect cave's "
              "decoded asm.")
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
