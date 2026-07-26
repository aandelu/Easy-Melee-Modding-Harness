"""asdi_tdi_offline.py -- OFFLINE: add the TDI (trajectory DI) layer on top of
ASDI + tech + SDI, and measure that it actually rotates the launch trajectory.

TDI is sampled from the ANALOG stick on the LAST hitlag frame -- the same frame
the tech press goes out, and the same frame the c-stick ASDI is read.  The
engine rotates the stored knockback vector by up to 18 degrees toward the
component of the stick perpendicular to it.  For a floorhug macro we always
want the DOWNWARD perpendicular: it trades vertical distance for horizontal on
every trajectory, which both reaches the ground sooner (tech) and survives
vertical launches.

So the layer is one rule with no quadrant special-cases:

    stick = perpendicular to (kb_x, kb_y), whichever of the two ends has y < 0

The analog stick is already owned by the SDI pattern during hitlag, so a second
threshold splits them: `hitlag < 3.0` writes the TDI direction, `>= 3.0` keeps
flipping the SDI V.  c-stick ASDI and the digital-R tech press are unchanged.

MEASURED, NOT ASSUMED: **DI is read on the `hitlag == 2` frame**, which is NOT
the `hitlag < 2.0` frame the tech press uses.  Writing TDI on the tech frame
(the obvious guess, and this file's first version) produces exactly zero
rotation -- what shows up instead is the SDI pattern doing incidental DI from
frame 2.  The threshold is a poked parameter (PARAMS+8) precisely so this was
swept rather than guessed; see REFERENCE 2.9 for the elimination argument.

ponytail: direction is quantized to 8 sectors using integer compares on the raw
float bits (positive IEEE floats order as ints), NOT computed with the FPU.
Worst-case 23 degrees off perpendicular = cos^2 = 0.85 of full DI (~15.3 of 18
degrees).  Buys ~16 instructions and zero FPR save/restore inside a hook whose
live FPRs are unknown.  Upgrade path if the measured rotation is short: fabs +
fdivs to saturate the true perpendicular (needs f0-f3 saved to scratch).

Per victim hit the probe A/Bs TDI on/off and reports the trajectory rotation
measured from the engine's own knockback vector (first hitlag frame = pre-DI,
first frame after hitlag = post-DI).  Expect ~0 with TDI off, ~-15 degrees of
elevation with it on.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_tdi_offline.py [seconds]
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_tdi_offline.py --dry
  python3 asdi_tdi_offline.py --selfcheck     # direction table, no Dolphin
"""
import math
import struct
import sys
import time

import gecko_tools as gt
import observe_hitlag as oh
import observer
import asdi_tech_offline as base
from asdi_tech_offline import (HOOK_ADDR, CAVE, COUNTERS, CSTICK_Y_DOWN,
                               R_DIGITAL, PCT_TOPUP_BELOW, PCT_TOPUP_TO,
                               TECH_STATES, MISS_STATES, CLEAN_STATES,
                               _bump, install, run_pairlog)

# 0=x_a 1=y_a 2=x_b 3=y_b 4=toggle_port0 5=toggle_port1 6=tdi_enable
PARAMS = 0x803FAA30

CNAMES = ["reached", "owner", "chain", "hitlag", "hitstun", "air", "dmgst",
          "sdi", "tdi", "tech"]

oh.OFF["x"] = (0xB0, "f")

# SDI pattern pinned to the round-2 winner: raw (+-24,-76) = (+-0.30,-0.95),
# -5.7 units/frame (REFERENCE 2.8).  Only TDI is under test now.
SDI_PATTERN = (-24, -76, 24, -76)
# --isolate: SDI writes a NEUTRAL stick instead, so the only stick input all
# hitlag is the TDI write.  Run 1 showed the SDI pattern is itself what the
# engine was reading for DI (every rotation matched the SDI stick to 0.1 deg),
# which made the TDI A/B arms indistinguishable -- isolating is the only way to
# attribute a rotation to TDI.
SDI_NEUTRAL = (0, 0, 0, 0)

DMG_LO, DMG_HI = 0x4E, 0x5B

# Sector representatives: |x|>|y| -> kb is within 22.5 deg of horizontal, so the
# downward perpendicular is steep (31,-74); otherwise it is shallow (74,-31).
# The x SIGN flips when kb_x and kb_y have opposite signs -- that is the whole
# quadrant dependence, because the perpendicular LINE is the same for kb and -kb.
TDI_STEEP = (31, -74)      # ~ -67.3 deg
TDI_SHALLOW = (74, -31)    # ~ -22.7 deg


def cave_tdi(kb_x, kb_y):
    """Exactly what the cave computes, in Python -- kept honest by --selfcheck."""
    if kb_x == 0.0 and kb_y == 0.0:
        return None                                    # no knockback: no write
    d = (math.copysign(1.0, kb_x) < 0) != (math.copysign(1.0, kb_y) < 0)
    x, y = TDI_STEEP if abs(kb_x) > abs(kb_y) else TDI_SHALLOW
    return (-x if d else x), y


def selfcheck():
    """The quantizer must always point DOWN and stay within one sector-half of
    the true downward perpendicular. Fails loudly if the table is transposed,
    mis-signed, or the steep/shallow cases are swapped."""
    worst = 0.0
    for deg in range(0, 360):
        th = math.radians(deg + 0.5)               # off-boundary, avoids ties
        kb_x, kb_y = math.cos(th) * 7.0, math.sin(th) * 7.0
        sx, sy = cave_tdi(kb_x, kb_y)
        assert sy < 0, f"kb {deg}deg -> stick y {sy} is not downward"
        ideal = th - math.pi / 2
        if math.sin(ideal) > 0:
            ideal = th + math.pi / 2
        err = abs((math.atan2(sy, sx) - ideal + math.pi) % (2 * math.pi) - math.pi)
        worst = max(worst, math.degrees(err))
    assert worst < 23.0, f"worst perpendicular error {worst:.1f} deg"
    assert cave_tdi(0.0, 0.0) is None
    print(f"[tdi] selfcheck OK: worst {worst:.1f} deg off perpendicular "
          f"= {math.cos(math.radians(worst)) ** 2 * 18:.1f} of 18 deg of DI")
    return 0


def tdi_src(ring_base, ring_limit):
    """v1.5 gate (verbatim from asdi_tech_offline) + three stick writes:
    c-stick ASDI every gated frame, SDI flip on frames >= 2, TDI on the last.

    Register discipline in the TDI block: r11 is the counter base and must
    survive, so it uses r0/r9/r12 only -- r12 is free once both knockback
    words are loaded out of Player Data."""
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
    li     0, {CSTICK_Y_DOWN}      /* ASDI down: EVERY gated hitlag frame */
    stb    0, 5(25)
    /* TDI window: hitlag < PARAMS+8 (float bits, poked from Python). DI is a
       ONE-frame read and it is NOT the hitlag<2.0 frame the tech press uses --
       measured 2026-07-25, see the run notes -- so the window is swept, not
       assumed. Raw unsigned compare: positive floats order as ints. */
    lis    9, 0x{PARAMS >> 16:X}
    ori    9, 9, 0x{PARAMS & 0xFFFF:X}
    lwz    9, 8(9)
    lwz    0, 0x195C(12)
    cmplw  0, 9
    blt    lastf

    /* ---- frames >= 2: SDI, alternating via the PER-PORT toggle byte ---- */
    lis    9, 0x{PARAMS >> 16:X}
    ori    9, 9, 0x{PARAMS & 0xFFFF:X}
    add    9, 9, 24
    lbz    0, 4(9)
    xori   0, 0, 1
    stb    0, 4(9)
    subf   9, 24, 9
    cmpwi  0, 0
    bne    sdib
    lbz    0, 0(9)
    stb    0, 2(25)
    lbz    0, 1(9)
    stb    0, 3(25)
    b      sdix
sdib:
    lbz    0, 2(9)
    stb    0, 2(25)
    lbz    0, 3(9)
    stb    0, 3(25)
sdix:
{_bump(7)}
    b      techchk

    /* ---- last hitlag frame: TDI (perpendicular-down) + the tech press ---- */
lastf:
    lis    9, 0x{PARAMS >> 16:X}
    ori    9, 9, 0x{PARAMS & 0xFFFF:X}
    lbz    0, 6(9)                 /* A/B switch: TDI on? */
    cmpwi  0, 0
    beq    tdiend
    lwz    9, 0x8C(12)             /* kb_x raw bits */
    lwz    12, 0x90(12)            /* kb_y raw bits (r12 free from here) */
    or     0, 9, 12
    rlwinm. 0, 0, 0, 1, 31         /* both +-0.0 -> no knockback, leave stick */
    beq    tdiend
    xor    0, 9, 12
    srwi   0, 0, 31                /* r0 = 1 when the signs differ */
    rlwinm 9, 9, 0, 1, 31          /* |kb_x| -- positive floats order as ints */
    rlwinm 12, 12, 0, 1, 31        /* |kb_y| */
    cmplw  9, 12
    bgt    tdisteep
    li     9, {TDI_SHALLOW[0]}
    li     12, {TDI_SHALLOW[1]}
    b      tdisign
tdisteep:
    li     9, {TDI_STEEP[0]}
    li     12, {TDI_STEEP[1]}
tdisign:
    cmpwi  0, 0
    beq    tdiwr
    neg    9, 9
tdiwr:
    stb    9, 2(25)
    stb    12, 3(25)
{_bump(8)}
tdiend:
techchk:
    /* tech: ONE digital-R press, still the hitlag < 2.0 frame (20-frame
       window, so it does not care that DI reads a different frame) */
    lwz    0, 0x195C(12)
    lis    9, 0x4000
    cmplw  0, 9
    bge    done
    lhz    9, 0(25)
    ori    9, 9, {R_DIGITAL}
    sth    9, 0(25)
{_bump(9)}
done:
"""


def build_tdi(pad0, pad1):
    # every store that must survive keystone->capstone: SDI x/y, TDI x/y,
    # ASDI c-stick, tech buttons
    return base.build(tdi_src(pad0, pad1),
                      want_words=[0x98190002, 0x98190003,     # stb r0,2/3(r25)
                                  0x99390002, 0x99990003,     # stb r9,2 / r12,3
                                  0x98190005, 0xB1390000])


def poke(h, tdi_on, pattern, window):
    xa, ya, xb, yb = pattern
    word = ((xa & 0xFF) << 24) | ((ya & 0xFF) << 16) | ((xb & 0xFF) << 8) | (yb & 0xFF)
    thr = struct.unpack(">I", struct.pack(">f", window))[0]
    h.write_words(PARAMS, [word, (1 if tdi_on else 0) << 8, thr])
    print(f"[tdi] NEXT HIT: TDI {'ON' if tdi_on else 'OFF'}", flush=True)
    return tdi_on


def elevation(kx, ky):
    """Angle above horizontal, sign-independent in x -- the quantity DI-down is
    supposed to reduce, and well-behaved for near-vertical launches."""
    return math.degrees(math.atan2(ky, abs(kx)))


def rotation(pre, post):
    """True signed rotation of the trajectory, wrapped to (-180,180]. Unlike an
    elevation delta this survives kb_x crossing zero, which is exactly what
    near-vertical launches do."""
    d = math.atan2(post[1], post[0]) - math.atan2(pre[1], pre[0])
    return math.degrees((d + math.pi) % (2 * math.pi) - math.pi)


def predict_sdi_di(traj_deg, sx, sy):
    """What the Melee DI formula gives for a stick (sx,sy) at trajectory
    traj_deg: 18 deg * (perpendicular component)^2. Run 1 matched this to 0.1
    deg for the SDI stick on every clean hit, which is how the SDI pattern was
    identified as the thing actually being sampled."""
    mag = min(1.0, math.hypot(sx, sy) / 80.0)
    diff = math.radians(math.degrees(math.atan2(sy, sx)) - traj_deg)
    return 18.0 * (math.sin(diff) * mag) ** 2


def main():
    if "--selfcheck" in sys.argv:
        return selfcheck()
    selfcheck()                                  # cheap; never ship a bad table

    if "--dry" in sys.argv:
        payload = build_tdi(0x8046B108, 0xF0)
        print(f"[dry] tdi: {len(payload)} words")
        for line in gt.disasm(payload, addr=CAVE):
            print(f"    {line}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 420.0
    isolate = "--isolate" in sys.argv
    pattern = SDI_NEUTRAL if isolate else SDI_PATTERN
    # 3.0 = the measured minimum: covers the hitlag==2 read frame and gives
    # back only one SDI frame (4.0 also works, 2.0 does not -- see REFERENCE 2.9)
    window = next((float(a.split("=")[1]) for a in sys.argv
                   if a.startswith("--window=")), 3.0)
    build_tdi(0x8046B108, 0xF0)                  # fail on asm errors pre-launch

    h = observer.bring_up()

    count, pbase, plimit = run_pairlog(h)
    if count == 0:
        print("[tdi] ABORT: hook never fired -- is the match live?")
        return 1
    if pbase is None:
        print("[tdi] ABORT: pad ring structure not clean; study the dump above.")
        return 1
    print(f"[tdi] pad ring: base 0x{pbase:08X}, extent 0x{plimit:X}")

    h.write_words(COUNTERS, [0] * len(CNAMES))
    tdi_on = poke(h, True, pattern, window)
    install(h, build_tdi(pbase, plimit))
    print(f"[tdi] hook patched: 0x{h.read_word(HOOK_ADDR):08X}", flush=True)

    ports = observer.players(h)
    names = {p: oh.CHARS.get(h.char_id(p), "?") for p in ports}
    fox = next((p for p in ports if h.char_id(p) == 0x01), None)
    print("[tdi] tracking " + "  ".join(f"P{p}={names[p]}" for p in sorted(ports)))
    print("=" * 72)
    print("TDI + SDI + ASDI + TECH LIVE. TDI alternates ON/OFF every victim hit.")
    print("Hit Fox AIRBORNE with a VARIETY of angles -- low/horizontal (f-tilt,")
    print("f-smash, b-air) and steep/vertical (up-air, up-smash, up-throw follow-")
    print("ups). Strong hits especially: the target is the kb_y >= 4.2 launches the")
    print("current stack cannot save. ~16 gated hits wanted, 8 per arm.")
    print("=" * 72, flush=True)

    last_ctr = [0] * len(CNAMES)
    active, events, states = {}, {}, {}
    kb_pre, kb_post, armat = {}, {}, {}
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
                tdi_on = poke(h, not tdi_on, pattern, window)
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
                    kb_pre[port] = (s["kb_x"], s["kb_y"])     # pre-DI trajectory
                    armat[port] = tdi_on
                    events[port] = events.get(port, 0) + 1
                    print(f"[tdi] P{port} {names.get(port, '?')} HIT "
                          f"#{events[port]} (TDI {'ON' if tdi_on else 'OFF'}) "
                          f"kb ({s['kb_x']:+.2f},{s['kb_y']:+.2f}) "
                          f"elev {elevation(s['kb_x'], s['kb_y']):+.1f}", flush=True)
                if active.get(port) is not None:
                    seen = active[port]
                    if not seen or seen[-1] != fr:
                        seen.append(fr)
                        states[port].append(s["state"])
                        print(oh.row(fr, s), flush=True)
                        # post-DI trajectory: first frame after hitlag ran out.
                        # air==1 required -- a landing zeroes kb and would read
                        # as a huge bogus rotation (run 1: elev 44 -> 0).
                        if (s["hitlag"] == 0.0 and s["air"] == 1
                                and port not in kb_post):
                            kb_post[port] = (s["kb_x"], s["kb_y"])
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
                        if victim:
                            pre, post = kb_pre[port], kb_post.get(port)
                            # DI ROTATES the trajectory, it never changes its
                            # speed. A collision does the opposite: it zeroes or
                            # slashes a component. air==1 alone does not catch
                            # it -- the flag lags the floor contact by a frame
                            # (run 2 #5: ROT -44.0 on a DownBoundU). Requiring
                            # the magnitude to survive is the honest filter.
                            # kb_y == 0.0 exactly means the floor took it: a
                            # ground landing PROJECTS the velocity onto the
                            # ground plane, which preserves magnitude and so
                            # slips past the ratio test (run 3 #10, ROT -30.0
                            # on a DownBoundU). DI never lands exactly on 0.
                            ok = (post and any(post) and post[1] != 0.0
                                  and 0.85 < (math.hypot(*post)
                                              / (math.hypot(*pre) or 1)) < 1.15)
                            rot = rotation(pre, post) if ok else None
                            if not ok:
                                post = None
                            results.append(dict(on=armat[port], n=events[port],
                                                pre=pre, post=post, rot=rot,
                                                verdict=verdict))
                            pending = True
                            if post and rot is not None:
                                tj = math.degrees(math.atan2(pre[1], pre[0]))
                                want = cave_tdi(*pre)
                                # attribution: which stick does the measured
                                # rotation match? SDI's two phases bracket it.
                                sdi_p = [predict_sdi_di(tj, pattern[0], pattern[1]),
                                         predict_sdi_di(tj, pattern[2], pattern[3])]
                                traj = (f"traj {tj:+.1f} elev "
                                        f"{elevation(*pre):+.1f}->"
                                        f"{elevation(*post):+.1f}  ROT {rot:+.1f}"
                                        f"  [tdi {want}={predict_sdi_di(tj, *want):.1f}"
                                        f"  sdi={sdi_p[0]:.1f}/{sdi_p[1]:.1f}]")
                            else:
                                traj = "no airborne post-hitlag kb sample"
                            print(f"[tdi] P{port} #{events[port]} "
                                  f"TDI {'ON ' if armat[port] else 'OFF'}: "
                                  f"{traj} -> {verdict}\n", flush=True)
                        kb_post.pop(port, None)
                        active[port] = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[tdi] stopped")
    return summarize(h, results)


def summarize(h, results):
    print("\n[tdi] ===== SUMMARY =====")
    try:
        c = gt.read_counters(h, COUNTERS, CNAMES)
        print("[tdi] " + "  ".join(f"{n}={v}" for n, v in c.items()))
    except Exception:
        pass
    for on in (False, True):
        rs = [r for r in results if r["on"] == on]
        arm = "TDI ON " if on else "TDI OFF"
        if not rs:
            print(f"[tdi] {arm}: no victim hits")
            continue
        rots = [r["rot"] for r in rs if r.get("rot") is not None]
        teched = sum(1 for r in rs if r["verdict"].startswith("TECH"))
        rot = (f"rotation {sum(rots) / len(rots):+.1f} deg avg "
               f"{[round(x, 1) for x in rots]}") if rots else "no rotation data"
        print(f"[tdi] {arm}: {len(rs)} hits, teched {teched}/{len(rs)}, {rot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
