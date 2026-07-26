"""asdi_probe_offline.py -- OFFLINE proof that a C-stick-down injection during hitlag
produces a floorhug (ASDI down -> air/ground transition -> hitstun dumped -> ~4f landing
lag instead of the full hitstun).

Offline only: hooks the CONSUMER side (0x803775B8), which desyncs online but needs no
ODB and no scene gate.  Once the mechanic is proven here, the same gate moves to the
producer hook 0x8034E680 for the shipped netplay-safe gecko.

Gate (all raw word compares -- no FPU), measured by observe_hitlag.py 2026-07-25:
  hitlag  != 0   +0x195C     in hitlag
  hitstun != 0   +0x2340     VICTIM, not the attacker (an aerial attacker is airborne
                             in his own hitlag too, but always has hitstun 0)
  air     == 1   +0xE0       the hit launched us

No grounded-when-hit clause in v1 -- see the note in SRC. It belongs to v2 (SDI),
and needs a real float tolerance, not the exact word compare a 2-decimal log implied.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_probe_offline.py [seconds]
"""
import sys
import time

import gecko_tools as gt
import instr_writer as iw
import observe_hitlag as oh
from melee_harness import Harness, POWERON_COUNT

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000        # lhz r0, 0(r25)
NAME = "asdi-floorhug-offline-probe"

CSTICK_Y_DOWN = 0x90               # signed -112 = full down (wavedash: full defl = 0x70)
PAD_CSTICK_Y = 5                   # pad struct +0x5 (REFERENCE.md 2.3)

# In-cave gate counters. Clear of the meta-flush control plane (0x803FA440-0x44C)
# and the offline scratch at 0x803FA470. One counter per gate clause, so a failed
# run says exactly WHICH clause rejected instead of just "nothing happened".
COUNTERS = 0x803FA700
CNAMES = ["reached", "chain_ok", "hitlag", "hitstun", "air", "fired",
          "fired_p0", "fired_p1", "fired_p2", "fired_p3"]


def _bump(i):
    """r11 holds the counter base; r9 is the scratch. Both clobber-safe here."""
    return f"""
    lwz    9, {i * 4}(11)
    addi   9, 9, 1
    stw    9, {i * 4}(11)
"""


SRC = f"""
    /* r24 = 0-indexed port, r25 = pad struct. r0/r9/r11/r12 clobber-safe here
       (proven by candidate_d_standalone_v2 at this same hook). */
    cmplwi 24, 3                   /* bound the port BEFORE using it as an index -- */
    bgt    done                    /* unsigned, so garbage/negative r24 bails too.  */
    lis    11, 0x803F
    ori    11, 11, 0xA700          /* counter base, live for the whole payload */
{_bump(0)}
    lis    12, 0x8045
    ori    12, 12, 0x3130          /* 0x80453130 = P1 GObj ptr */
    mulli  9, 24, 0xE90            /* + port * stride */
    add    12, 12, 9
    lwz    12, 0(12)               /* GObj */
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80                 /* MEM1? garbage pointers crash Dolphin */
    bne    done
    lwz    12, 0x2C(12)            /* Player Data */
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80
    bne    done
{_bump(1)}
    lwz    0, 0x195C(12)           /* hitlag counter (float; +0.0 is all-zero bits) */
    cmpwi  0, 0
    beq    done
{_bump(2)}
    lwz    0, 0x2340(12)           /* hitstun -- separates victim from attacker */
    cmpwi  0, 0
    beq    done
{_bump(3)}
    lwz    0, 0xE0(12)             /* ground/air state */
    cmpwi  0, 1
    bne    done
{_bump(4)}
    /* NO grounded-when-hit gate in v1. y == last-landed-y looked exact in the
       baseline only because that log rounded to 2 decimals; the raw words differ.
       ASDI moves 3 units, so omitting it is near-harmless here -- the check earns
       its keep in v2 where SDI (6 units/frame) can drag you offstage. v2 wants
       (y - land_y) < 3.0 as a real float compare, sized from the raw bits we now log. */
{_bump(5)}
    /* per-port fire counter at CNAMES index 6+r24, so we can tell WHICH port's
       gate passed -- one aggregate counter can't distinguish victim from attacker. */
    slwi   9, 24, 2
    add    9, 9, 11
    lwz    12, 24(9)               /* r12, NOT r0: `addi r0,r0,1` assembles to */
    addi   12, 12, 1               /* `li r0,1` -- rA=0 means literal 0 (REFERENCE 4). */
    stw    12, 24(9)               /* Player Data in r12 is dead by here.              */

    li     0, {CSTICK_Y_DOWN}      /* ASDI down: full-down c-stick for one hitlag frame */
    stb    0, {PAD_CSTICK_Y}(25)
done:
"""


def build():
    words = gt.assemble(SRC, addr=0)
    print(f"[asdi] assembled {len(words)} words; capstone readback:")
    for line in gt.disasm(words, addr=0):
        print(f"    {line}")
    # the two things that must survive: the store, and no stray branch out of the cave
    txt = "\n".join(gt.disasm(words, addr=0))
    assert f"stb" in txt, "c-stick store missing"
    return words


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 300.0
    words = build()

    h = Harness()
    print(f"[asdi] staging C2 at 0x{HOOK_ADDR:08X}", flush=True)
    # meta-flush MUST be staged first. Without it seed_snapshot's save+overlay+load
    # round-trip wedges the CPU -- even for a 2-instruction no-op payload (proven by
    # bisect_asdi.py). Every working script in this repo installs it first.
    iw.install_meta_flush(h)
    h.install_gecko_c2(name=NAME, hook_addr=HOOK_ADDR,
                       logic_words=words, displaced_orig=DISPLACED_ORIG)
    oh.bring_up(h)

    # A C2 can silently fail to install and leave the hook vanilla (REFERENCE 3.1).
    # Then every "no floorhug" result below would be meaningless.
    hooked = h.read_word(HOOK_ADDR)
    if hooked == DISPLACED_ORIG:
        print(f"[asdi] ABORT: hook 0x{HOOK_ADDR:08X} is still vanilla "
              f"(0x{hooked:08X}) -- the C2 did not install.")
        return 1
    print(f"[asdi] hook patched: 0x{hooked:08X} (was 0x{DISPLACED_ORIG:08X})", flush=True)
    h.write_words(COUNTERS, [0] * len(CNAMES))   # snapshot may carry stale values

    pds, names = {}, {}
    for port in (1, 2, 3, 4):
        try:
            pd = h.player_data_ptr(port)
            cid = h.char_id(port) if pd != -1 else -1
        except Exception:
            continue
        if pd != -1:
            pds[port], names[port] = pd, oh.CHARS.get(cid, f"char 0x{cid:02X}")
    if not pds:
        print("[asdi] no valid Player Data -- not in a match?")
        return 1
    print("[asdi] tracking " + "  ".join(f"P{p}={names[p]}" for p in sorted(pds)))
    print("=" * 70)
    print("ASDI PROBE LIVE -- hit a GROUNDED opponent, same as the baseline run.")
    print("Watch cstickY: it should read -1.00 during the victim's hitlag.")
    print("Floorhug = hitstun drops to 0 and air goes 1->0 within a frame or two")
    print("of hitlag ending, instead of a long airborne hitstun arc.")
    print("=" * 70, flush=True)

    def counters():
        try:
            return [h.read_word(COUNTERS + i * 4) for i in range(len(CNAMES))]
        except Exception:
            return None

    active = {p: None for p in pds}
    events = {p: 0 for p in pds}
    last_ctr = [0] * len(CNAMES)
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            c = counters()
            if c and c != last_ctr:
                # a clause that never advances is the one rejecting
                print("[gate] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, c)),
                      flush=True)
                last_ctr = c
            try:
                fr = h.read_word(oh.FRAME)
            except Exception:
                oh.ensure_hooked()
                time.sleep(0.05)
                continue
            for port in list(pds):
                # Re-resolve every frame: a death + respawn moves Player Data, and a
                # pointer cached at startup then reads a stale struct (attach_observe_
                # wavedash.py does the same for exactly this reason).
                try:
                    pd = h.player_data_ptr(port)
                except Exception:
                    continue
                if pd == -1:
                    continue
                pds[port] = pd
                s = oh.sample(h, pd)
                if s is None:
                    continue
                if s["hitlag"] != 0.0 and active[port] is None:
                    active[port] = []
                    events[port] += 1
                    print(f"[asdi] P{port} {names[port]} HIT #{events[port]}", flush=True)
                if active[port] is not None:
                    seen = active[port]
                    if not seen or seen[-1] != fr:
                        seen.append(fr)
                        print(oh.row(fr, s), flush=True)
                    if (s["hitlag"] == 0.0 and s["hitstun"] == 0.0
                            and s["air"] == 0 and len(seen) >= 3) or len(seen) > 90:
                        print(f"[asdi] P{port} {names[port]} done "
                              f"({len(seen)} frames)\n", flush=True)
                        active[port] = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[asdi] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
