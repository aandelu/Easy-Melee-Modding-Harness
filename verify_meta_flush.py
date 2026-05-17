"""Smoke test for the meta-flush gecko (Phase 1 of Option B).

Same end-state proof as `verify_inject_gecko.py` -- a per-frame hook is
patched to branch into a code cave that increments a counter -- but the
patch is installed at *runtime* via dme + the meta-flush gecko, not by the
bootloader. If the counter advances, dme code patches now take effect.

This is the experiment `old&unused/diag_inject_no_savestate.py` ran and
failed (no flush primitive available then). Success here proves the
meta-flush primitive bridges the gap.

Layout:
  meta-flush gecko -> hooked at 0x803775C0 (per-frame pad-process loop)
  test payload     -> at 0x803FA600 (in DEFAULT_CAVE region)
  test hook        -> 0x803775B8 (per-frame pad-read, separate from meta-flush)
  test counter     -> 0x803FA428 (same scratch verify_inject_gecko uses)
"""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw


TEST_HOOK      = 0x803775B8
TEST_HOOK_ORIG = 0xA0190000           # lhz r0, 0(r25)
TEST_CAVE      = 0x803FA600
TEST_COUNTER   = 0x803FA428


def build_test_payload():
    """Counter-incrementing payload that ends with the displaced original
    and a branch back to TEST_HOOK + 4. 7 words total."""
    # b TEST_HOOK+4 from TEST_CAVE + 6*4 = the 7th instruction:
    branch_back_pc = TEST_CAVE + 6 * 4
    branch_back_offset = (TEST_HOOK + 4 - branch_back_pc) & 0x03FFFFFC
    return [
        0x3D80803F,                       # lis  r12, 0x803F
        0x618CA428,                       # ori  r12, r12, 0xA428   ; counter addr
        0x816C0000,                       # lwz  r11, 0(r12)
        0x396B0001,                       # addi r11, r11, 1
        0x916C0000,                       # stw  r11, 0(r12)
        TEST_HOOK_ORIG,                   # displaced original
        0x48000000 | branch_back_offset,  # b   TEST_HOOK + 4
    ]


def main():
    h = Harness()
    try:
        print("staging meta-flush gecko + launching", flush=True)
        iw.install_meta_flush(h)
        h.launch()
        h.hook_dme()

        # Wait for the bootloader to finish + the gecko to be live.
        print("waiting for CPU to tick ...", flush=True)
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"  CPU live ({prev} -> {cur})", flush=True)
                break
            prev = cur
        else:
            print("[FAIL] CPU never started ticking", flush=True)
            return 1

        # Wait for the codehandler to finish installing the gecko (the CPU
        # tick check above can race ahead of gecko-install by 0.5-1.5 s).
        print("\nwaiting for meta-flush gecko to come alive ...", flush=True)
        try:
            iw.wait_for_meta_flush_alive(h)
            print("  gecko is responding to flush requests", flush=True)
        except TimeoutError as e:
            print(f"[FAIL] {e}", flush=True)
            return 1

        # Install the counter payload into the cave (data write + flush).
        payload = build_test_payload()
        print(f"\nwriting {len(payload)}-word test payload to "
              f"0x{TEST_CAVE:08X}", flush=True)
        iw.write_instrs(h, TEST_CAVE, payload)
        readback = [h.read_word(TEST_CAVE + i * 4) for i in range(len(payload))]
        ok = all(readback[i] == payload[i] for i in range(len(payload)))
        print(f"  cave readback matches: {ok}", flush=True)
        if not ok:
            for i, (w, r) in enumerate(zip(payload, readback)):
                marker = "OK" if w == r else "**MISMATCH**"
                print(f"    [{i}] wrote 0x{w:08X}  read 0x{r:08X}  {marker}",
                      flush=True)
            print("[FAIL] cave write did not stick", flush=True)
            return 1

        # Patch the test hook to branch into the cave (instruction write + flush).
        before = h.read_word(TEST_HOOK)
        print(f"\nhook 0x{TEST_HOOK:08X} before patch = 0x{before:08X} "
              f"(expected 0x{TEST_HOOK_ORIG:08X})", flush=True)
        if before != TEST_HOOK_ORIG:
            print("[FAIL] hook's vanilla word doesn't match expectation -- "
                  "is this the right Slippi build / Melee revision?", flush=True)
            return 1
        iw.patch_branch(h, TEST_HOOK, TEST_CAVE)
        after = h.read_word(TEST_HOOK)
        expected_branch = 0x48000000 | ((TEST_CAVE - TEST_HOOK) & 0x03FFFFFC)
        print(f"hook 0x{TEST_HOOK:08X} after  patch = 0x{after:08X} "
              f"(expected 0x{expected_branch:08X})", flush=True)
        if after != expected_branch:
            print("[FAIL] hook patch byte-level write failed", flush=True)
            return 1

        # Watch the counter.
        before = h.read_word(TEST_COUNTER)
        print(f"\npre-watch counter @ 0x{TEST_COUNTER:08X} = 0x{before:08X}",
              flush=True)
        print("polling counter for 15s ...", flush=True)
        last = before
        for i in range(15):
            time.sleep(1.0)
            cur = h.read_word(TEST_COUNTER)
            delta = (cur - before) & 0xFFFFFFFF
            print(f"  t={i + 1:2d}s  counter=0x{cur:08X}  (delta {delta})",
                  flush=True)
            last = cur

        delta = (last - before) & 0xFFFFFFFF
        if delta > 0:
            print(f"\n[PASS] dme-written instructions take effect after "
                  f"meta-flush ({delta} hits in 15s ~= {delta / 15:.0f}/s). "
                  f"Option B is unblocked.", flush=True)
            return 0
        print("\n[FAIL] counter never advanced. Meta-flush gecko reported "
              "completion but the CPU still doesn't observe the dme-installed "
              "code. Need to inspect: did flush_range really clear the magic? "
              "Are dcbf/icbi actually issued by the gecko (cave-dump it)? "
              "Is the test hook firing at all (drop the indirection: patch a "
              "trap word and see if Dolphin panics)?", flush=True)
        return 1
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
