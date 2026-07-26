"""asdi_tech_offline.py -- OFFLINE: ASDI floorhug + auto-TECH on the last hitlag frame.

Above tumble the validated ASDI floorhug ends in DownBoundU (the missed-tech
bounce; 11/23 online hits) -- this adds the missing tech: one digital-R press
(buttons bit 0x20) injected on the LAST hitlag frame, so the 20-frame tech
window (PlCo 0xA230) is open when the floorhug collision lands on the frame
after hitlag. Presses on earlier hitlag frames don't register; presses after
hitlag can't be frame-guaranteed from Python -- the last hitlag frame is the
one frame that is both reachable in-cave and inside the window.

Phase 1 (autonomous, ~4s): measure the offline hook's r24/r25 pairing before
trusting it -- REFERENCE 2.2 records 61/137 gated injections landing on the
attacker's pad. A held c-stick shrugged that off; a ONE-FRAME press cannot.
A ring buffer logs (r24, r25, LR, buttons|err) per call; phase 2 then gates on
the PAD OWNER (r25 address compare), which also drops any rogue call path.

Phase 2 (user plays): ASDI c-stick down on every gated hitlag frame + digital R
on the last one. Fox is topped up to 70% whenever he drops below 50 so
tumble-range hits don't need a long ramp after each KO.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_tech_offline.py [seconds]
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_tech_offline.py --dry   # assemble only
"""
import struct
import sys
import time

import gecko_tools as gt
import instr_writer as iw
import observe_hitlag as oh
import observer
from melee_harness import Harness, finalize_payload

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000        # lhz r0, 0(r25)
CAVE = 0x803FA600                  # ends < 0x803FAA00 (asserted after assembly)
COUNTERS = 0x803FAA00
RING = 0x803FAA40                  # [0]=count, entries at +8: 16 x (r24,r25,lr,pad)

CSTICK_Y_DOWN = 0x90               # signed -112 = full down
R_DIGITAL = 0x20                   # buttons bit (REFERENCE 2.3)
PCT_TOPUP_BELOW, PCT_TOPUP_TO = 50.0, 0x428C0000   # 70.0f

CNAMES = ["reached", "owner", "chain", "hitlag", "hitstun", "air", "dmgst",
          "fired", "tech"]

# extra per-frame observables on top of observe_hitlag's set
oh.OFF["lr_t"] = (0x680, "w")      # L/R press timer -- should snap low on our press
oh.OFF["pct"] = (0x1830, "f")      # percent (Char_Data_Offsets.csv)

TECH_STATES = {0xC7: "Passive (neutral tech)", 0xC8: "PassiveStandF (tech fwd)",
               0xC9: "PassiveStandB (tech back)", 0xCA: "PassiveWall"}
MISS_STATES = {0xB7: "DownBoundU", 0xB8: "DownWaitU", 0xBF: "DownBoundD",
               0xC0: "DownWaitD"}
CLEAN_STATES = {0x2A: "Landing", 0x2B: "LandingFallSpecial"}


# ---- phase 1: who owns r25? ------------------------------------------------

PAIRLOG_SRC = f"""
    /* No gate: log EVERY call. r0/r9/r11/r12 clobber-safe at this hook. */
    lis    11, 0x{RING >> 16:X}
    ori    11, 11, 0x{RING & 0xFFFF:X}
    lwz    9, 0(11)
    rlwinm 12, 9, 4, 24, 27        /* (count & 15) * 16 */
    add    12, 12, 11
    addi   9, 9, 1
    stw    9, 0(11)
    stw    24, 8(12)
    stw    25, 12(12)
    mflr   9
    stw    9, 16(12)
    li     9, -1                   /* marker: pad unreadable */
    srwi   0, 25, 24
    cmplwi 0, 0x80                 /* rogue-path r25 may be garbage -- no blind deref */
    bne    skipd
    lhz    9, 0(25)                /* buttons */
    rlwinm 9, 9, 16, 0, 15
    lbz    0, 0xA(25)              /* err/status byte */
    or     9, 9, 0
skipd:
    stw    9, 20(12)
"""


def _bump(i):
    """r11 = counter base, r9 = scratch (never r0: addi rA=0 is the literal 0)."""
    return f"""
    lwz    9, {i * 4}(11)
    addi   9, 9, 1
    stw    9, {i * 4}(11)
"""


def tech_src(ring_base, ring_limit):
    """ASDI + tech cave. Owner check is structural: the pad ring (measured live
    by phase 1) is entries of 4 x 0xC-byte pads every 0x30, so r24's own pad
    sits at ring_base + k*0x30 + r24*0xC. Verifying that r25 lands there
    confirms r24 == pad owner AND drops any rogue call path with junk regs."""
    return f"""
    lis    11, 0x{COUNTERS >> 16:X}
    ori    11, 11, 0x{COUNTERS & 0xFFFF:X}
{_bump(0)}
    cmplwi 24, 1                   /* offline: players only on ports 0/1 */
    bgt    done
    mulli  9, 24, 0xC
    lis    12, 0x{ring_base >> 16:X}
    ori    12, 12, 0x{ring_base & 0xFFFF:X}
    add    12, 12, 9
    subf   9, 12, 25               /* r25 - (ring_base + r24*0xC) */
    cmplwi 9, 0x{ring_limit:X}
    bge    done
    andi.  9, 9, 0xF               /* ring entries are 0x30 apart -> 16-aligned */
    bne    done
{_bump(1)}
    mulli  9, 24, 0xE90
    lis    12, 0x8045
    ori    12, 12, 0x3130          /* 0x80453130 = P1 GObj ptr */
    add    12, 12, 9               /* r9 free from here */
    lwz    12, 0(12)               /* GObj */
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80
    bne    done
    lwz    12, 0x2C(12)            /* Player Data */
    cmpwi  12, 0
    beq    done
    srwi   0, 12, 24
    cmplwi 0, 0x80
    bne    done
{_bump(2)}
    lwz    0, 0x195C(12)           /* hitlag (float; +0.0 is all-zero bits) */
    cmpwi  0, 0
    beq    done
{_bump(3)}
    lwz    0, 0x2340(12)           /* hitstun -- VICTIM, not the attacker */
    cmpwi  0, 0
    beq    done
{_bump(4)}
    lwz    0, 0xE0(12)             /* airborne */
    cmpwi  0, 1
    bne    done
{_bump(5)}
    /* Damage action state 0x4E..0x5B (DamageN1..DamageFlyRoll) -- the
       DEFINITIONAL victim check. hitstun != 0 is NOT one: the attacker's
       +0x2340 can hold a denormal-tiny value that prints as 0.00 but passes
       cmpwi != 0 (measured: both players fired +2/frame on the first hit of
       run 2, attacker c-stick went -1.00). That was the old "61/137 leak". */
    lwz    9, 0x10(12)
    addi   9, 9, -0x4E
    cmplwi 9, 0xD
    bgt    done
{_bump(6)}
    li     0, {CSTICK_Y_DOWN}      /* ASDI down, held every gated hitlag frame */
    stb    0, 5(25)
{_bump(7)}
    /* Tech: ONE digital-R press, last hitlag frame only. hitlag < 2.0 via raw
       unsigned compare (positive IEEE floats order as ints; != 0 gated above),
       so fractional hitlag endings still get exactly one press-edge.
       ponytail: multi-hit moves press once per hit; presses < 40f apart are
       ignored by the game's lockout -- drills may not tech. Revisit if seen. */
    lwz    0, 0x195C(12)
    lis    9, 0x4000               /* 2.0f */
    cmplw  0, 9
    bge    done
    lhz    9, 0(25)
    ori    9, 9, {R_DIGITAL}
    sth    9, 0(25)                /* displaced lhz r0,0(r25) reloads this after us */
{_bump(8)}
done:
"""


def build(src, want_words):
    logic = gt.assemble(src, addr=CAVE)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED_ORIG)
    assert CAVE + 4 * len(payload) <= COUNTERS, "cave overlaps counters"
    assert payload[-2] == DISPLACED_ORIG, "displaced original not protected"
    for w in want_words:
        assert w in payload, f"expected instruction 0x{w:08X} missing"
    return payload


def build_pairlog():
    return build(PAIRLOG_SRC, want_words=[])


def build_tech(pad0, pad1):
    # the two stores that must survive: stb r0,5(r25) (ASDI), sth r9,0(r25) (tech)
    return build(tech_src(pad0, pad1), want_words=[0x98190005, 0xB1390000])


def install(h, payload):
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)


def uninstall(h):
    iw.write_instrs(h, HOOK_ADDR, [DISPLACED_ORIG])
    time.sleep(0.2)                # let any in-flight cave execution drain


def run_pairlog(h, secs=4.0):
    """Install the ring logger, collect, return {r24: set(r25)} for valid calls."""
    h.write_words(RING, [0] * (2 + 16 * 4))
    install(h, build_pairlog())
    time.sleep(secs)
    uninstall(h)
    count = h.read_word(RING)
    entries = []
    for i in range(min(count, 16)):
        base = RING + 8 + i * 16
        entries.append(tuple(h.read_word(base + j * 4) for j in range(4)))
    print(f"[tech] pairlog: {count} calls, last {len(entries)} logged")
    from collections import Counter
    for (r24, r25, lr, packed), n in sorted(Counter(entries).items()):
        print(f"    r24={r24:<10} r25=0x{r25:08X}  lr=0x{lr:08X}  "
              f"buttons|err=0x{packed:08X}  x{n}")
    # Pad ring structure (measured 2026-07-25): entries of 4 x 0xC-byte pads
    # every 0x30; r24's own pad = ring_base + k*0x30 + r24*0xC. Derive base and
    # extent from the data; any entry that breaks the structure is a rogue call.
    valid = [(r24, r25) for r24, r25, _, _ in entries
             if r24 <= 3 and (r25 >> 24) == 0x80]
    rogue = len(entries) - len(valid)
    if rogue:
        print(f"[tech] {rogue} rogue calls (junk r24/r25) -- the structural "
              f"gate drops these in phase 2")
    if not valid:
        return count, None, None
    base = min(r25 - 0xC * r24 for r24, r25 in valid)
    deltas = sorted({r25 - 0xC * r24 - base for r24, r25 in valid})
    if any(d % 0x30 for d in deltas):
        print(f"[tech] ring structure broken: deltas {[hex(d) for d in deltas]}")
        return count, None, None
    limit = deltas[-1] + 0x30
    return count, base, limit


def main():
    if "--dry" in sys.argv:
        for name, payload in [("pairlog", build_pairlog()),
                              ("tech", build_tech(0x8046B108, 0xF0))]:
            print(f"[dry] {name}: {len(payload)} words")
            for line in gt.disasm(payload, addr=CAVE):
                print(f"    {line}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 300.0
    build_pairlog()                        # fail on asm errors before launching
    build_tech(0x8046B108, 0xF0)

    h = observer.bring_up()                # meta-flush staged; runtime iteration live

    # ---- phase 1 ----
    count, base, limit = run_pairlog(h)
    if count == 0:
        print("[tech] ABORT: hook never fired -- is the match live?")
        return 1
    if base is None:
        print("[tech] ABORT: pad ring structure not clean; study the dump above.")
        return 1
    print(f"[tech] pad ring: base 0x{base:08X}, extent 0x{limit:X} "
          f"({limit // 0x30} entries)")

    # ---- phase 2 ----
    h.write_words(COUNTERS, [0] * len(CNAMES))
    install(h, build_tech(base, limit))
    print(f"[tech] hook patched: 0x{h.read_word(HOOK_ADDR):08X}", flush=True)

    ports = observer.players(h)
    names = {p: oh.CHARS.get(h.char_id(p), "?") for p in ports}
    fox = next((p for p in ports if h.char_id(p) == 0x01), None)
    print("[tech] tracking " + "  ".join(f"P{p}={names[p]}" for p in sorted(ports)))
    print("=" * 72)
    print("ASDI + TECH LIVE -- hit Fox. He is held at 70%+ so hits reach tumble.")
    print("Money hits: f-tilt / f-throw / jab (tumble but kb_y <= ~3 so the hug")
    print("connects). Smashes will out-range the ASDI -- that's the TDI layer.")
    print("Watch for Passive/PassiveStand* instead of DownBoundU.")
    print("=" * 72, flush=True)

    last_ctr = [0] * len(CNAMES)
    active, events, states = {}, {}, {}
    outcomes = []
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            c = list(gt.read_counters(h, COUNTERS, CNAMES).values())
            if c != last_ctr:
                print("[gate] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, c)),
                      flush=True)
                last_ctr = c
            try:
                fr = h.read_word(oh.FRAME)
                cur = observer.players(h)      # re-resolve EVERY frame
            except Exception:
                oh.ensure_hooked()
                time.sleep(0.05)
                continue
            for port, pd in cur.items():
                s = oh.sample(h, pd)
                if s is None:
                    continue
                if (port == fox and s["hitlag"] == 0.0 and s["hitstun"] == 0.0
                        and (s["pct"] < PCT_TOPUP_BELOW or s["pct"] > 130.0)):
                    h.write_words(pd + 0x1830, [PCT_TOPUP_TO])
                if s["hitlag"] != 0.0 and active.get(port) is None:
                    active[port] = []
                    states[port] = []
                    events[port] = events.get(port, 0) + 1
                    print(f"[tech] P{port} {names.get(port, '?')} HIT #{events[port]}",
                          flush=True)
                if active.get(port) is not None:
                    seen = active[port]
                    if not seen or seen[-1] != fr:
                        seen.append(fr)
                        states[port].append(s["state"])
                        print(oh.row(fr, s) +
                              f"  hs[{s['hitstun_raw']:08X}]"
                              f"  lr_t 0x{s['lr_t_raw']:08X}  pct {s['pct']:5.1f}",
                              flush=True)
                    if (s["hitlag"] == 0.0 and s["hitstun"] == 0.0 and s["air"] == 0
                            and len(seen) >= 3) or len(seen) > 90:
                        st = states[port]
                        verdict = next(
                            (f"TECH: {TECH_STATES[x]}" for x in st if x in TECH_STATES),
                            next((f"missed tech: {MISS_STATES[x]}" for x in st
                                  if x in MISS_STATES),
                                 next((f"clean: {CLEAN_STATES[x]}" for x in st
                                       if x in CLEAN_STATES), "escaped / other")))
                        outcomes.append((port, events[port], verdict))
                        print(f"[tech] P{port} HIT #{events[port]} done "
                              f"({len(seen)} frames) -> {verdict}\n", flush=True)
                        active[port] = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[tech] stopped")

    print("\n[tech] ===== SUMMARY =====")
    try:
        c = gt.read_counters(h, COUNTERS, CNAMES)
        print("[tech] " + "  ".join(f"{n}={v}" for n, v in c.items()))
    except Exception:
        pass
    for port, num, verdict in outcomes:
        print(f"[tech]   P{port} #{num}: {verdict}")
    n_tech = sum("TECH" in v for _, _, v in outcomes)
    n_miss = sum("missed" in v for _, _, v in outcomes)
    print(f"[tech] {len(outcomes)} hits: {n_tech} teched, {n_miss} missed, "
          f"{sum('clean' in v for _, _, v in outcomes)} clean (below tumble), "
          f"{sum('escaped' in v for _, _, v in outcomes)} escaped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
