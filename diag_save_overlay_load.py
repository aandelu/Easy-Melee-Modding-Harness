"""Diagnose: can a save+overlay+load round-trip force a JIT cache flush so
the dme-overlaid gecko actually runs after F2 wipes it?

Plan:
  1. Boot with the counter gecko. Slippi's codehandler installs it.
  2. Scan MEM1 for the unique LOGIC[0..N] pattern -> discover the actual
     cave_addr (NOT DEFAULT_CAVE -- the codehandler picks its own slot).
  3. Phase A: watch counter pre-F2.  expect: advances.
  4. F2 (load slot 2). Phase B watch.  expect: freezes (savestate wipe).
  5. dme overlay: write the cave body back at cave_addr, repatch hook.
     Phase C watch.  expect: freezes (JIT cache holds stale block).
  6. Shift+F1 to save slot 1 (captures our overlay-in-MEM1).
  7. F1 to load slot 1 (triggers JIT cache flush). Phase D watch.
     If gecko runs again: save+load trick works.
"""
import struct
import sys
import time

from melee_harness import (
    Harness, MEM1_BASE, POWERON_COUNT, make_branch,
)


HOOK_ADDR = 0x803775C0
HOOK_ORIG = 0x88190002
COUNTER_ADDR = 0x803FA428

LOGIC = [
    0x3D80803F,    # lis  r12, 0x803F
    0x618CA428,    # ori  r12, r12, 0xA428
    0x816C0000,    # lwz  r11, 0(r12)
    0x396B0001,    # addi r11, r11, 1
    0x916C0000,    # stw  r11, 0(r12)
]


def watch(h, label: str, seconds: int = 5):
    before = h.read_word(COUNTER_ADDR)
    print(f"\n[{label}] start counter=0x{before:08X}", flush=True)
    for i in range(seconds):
        time.sleep(1.0)
        cur = h.read_word(COUNTER_ADDR)
        delta = (cur - before) & 0xFFFFFFFF
        print(f"  t={i + 1}s  counter=0x{cur:08X}  delta={delta}", flush=True)
    final = h.read_word(COUNTER_ADDR)
    return (final - before) & 0xFFFFFFFF


def find_cave(h):
    """Scan MEM1 chunk-by-chunk for the contiguous LOGIC pattern."""
    sig = b"".join(struct.pack(">I", w) for w in LOGIC)
    chunk_size = 1 << 20      # 1 MiB
    addr = MEM1_BASE
    end = MEM1_BASE + (24 << 20)
    while addr < end:
        n = min(chunk_size, end - addr)
        # Allow overlap so a signature straddling a chunk boundary is found.
        n_read = min(n + len(sig), end - addr)
        buf = h.read_bytes(addr, n_read)
        idx = buf.find(sig)
        if idx >= 0:
            return addr + idx
        addr += chunk_size
    return None


def cave_payload(cave_addr: int) -> list:
    body = list(LOGIC) + [HOOK_ORIG]
    branch_back_src = cave_addr + len(body) * 4
    body.append(make_branch(branch_back_src, HOOK_ADDR + 4))
    return body


def main():
    h = Harness()
    h.install_gecko_c2(
        name="diag-save-overlay-load",
        hook_addr=HOOK_ADDR,
        logic_words=LOGIC,
        displaced_orig=HOOK_ORIG,
    )
    try:
        h.launch()
        h.hook_dme()
        h._wait_for_cpu_alive(timeout_s=30.0)

        print("\nscanning MEM1 for the installed cave ...", flush=True)
        cave_addr = find_cave(h)
        if cave_addr is None:
            print("[FATAL] LOGIC pattern not found in MEM1 -- "
                  "boot install did not place our code.", flush=True)
            return 1
        print(f"  cave found at 0x{cave_addr:08X}", flush=True)
        hook_word_pre = h.read_word(HOOK_ADDR)
        print(f"  hook 0x{HOOK_ADDR:08X} reads as 0x{hook_word_pre:08X}",
              flush=True)

        delta_a = watch(h, "Phase A (post-launch, pre-F2)")

        print("\n>>> F2 to load slot 2 <<<", flush=True)
        h.load_savestate(slot=2, timeout_s=30.0)
        cave_after_f2 = h.read_word(cave_addr)
        print(f"  cave[0] after F2 = 0x{cave_after_f2:08X}  "
              f"(was 0x{LOGIC[0]:08X})", flush=True)
        hook_after_f2 = h.read_word(HOOK_ADDR)
        print(f"  hook after F2    = 0x{hook_after_f2:08X}  "
              f"(orig 0x{HOOK_ORIG:08X})", flush=True)
        delta_b = watch(h, "Phase B (post-F2, no overlay)")

        # Overlay the cave + repatch the hook via dme.
        payload = cave_payload(cave_addr)
        print(f"\n>>> dme overlay -> cave 0x{cave_addr:08X} "
              f"({len(payload)} words) + hook branch <<<", flush=True)
        h.write_words(cave_addr, payload)
        h.write_words(HOOK_ADDR, [make_branch(HOOK_ADDR, cave_addr)])
        h.write_words(COUNTER_ADDR, [0])     # zero so any post-overlay hit shows
        delta_c = watch(h, "Phase C (post-F2, dme overlay, no save/load)")

        print("\n>>> Shift+F1 to save slot 1, then F1 to reload <<<",
              flush=True)
        h.save_savestate(slot=1)
        h.load_savestate(slot=1, wait_in_game=False)
        # Sanity-check that slot 1 retained our overlay in MEM1.
        cave_after_reload = h.read_word(cave_addr)
        hook_after_reload = h.read_word(HOOK_ADDR)
        print(f"  cave[0] after reload = 0x{cave_after_reload:08X}  "
              f"(want 0x{LOGIC[0]:08X})", flush=True)
        print(f"  hook after reload    = 0x{hook_after_reload:08X}  "
              f"(want a branch 0x48xxxxxx)", flush=True)
        h.write_words(COUNTER_ADDR, [0])
        delta_d = watch(h, "Phase D (post-save+load, gecko should resume)")

        print("\n" + "=" * 60, flush=True)
        print(f"Phase A (pre-F2):                 {delta_a}", flush=True)
        print(f"Phase B (post-F2, no overlay):    {delta_b}", flush=True)
        print(f"Phase C (post-F2, overlay only):  {delta_c}", flush=True)
        print(f"Phase D (post-save+load):         {delta_d}", flush=True)
        print("=" * 60, flush=True)
        if delta_a == 0:
            print("\n[FATAL] gecko never ran at boot", flush=True)
            return 1
        if delta_d > 0:
            print("\n[INFO] save+overlay+load forces a JIT cache flush -- "
                  "this is a viable reset architecture.", flush=True)
        else:
            print("\n[INFO] save+overlay+load did NOT restore gecko execution. "
                  "Save-state's snapshot path may not include the dme-modified "
                  "MEM1 region, OR the JIT cache isn't flushed by F1 load.",
                  flush=True)
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
