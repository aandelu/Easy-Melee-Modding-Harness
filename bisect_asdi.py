"""bisect_asdi.py -- find which part of the ASDI payload wedges the CPU during
seed_snapshot's save+overlay+load round-trip.

verify_d_standalone_v2 passes on the same hook and the same round-trip, so the
harness path is fine and the fault is somewhere in our logic. Each variant gets a
fresh Dolphin; we report survived / wedged per variant.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 bisect_asdi.py
"""
import subprocess
import sys
import time

import gecko_tools as gt
import instr_writer as iw
from melee_harness import Harness, POWERON_COUNT

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000        # lhz r0, 0(r25)

BOUND = """
    cmplwi 24, 3
    bgt    done
"""
CHAIN = """
    lis    12, 0x8045
    ori    12, 12, 0x3130
    mulli  9, 24, 0xE90
    add    12, 12, 9
    lwz    12, 0(12)
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80
    bne    done
    lwz    12, 0x2C(12)
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80
    bne    done
"""
GATES = """
    lwz    0, 0x195C(12)
    cmpwi  0, 0
    beq    done
    lwz    0, 0x2340(12)
    cmpwi  0, 0
    beq    done
    lwz    0, 0xE0(12)
    cmpwi  0, 1
    bne    done
    lwz    0, 0xB4(12)
    lwz    9, 0x834(12)
    cmpw   0, 9
    bne    done
"""
STORE = """
    li     0, 0x90
    stb    0, 5(25)
"""

VARIANTS = [
    ("A bound only",          BOUND + "\ndone:\n"),
    ("B + pointer chain",     BOUND + CHAIN + "\ndone:\n"),
    ("C + gates",             BOUND + CHAIN + GATES + "\ndone:\n"),
    ("D + store (full)",      BOUND + CHAIN + GATES + STORE + "\ndone:\n"),
]


def clean_dolphin():
    subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    for _ in range(40):
        if subprocess.run(["pgrep", "-x", "Dolphin"],
                          capture_output=True).returncode != 0:
            return
        time.sleep(0.5)


def try_variant(name, src):
    words = gt.assemble(src, addr=0)
    clean_dolphin()
    h = Harness()
    # meta-flush FIRST -- verify_d_standalone_v2 / play_d2 both do this before
    # staging their candidate, and without it the seed_snapshot round-trip wedges
    # the CPU even for a 2-instruction no-op payload.
    iw.install_meta_flush(h)
    h.install_gecko_c2(name=f"asdi-bisect", hook_addr=HOOK_ADDR,
                       logic_words=words, displaced_orig=DISPLACED_ORIG)
    try:
        h.launch()
        h.hook_dme()
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                break
            prev = cur
        h.seed_snapshot(timeout_s=60.0)
        return True, f"{len(words)} words"
    except Exception as e:
        return False, f"{len(words)} words -- {type(e).__name__}: {e}"
    finally:
        try:
            h.close()
        except Exception:
            pass


def main():
    results = []
    for name, src in VARIANTS:
        print(f"\n{'=' * 60}\n[bisect] {name}\n{'=' * 60}", flush=True)
        ok, detail = try_variant(name, src)
        results.append((name, ok, detail))
        print(f"[bisect] {name}: {'SURVIVED' if ok else 'WEDGED'} ({detail})", flush=True)
        if not ok:
            break          # first failure localizes it; no point going further
    clean_dolphin()
    print(f"\n{'=' * 60}\nRESULTS\n{'=' * 60}")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
