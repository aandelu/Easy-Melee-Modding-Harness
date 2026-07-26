"""asdi_sdi_offline.py -- OFFLINE: measure how to SDI *downward* most efficiently.

SDI displaces the victim by (stick vector * 6 units) on hitlag frames where the
stick direction CHANGED -- holding one direction gives one tick, so the macro
must alternate.  Run-1 findings already banked (2026-07-25):

  * RADIAL CLAMP CONFIRMED: raw (+80,-80) -- impossible on a real stick -- is
    normalized to (0.707,-0.707): diagonal ticks are 4.2 units, never 6.
    Out-of-bounds coordinates buy nothing.
  * FLOOR EATS VERTICAL SDI: a victim at ground level shows dx ticks with
    dy == 0 -- SDI-down can't push through the floor (and those hits teched).
    So measuring dy needs the victim WELL above ground.

Open questions for this run (one pattern per victim-hit, rotating):

  A  hold pure down        control: the no-repeat rule (expect 1 entry tick)
  B  down <-> neutral      threshold re-entry: full -6.0 vertical every other
                           frame, zero drift -- candidate best pure-down
  C  downL <-> downR       90 degree flip: a tick EVERY frame (seen in run 1),
                           -4.2 vertical each, horizontal cancels -- candidate
  D  down <-> down-away    45 degree: run 1 hinted only the diagonal re-ticks
                           (every other frame) -- resolve the asymmetry

Run-1 probe bugs fixed here: per-PORT alternation toggle (global toggle
double-flipped when attacker+victim froze together); pattern pinned at hit
start and rotated only when no hit window is open; victimhood decided by
observed Damage states (0x4E..0x5B), not the global write counter; per-frame
deltas only between ADJACENT frames (kills fake ticks from multi-hit gaps).

Builds on asdi_tech_offline (same hook, same phase-1 pad-ring discovery, same
v1.5 victim gate); the full ASDI + tech stack stays live underneath.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_sdi_offline.py [seconds]
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_sdi_offline.py --dry
"""
import sys
import time
from collections import Counter

import gecko_tools as gt
import observe_hitlag as oh
import observer
import asdi_tech_offline as base
from asdi_tech_offline import (HOOK_ADDR, CAVE, COUNTERS, CSTICK_Y_DOWN,
                               R_DIGITAL, PCT_TOPUP_BELOW, PCT_TOPUP_TO,
                               TECH_STATES, MISS_STATES, CLEAN_STATES,
                               _bump, install, run_pairlog)

# 0=x_a 1=y_a 2=x_b 3=y_b 4=toggle_port0 5=toggle_port1 (COUNTERS end 0xAA28)
PARAMS = 0x803FAA30

CNAMES = ["reached", "owner", "chain", "hitlag", "hitstun", "air", "dmgst",
          "sdi", "fired", "tech"]

oh.OFF["x"] = (0xB0, "f")

# (name, x_a, y_a, x_b, y_b) as s8 pad-stick bytes; engine maps +-80 -> +-1.0
#
# ROUND-2 SET. Round 1 (2026-07-25, 28 hits) settled the trigger rule:
#   * hold = ONE tick (A); down<->neutral re-arms every OTHER frame, -3.0/f (B);
#     X sign-flip re-arms EVERY frame with no neutral frame between (C, 6/6 hits);
#     diagonal->cardinal return NEVER re-arms (D refuted: -2.1/f + one-sided drift).
#   * WHOLE-VECTOR ARMING: a tick armed by the fresh X applies the full
#     (x,y)*6 including the STALE held Y (C hit #13: dy -4.2 every frame).
#   * Vertical SDI is EATEN in some at/below-floor-plane geometries while dx
#     still applies (#8, #22 early frames) -- not characterized; the shipped
#     near-ground gate constrains exposure anyway.
# So: hold Y deep, flip a small X. This round finds how small the X swing can
# be and still re-arm (X cardinal boundary is presumably the 0.2875 deadzone).
PATTERNS = [
    ("C downL<->downR (control, -4.2/f)", -80, -80,  80, -80),
    ("E steep-V x=+-0.35 (-5.6/f?)",      -28, -75,  28, -75),
    ("F steeper-V x=+-0.30 (-5.7/f?)",    -24, -76,  24, -76),
    ("G V x=+-0.25 (below deadzone?)",    -20, -77,  20, -77),
]

DMG_LO, DMG_HI = 0x4E, 0x5B


def sdi_src(ring_base, ring_limit):
    """The tech cave (gates verbatim from asdi_tech_offline.tech_src) plus an
    SDI stick write on every gated hitlag frame, alternating (x,y) via a
    PER-PORT toggle byte -- self-clocked per gated call for that port, immune
    to attacker+victim freezing on the same frames."""
    return f"""
    lis    11, 0x{COUNTERS >> 16:X}
    ori    11, 11, 0x{COUNTERS & 0xFFFF:X}
{_bump(0)}
    cmplwi 24, 1
    bgt    done
    mulli  9, 24, 0xC
    lis    12, 0x{ring_base >> 16:X}
    ori    12, 12, 0x{ring_base & 0xFFFF:X}
    add    12, 12, 9
    subf   9, 12, 25
    cmplwi 9, 0x{ring_limit:X}
    bge    done
    andi.  9, 9, 0xF
    bne    done
{_bump(1)}
    mulli  9, 24, 0xE90
    lis    12, 0x8045
    ori    12, 12, 0x3130
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
{_bump(2)}
    lwz    0, 0x195C(12)           /* hitlag != 0 */
    cmpwi  0, 0
    beq    done
{_bump(3)}
    lwz    0, 0x2340(12)           /* hitstun != 0 (belt+suspenders) */
    cmpwi  0, 0
    beq    done
{_bump(4)}
    lwz    0, 0xE0(12)             /* airborne */
    cmpwi  0, 1
    bne    done
{_bump(5)}
    lwz    9, 0x10(12)             /* Damage state 0x4E..0x5B -- the victim check */
    addi   9, 9, -0x4E
    cmplwi 9, 0xD
    bgt    done
{_bump(6)}
    /* SDI: alternate main stick between (x_a,y_a) and (x_b,y_b). Toggle is
       PER PORT (PARAMS+4+r24), flipping once per gated call = once per hitlag
       frame for THIS victim. Python pokes the params between hits. */
    lis    9, 0x{PARAMS >> 16:X}
    ori    9, 9, 0x{PARAMS & 0xFFFF:X}
    add    9, 9, 24
    lbz    0, 4(9)
    xori   0, 0, 1
    stb    0, 4(9)
    subf   9, 24, 9
    cmpwi  0, 0
    bne    sdib
    lbz    0, 0(9)                 /* x_a */
    stb    0, 2(25)
    lbz    0, 1(9)                 /* y_a */
    stb    0, 3(25)
    b      sdix
sdib:
    lbz    0, 2(9)                 /* x_b */
    stb    0, 2(25)
    lbz    0, 3(9)                 /* y_b */
    stb    0, 3(25)
sdix:
{_bump(7)}
    li     0, {CSTICK_Y_DOWN}      /* ASDI down, every gated hitlag frame */
    stb    0, 5(25)
{_bump(8)}
    lwz    0, 0x195C(12)           /* tech press: last hitlag frame only */
    lis    9, 0x4000
    cmplw  0, 9
    bge    done
    lhz    9, 0(25)
    ori    9, 9, {R_DIGITAL}
    sth    9, 0(25)
{_bump(9)}
done:
"""


def build_sdi(pad0, pad1):
    # must survive: stick X/Y stores, ASDI c-stick store, tech buttons store
    return base.build(sdi_src(pad0, pad1),
                      want_words=[0x98190002, 0x98190003, 0x98190005, 0xB1390000])


def poke_pattern(h, idx):
    idx %= len(PATTERNS)
    name, xa, ya, xb, yb = PATTERNS[idx]
    word = ((xa & 0xFF) << 24) | ((ya & 0xFF) << 16) | ((xb & 0xFF) << 8) | (yb & 0xFF)
    h.write_words(PARAMS, [word, 0])   # second word zeroes both port toggles
    print(f"[sdi] NEXT HIT uses {name}", flush=True)
    return idx


def main():
    if "--dry" in sys.argv:
        payload = build_sdi(0x8046B108, 0xF0)
        print(f"[dry] sdi: {len(payload)} words")
        for line in gt.disasm(payload, addr=CAVE):
            print(f"    {line}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 420.0
    build_sdi(0x8046B108, 0xF0)            # fail on asm errors before launching

    h = observer.bring_up()

    count, pbase, plimit = run_pairlog(h)
    if count == 0:
        print("[sdi] ABORT: hook never fired -- is the match live?")
        return 1
    if pbase is None:
        print("[sdi] ABORT: pad ring structure not clean; study the dump above.")
        return 1
    print(f"[sdi] pad ring: base 0x{pbase:08X}, extent 0x{plimit:X}")

    h.write_words(COUNTERS, [0] * len(CNAMES))
    pat = poke_pattern(h, 0)
    install(h, build_sdi(pbase, plimit))
    print(f"[sdi] hook patched: 0x{h.read_word(HOOK_ADDR):08X}", flush=True)

    ports = observer.players(h)
    names = {p: oh.CHARS.get(h.char_id(p), "?") for p in ports}
    fox = next((p for p in ports if h.char_id(p) == 0x01), None)
    print("[sdi] tracking " + "  ".join(f"P{p}={names[p]}" for p in sorted(ports)))
    print("=" * 72)
    print("SDI + ASDI + TECH LIVE. Fox must be AIRBORNE when the hit lands, and")
    print("for dy to be measurable he must be WELL ABOVE the ground -- the floor")
    print("eats vertical SDI at ground level. Juggle him HIGH (up-throw -> up-air,")
    print("rising aerials), strong moves for long hitlag. ~16 gated hits wanted.")
    print("=" * 72, flush=True)

    last_ctr = [0] * len(CNAMES)
    active, events, states, series = {}, {}, {}, {}
    patat, lastsamp, ctr_at_start = {}, {}, {}
    pending = False
    results = []
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            ctrs = gt.read_counters(h, COUNTERS, CNAMES)
            c = list(ctrs.values())
            if c != last_ctr:
                print("[gate] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, c)),
                      flush=True)
                last_ctr = c
            try:
                fr = h.read_word(oh.FRAME)
                cur = observer.players(h)
            except Exception:
                oh.ensure_hooked()
                time.sleep(0.05)
                continue
            if pending and all(active.get(p) is None for p in cur):
                pat = poke_pattern(h, pat + 1)
                pending = False
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
                    # seed with the last pre-hit sample so the entry tick shows
                    series[port] = [lastsamp[port]] if port in lastsamp else []
                    patat[port] = pat
                    ctr_at_start[port] = ctrs["sdi"]
                    events[port] = events.get(port, 0) + 1
                    print(f"[sdi] P{port} {names.get(port, '?')} HIT "
                          f"#{events[port]} ({PATTERNS[pat][0]})", flush=True)
                if active.get(port) is None:
                    lastsamp[port] = (fr, s["x"], s["y"], s["air"])
                else:
                    seen = active[port]
                    if not seen or seen[-1] != fr:
                        seen.append(fr)
                        states[port].append(s["state"])
                        if s["hitlag"] != 0.0:
                            series[port].append((fr, s["x"], s["y"], s["air"]))
                        print(oh.row(fr, s) + f"  x {s['x']:8.2f}", flush=True)
                    if (s["hitlag"] == 0.0 and s["hitstun"] == 0.0 and s["air"] == 0
                            and len(seen) >= 3) or len(seen) > 90:
                        st = states[port]
                        victim = any(DMG_LO <= x <= DMG_HI for x in st)
                        verdict = next(
                            (f"TECH: {TECH_STATES[x]}" for x in st if x in TECH_STATES),
                            next((f"missed tech: {MISS_STATES[x]}" for x in st
                                  if x in MISS_STATES),
                                 next((f"clean: {CLEAN_STATES[x]}" for x in st
                                       if x in CLEAN_STATES), "escaped / other")))
                        hl = series[port]
                        # adjacent-frame deltas only: multi-hit gaps excluded
                        adj = [(a, b) for a, b in zip(hl, hl[1:]) if b[0] - a[0] == 1]
                        dys = [round(b[2] - a[2], 2) for a, b in adj]
                        dxs = [round(b[1] - a[1], 2) for a, b in adj]
                        floored = any(a[3] == 1 and b[3] == 0 for a, b in adj)
                        writes = ctrs["sdi"] - ctr_at_start.get(port, ctrs["sdi"])
                        p0 = patat.get(port, pat)
                        if victim:
                            results.append(dict(pat=p0, port=port, n=events[port],
                                                hlf=len(hl), writes=writes,
                                                dys=dys, dxs=dxs, floored=floored,
                                                verdict=verdict))
                            pending = True
                            print(f"[sdi] P{port} #{events[port]} "
                                  f"{PATTERNS[p0][0]}: hitlag {len(hl)}f, "
                                  f"writes(global) {writes}, "
                                  f"dy {dys} (total {sum(dys):+.1f}), "
                                  f"dx {dxs} (total {sum(dxs):+.1f})"
                                  f"{', FLOORED mid-hitlag' if floored else ''}"
                                  f" -> {verdict}\n", flush=True)
                        else:
                            print(f"[sdi] P{port} #{events[port]} attacker-freeze "
                                  f"(not a victim) -> ignored\n", flush=True)
                        active[port] = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[sdi] stopped")

    print("\n[sdi] ===== SUMMARY =====")
    try:
        c = gt.read_counters(h, COUNTERS, CNAMES)
        print("[sdi] " + "  ".join(f"{n}={v}" for n, v in c.items()))
    except Exception:
        pass
    for i, (pname, *_ignored) in enumerate(PATTERNS):
        rs = [r for r in results if r["pat"] == i]
        if not rs:
            print(f"[sdi] {pname}: no victim hits")
            continue
        ticks = Counter()
        for r in rs:
            for dy, dx in zip(r["dys"], r["dxs"]):
                if abs(dy) > 0.5 or abs(dx) > 0.5:
                    ticks[(dx, dy)] += 1
        tot = sum(sum(r["dys"]) for r in rs)
        frames = sum(len(r["dys"]) for r in rs) or 1
        print(f"[sdi] {pname}: {len(rs)} hits, dy/frame {tot / frames:+.2f}, "
              f"ticks(dx,dy) {dict(sorted(ticks.items()))}, "
              f"floored {sum(r['floored'] for r in rs)}/{len(rs)}, "
              f"verdicts {[r['verdict'].split(':')[0] for r in rs]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
