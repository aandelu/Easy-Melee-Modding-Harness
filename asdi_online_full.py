"""asdi_online_full.py -- ONLINE: the whole floorhug stack at the PRODUCER hooks.

asdi_online_test.py already proved the ASDI c-stick write survives PAD_Read and
reaches the peer (23 hits, no desync).  This adds the other three layers on the
netplay-safe side:

  hook A  0x8034E680  sticks   : c-stick down (ASDI) every gated frame,
                                 analog stick = SDI flip, or TDI when enabled
  hook B  0x8034E2AC  buttons  : one digital-R tech press on the last frame

Gate is v1.5 exactly as offline (ODB local player -> GObj -> Player Data,
hitlag != 0, hitstun != 0, air == 1, Damage state 0x4E..0x5B) but resolved
through the ODB, never an indexed port -- PAD_Read builds only the LOCAL pad, so
gate-player and pad-player are the same by construction.

THE ONLINE DIFFERENCE IS INPUT DELAY.  A pad byte written at the producer hook
on frame N is consumed by the engine on frame N+delay, so every frame-targeted
layer shifts by `delay` frames:

  * ASDI   samples the last hitlag frame -- we hold c-stick down the whole time,
           so it needs no compensation at all (this is why it already worked).
  * tech   wants the press on the last hitlag frame -> write while
           hitlag < 2.0 + delay.
  * TDI    is a ONE-frame read at engine hitlag == 2 (REFERENCE 2.9) -> write
           while hitlag < 3.0 + delay.
  * SDI    is cadence-only, so it just loses the frames TDI takes.  At delay 1 a
           4-frame hitlag leaves SDI a single frame, which is why TDI is OFF by
           default here: SDI's down-drag is the proven floorhug driver and TDI's
           benefit is not (docs/macros/asdi_floorhug.md, open item 1).

ponytail: both windows are POKED as float bits from Python, using the delay that
attach_observe_wavedash.resolve_pd already reads out of ODB+0x21 -- no float
arithmetic in the cave.  Delay is fixed for a match, so this is exact.  Ship
time it becomes a delay-indexed table of 8 floats + an `lwzx` (~4 instructions);
do that when it goes into the wavedash cave, not before.

STANDALONE ON PURPOSE: 0x8034E680 is also the wavedash gecko's stick hook and
0x8034E2AC its button hook.  Wavedash + auto-L-cancel are OFF for this run.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_online_full.py [seconds]
      [--tdi] [--no-sdi] [--tdi-window=N] [--tech-window=N]
  python3 asdi_online_full.py --dry        # assemble + disassemble, no Dolphin
"""
import math
import struct
import sys
import time

import attach_observe_wavedash as ao
import gecko_tools as gt
import instr_writer as iw
import observe_hitlag as oh
from asdi_tech_offline import TECH_STATES, MISS_STATES, CLEAN_STATES
from asdi_tdi_offline import TDI_STEEP, TDI_SHALLOW, SDI_PATTERN, SDI_NEUTRAL
from melee_harness import Harness, finalize_payload
from peer import Peer

HOOK_A, DISP_A, CAVE_A = 0x8034E680, 0x88030007, 0x803FA600   # sticks
# NOT the wavedash's 0x803FA800: cave A is 134 words and runs to 0x803FA818.
# All of this lives inside DEFAULT_CAVE (0x803FA3E8 + 0x1F04), REFERENCE 4.
HOOK_B, DISP_B, CAVE_B = 0x8034E2AC, 0x540084BE, 0x803FA840   # digital buttons
COUNTERS = 0x803FAA00                                          # 12 words
PARAMS = 0x803FAA80        # +0 sdi xa/ya/xb/yb, +6 tdi_on,
                           # +8 tdi window (float bits), +12 tech window

FRAME_TIMER = 0x80479D60   # SDI alternates on frame parity, NOT a stored toggle:
                           # REFERENCE 3.4 -- data flags in 0x803FAxxx are not
                           # reliably preserved across rollback, and the frame
                           # counter is engine state that rolls back correctly.

CSTICK_Y_DOWN = 0x90       # signed -112 = full down
R_BIT = 0x0020             # digital R, set in r0's HIGH half at 0x8034E2AC
DMG_LO, DMG_HI = 0x4E, 0x5B

CNAMES = ["reached", "chain", "hitlag", "hitstun", "air", "dmgst",
          "asdi", "tdi", "sdi", "b_reached", "b_chain", "tech"]


def _bump(i, scratch):
    """r11 is the counter base in both caves; `scratch` is a dead volatile."""
    return f"""
    lwz  {scratch}, {i * 4}(11)
    addi {scratch}, {scratch}, 1
    stw  {scratch}, {i * 4}(11)
"""


def _chain(pd, tmp, idx, out):
    """ODB -> local player index -> GObj -> Player Data, into `pd`.

    Every pointer is MEM1-checked (0x80xxxxxx): a garbage pointer dereferenced
    inside PAD_Read takes Dolphin down, not just the macro.
    """
    return f"""
    lwz  {pd}, -0x49E4(13)         /* ODB */
    cmpwi {pd}, 0
    beq  {out}
    srwi {tmp}, {pd}, 24
    cmplwi {tmp}, 0x80
    bne  {out}
    lbz  {idx}, 0({pd})            /* ODB_LOCAL_PLAYER_INDEX */
    cmplwi {idx}, 3
    bgt  {out}
    mulli {idx}, {idx}, 0xE90
    lis  {pd}, 0x8045
    ori  {pd}, {pd}, 0x3130        /* 0x80453130 = P1 GObj ptr */
    add  {pd}, {pd}, {idx}
    lwz  {pd}, 0({pd})
    cmpwi {pd}, 0
    beq  {out}
    srwi {tmp}, {pd}, 24
    cmplwi {tmp}, 0x80
    bne  {out}
    lwz  {pd}, 0x2C({pd})          /* GObj -> Player Data */
    cmpwi {pd}, 0
    beq  {out}
    srwi {tmp}, {pd}, 24
    cmplwi {tmp}, 0x80
    bne  {out}
"""


def _victim(pd, tmp, out):
    """hitlag != 0, hitstun != 0, airborne, Damage state -- the v1.5 gate tail.
    All three floats are compared as raw ints: +0.0 is all-zero bits, no FPU."""
    return f"""
    lwz  {tmp}, 0x195C({pd})       /* hitlag */
    cmpwi {tmp}, 0
    beq  {out}
    lwz  {tmp}, 0x2340({pd})       /* hitstun -- victim, not attacker */
    cmpwi {tmp}, 0
    beq  {out}
    lwz  {tmp}, 0xE0({pd})         /* airborne */
    cmpwi {tmp}, 1
    bne  {out}
    lwz  {tmp}, 0x10({pd})         /* Damage state 0x4E..0x5B */
    addi {tmp}, {tmp}, -0x4E
    cmplwi {tmp}, 0xD
    bgt  {out}
"""


# ---- hook A: sticks (r3 calib ptr, r4 PADStatus, r13 all preserved) --------
SRC_A = f"""
    stwu 1, -0x40(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)
    stw  11, 0x20(1)
    stw  12, 0x24(1)

    lis  11, 0x{COUNTERS >> 16:X}
    ori  11, 11, 0x{COUNTERS & 0xFFFF:X}
{_bump(0, 6)}
{_chain(5, 10, 9, "adone")}
{_bump(1, 6)}
    lwz  7, 0x195C(5)
    cmpwi 7, 0
    beq  adone
{_bump(2, 6)}
    lwz  7, 0x2340(5)
    cmpwi 7, 0
    beq  adone
{_bump(3, 6)}
    lwz  7, 0xE0(5)
    cmpwi 7, 1
    bne  adone
{_bump(4, 6)}
    lwz  7, 0x10(5)
    addi 7, 7, -0x4E
    cmplwi 7, 0xD
    bgt  adone
{_bump(5, 6)}
    /* ASDI: hold c-stick down for the whole of hitlag. No frame targeting, */
    /* so this layer needs no delay compensation -- which is why it already   */
    /* worked online before any of the rest of this existed.                  */
    li   8, {CSTICK_Y_DOWN}
    stb  8, 5(4)
{_bump(6, 6)}

    lis  10, 0x{PARAMS >> 16:X}
    ori  10, 10, 0x{PARAMS & 0xFFFF:X}
    lbz  7, 6(10)                  /* TDI enabled? */
    cmpwi 7, 0
    beq  dosdi
    lwz  7, 8(10)                  /* TDI window (float bits, delay-compensated) */
    lwz  8, 0x195C(5)
    cmplw 8, 7                     /* raw unsigned: positive floats order as ints */
    bge  dosdi

    /* TDI: the downward perpendicular to (kb_x, kb_y), quantized to 8 sectors */
    /* with integer compares on the raw float bits (REFERENCE 2.9).           */
    lwz  9, 0x8C(5)                /* kb_x raw bits */
    lwz  12, 0x90(5)               /* kb_y raw bits */
    or   7, 9, 12
    rlwinm. 7, 7, 0, 1, 31
    beq  dosdi                     /* no knockback -> nothing to rotate, keep SDI */
    xor  7, 9, 12
    srwi 7, 7, 31                  /* 1 when the signs differ */
    rlwinm 9, 9, 0, 1, 31          /* |kb_x| */
    rlwinm 12, 12, 0, 1, 31        /* |kb_y| */
    cmplw 9, 12
    bgt  tsteep
    li   9, {TDI_SHALLOW[0]}
    li   12, {TDI_SHALLOW[1]}
    b    tsign
tsteep:
    li   9, {TDI_STEEP[0]}
    li   12, {TDI_STEEP[1]}
tsign:
    cmpwi 7, 0
    beq  twr
    neg  9, 9
twr:
    stb  9, 2(4)
    stb  12, 3(4)
{_bump(7, 6)}
    b    adone

    /* ---- SDI: flip the V every gated frame via the toggle byte ---- */
dosdi:
    lis  7, 0x{FRAME_TIMER >> 16:X}
    ori  7, 7, 0x{FRAME_TIMER & 0xFFFF:X}
    lwz  7, 0(7)
    andi. 7, 7, 1
    bne  sdib
    lbz  8, 0(10)
    stb  8, 2(4)
    lbz  8, 1(10)
    stb  8, 3(4)
    b    sdix
sdib:
    lbz  8, 2(10)
    stb  8, 2(4)
    lbz  8, 3(10)
    stb  8, 3(4)
sdix:
{_bump(8, 6)}
adone:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    lwz  12, 0x24(1)
    addi 1, 1, 0x40
"""

# ---- hook B: digital R (r0 raw SI word -- deliberately edited; r4/r5/r13 kept) --
SRC_B = f"""
    stwu 1, -0x30(1)
    stw  6, 0x08(1)
    stw  7, 0x0C(1)
    stw  8, 0x10(1)
    stw  9, 0x14(1)
    stw  10, 0x18(1)
    stw  11, 0x1C(1)
    stw  12, 0x20(1)

    lis  11, 0x{COUNTERS >> 16:X}
    ori  11, 11, 0x{COUNTERS & 0xFFFF:X}
{_bump(9, 7)}
{_chain(6, 10, 9, "bdone")}
{_bump(10, 7)}
{_victim(6, 8, "bdone")}
    lwz  7, 0x195C(6)
    lis  9, 0x{PARAMS >> 16:X}
    ori  9, 9, 0x{PARAMS & 0xFFFF:X}
    lwz  9, 12(9)                  /* tech window (float bits, = 2.0 + delay) */
    cmplw 7, 9
    bge  bdone
    /* digital R; the displaced rlwinm folds the high half into the buttons */
    oris 0, 0, 0x{R_BIT:04X}
{_bump(11, 7)}
bdone:
    lwz  6, 0x08(1)
    lwz  7, 0x0C(1)
    lwz  8, 0x10(1)
    lwz  9, 0x14(1)
    lwz  10, 0x18(1)
    lwz  11, 0x1C(1)
    lwz  12, 0x20(1)
    addi 1, 1, 0x30
"""


def _w(mnemonic):
    """Ask the assembler for an encoding instead of hand-writing one -- the
    hand-written stb was wrong by a register field once already."""
    return gt.assemble(mnemonic)[0]


def build(src, hook, cave, disp, must):
    logic = gt.assemble(src, addr=cave)
    payload = finalize_payload(logic, hook, cave, disp)
    gt.disasm(payload, addr=cave)              # raises on any undecodable word
    for mn in must:
        assert _w(mn) in payload, f"{mn!r} did not survive keystone->capstone"
    assert payload[-2] == disp, "displaced original not protected"
    return payload


def build_a():
    return build(SRC_A, HOOK_A, CAVE_A, DISP_A,
                 ["stb 8, 5(4)",                    # ASDI c-stick
                  "stb 9, 2(4)", "stb 12, 3(4)",    # TDI stick
                  "stb 8, 2(4)", "stb 8, 3(4)"])    # SDI stick


def build_b():
    return build(SRC_B, HOOK_B, CAVE_B, DISP_B, [f"oris 0, 0, 0x{R_BIT:X}"])


def build_both():
    """Assemble both caves and prove the memory map doesn't self-collide.
    Cave A grew past 0x803FA800 once already -- silently, onto cave B."""
    a, b = build_a(), build_b()
    ends = [(CAVE_A, CAVE_A + len(a) * 4), (CAVE_B, CAVE_B + len(b) * 4),
            (COUNTERS, COUNTERS + len(CNAMES) * 4), (PARAMS, PARAMS + 16)]
    for i, (lo, hi) in enumerate(ends):
        for lo2, hi2 in ends[i + 1:]:
            assert hi <= lo2 or hi2 <= lo, (
                f"memory map collision: [{lo:08X},{hi:08X}) vs [{lo2:08X},{hi2:08X})")
    return a, b


def poke(h, pattern, tdi_on, tdi_w, tech_w):
    xa, ya, xb, yb = pattern
    sdi = ((xa & 0xFF) << 24) | ((ya & 0xFF) << 16) | ((xb & 0xFF) << 8) | (yb & 0xFF)
    f = lambda v: struct.unpack(">I", struct.pack(">f", v))[0]
    h.write_words(PARAMS, [sdi, (1 if tdi_on else 0) << 8, f(tdi_w), f(tech_w)])


def dolphin_alive():
    import subprocess
    return bool(subprocess.run(["pgrep", "-x", "Dolphin"],
                               capture_output=True, text=True).stdout.strip())


def bring_online(boots=4, attempts_per_boot=4):
    """Launch + enter online, relaunching if Dolphin dies (see asdi_online_test)."""
    import play_wavedash_offline as P
    for boot in range(1, boots + 1):
        P.kill_stale()
        h = Harness()
        print(f"[full] launching (boot {boot}/{boots}) ...", flush=True)
        try:
            h.launch()
            h.hook_dme()
            h._wait_for_cpu_alive(timeout_s=60.0)
        except Exception as e:
            # dme can attach in the window after exec but before Dolphin maps
            # MEM1 -- hook_dme then "succeeds" on attempt 1 and the first read
            # raises. Cheaper to reboot than to unpick which corpse we're on.
            print(f"[full] boot {boot} died before online entry ({e}) -- "
                  f"relaunching.", flush=True)
            continue
        print("[full] entering online (driving the Windows peer) ...", flush=True)
        if h.enter_online(peer=Peer(), max_attempts=attempts_per_boot):
            return h
        if not dolphin_alive():
            print("[full] Dolphin died (see dolphin.log) -- relaunching.", flush=True)
            continue
        print("[full] Dolphin alive but never reached a match -- relaunching.",
              flush=True)
    return None


def _retry(fn, n=6):
    """Each flush arms a magic word in 0x803FAxxx and waits 1s for the gecko to
    clear it -- and rollback can revert that write (REFERENCE 3.4). Re-arming is
    idempotent, so retry instead of losing the whole online session."""
    for i in range(n):
        try:
            return fn()
        except TimeoutError:
            if i == n - 1:
                raise
            print(f"[full] flush retry {i + 1}/{n - 1}", flush=True)


def rotation(pre, post):
    d = math.atan2(post[1], post[0]) - math.atan2(pre[1], pre[0])
    return math.degrees((d + math.pi) % (2 * math.pi) - math.pi)


def clean_rot(pre, post):
    """DI rotates in place and preserves speed; a floor collision does not.
    Both filters are needed -- air==1 lags contact by a frame, and a landing
    PROJECTS onto the ground plane, which preserves magnitude (REFERENCE 2.9)."""
    if not (post and any(post) and post[1] != 0.0):
        return None
    if not 0.85 < (math.hypot(*post) / (math.hypot(*pre) or 1)) < 1.15:
        return None
    return rotation(pre, post)


def verdict_of(states):
    return next((f"TECH: {TECH_STATES[x]}" for x in states if x in TECH_STATES),
                next((f"missed tech: {MISS_STATES[x]}" for x in states
                      if x in MISS_STATES),
                     next((f"clean: {CLEAN_STATES[x]}" for x in states
                           if x in CLEAN_STATES), "escaped / other")))


def main():
    if "--dry" in sys.argv:
        a, b = build_both()
        for nm, pay, cave in [("A sticks", a, CAVE_A), ("B buttons", b, CAVE_B)]:
            print(f"\n[dry] {nm}: {len(pay)} words @ 0x{cave:08X}")
            for line in gt.disasm(pay, addr=cave):
                print(f"    {line}")
        return 0

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 300.0
    tdi_on = "--tdi" in sys.argv
    pattern = SDI_NEUTRAL if "--no-sdi" in sys.argv else SDI_PATTERN
    ovr = lambda k: next((float(a.split("=")[1]) for a in sys.argv
                          if a.startswith(f"--{k}=")), None)
    pay_a, pay_b = build_both()                # fail on asm errors pre-launch

    h = bring_online()
    if h is None:
        print("[full] ABORT: never reached an online match.")
        return 1

    mf = h.read_word(iw.META_FLUSH_HOOK)
    if (mf >> 24) != 0x48:
        print(f"[full] ABORT: meta-flush hook reads 0x{mf:08X}, not a branch -- "
              f"it is not baked into slot 4.")
        return 1
    # The hook being a branch is NOT the same as the gecko being responsive: after
    # the F4 slot-4 load there is a window where it is patched but not yet firing,
    # and its control plane lives in 0x803FAxxx, which rollback does not reliably
    # preserve (REFERENCE 3.4). wait_for_meta_flush_alive re-arms every poll, so
    # a transient miss costs a poll instead of the whole run.
    try:
        iw.wait_for_meta_flush_alive(h, timeout_s=20.0)
    except TimeoutError as e:
        print(f"[full] ABORT: {e}")
        return 1
    for nm, hook, disp in [("A", HOOK_A, DISP_A), ("B", HOOK_B, DISP_B)]:
        pre = h.read_word(hook)
        if pre != disp:
            print(f"[full] NOTE: hook {nm} 0x{hook:08X} was 0x{pre:08X}, not vanilla "
                  f"-- taking it over (wavedash + auto-L-cancel OFF this run).",
                  flush=True)

    pd, port, delay = ao.resolve_pd(h)
    if delay is None:
        print("[full] ABORT: could not read the input delay from the ODB.")
        return 1
    tdi_w = ovr("tdi-window") if ovr("tdi-window") is not None else 3.0 + delay
    tech_w = ovr("tech-window") if ovr("tech-window") is not None else 2.0 + delay

    h.write_words(COUNTERS, [0] * len(CNAMES))
    poke(h, pattern, tdi_on, tdi_w, tech_w)
    for pay, cave, hook in [(pay_a, CAVE_A, HOOK_A), (pay_b, CAVE_B, HOOK_B)]:
        _retry(lambda: iw.write_instrs(h, cave, pay))
        _retry(lambda: iw.patch_branch(h, hook, cave))
    print(f"[full] hooks patched: A=0x{h.read_word(HOOK_A):08X} "
          f"B=0x{h.read_word(HOOK_B):08X}", flush=True)
    print(f"[full] local port {port}, input delay {delay}f -> tech window "
          f"hitlag < {tech_w}, TDI window hitlag < {tdi_w} "
          f"({'ON' if tdi_on else 'OFF'})", flush=True)
    print("=" * 72)
    print("ASDI + SDI + TECH ONLINE" + (" + TDI" if tdi_on else "") +
          " -- have the peer hit you AIRBORNE.")
    print("Watching the LOCAL player. Want: cstickY -1.00 during hitlag, and hits")
    print("ending in Passive* (tech) instead of DownBound*/an airborne arc.")
    print("CONFIRM ON THE PEER'S SCREEN THAT THE GAME DOES NOT DESYNC.")
    print("=" * 72, flush=True)

    last_ctr = [0] * len(CNAMES)
    active, states, cs_seen, results = None, [], [], []
    kb_pre = kb_post = None
    events = 0
    t_ctr = 0.0
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            now = time.time()
            # only between hits: resolve_pd majority-votes 15 reads and hitlag is
            # 4-9 frames, so anything extra inside the window costs samples.
            if active is None and now - t_ctr > 0.25:
                t_ctr = now
                try:
                    # re-poke between hits: REFERENCE 3.4 -- 0x803FAxxx data is
                    # not reliably preserved across rollback, and stale windows
                    # would silently disarm the tech press.
                    poke(h, pattern, tdi_on, tdi_w, tech_w)
                    c = [h.read_word(COUNTERS + i * 4) for i in range(len(CNAMES))]
                except Exception:
                    ao.ensure_hooked()
                    time.sleep(0.05)
                    continue
                if c != last_ctr:
                    print("[gate] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, c)),
                          flush=True)
                    last_ctr = c
            if active is None:
                pd, port, delay = ao.resolve_pd(h)
                if pd is None:
                    time.sleep(0.03)
                    continue
            try:
                fr = h.read_word(oh.FRAME)
                s = oh.sample(h, pd)
            except Exception:
                ao.ensure_hooked()
                time.sleep(0.05)
                continue
            if s is None:
                continue
            if s["hitlag"] != 0.0 and active is None:
                active, states, kb_post = [], [], None
                kb_pre = (s["kb_x"], s["kb_y"])
                events += 1
                print(f"[full] HIT #{events} kb ({kb_pre[0]:+.2f},{kb_pre[1]:+.2f})",
                      flush=True)
            if active is not None:
                if not active or active[-1] != fr:
                    active.append(fr)
                    states.append(s["state"])
                    cs_seen.append(s["cstick_y"])
                    print(oh.row(fr, s), flush=True)
                    if s["hitlag"] == 0.0 and s["air"] == 1 and kb_post is None:
                        kb_post = (s["kb_x"], s["kb_y"])
                if ((s["hitlag"] == 0.0 and s["hitstun"] == 0.0 and s["air"] == 0
                     and len(active) >= 3) or len(active) > 90):
                    v = verdict_of(states)
                    rot = clean_rot(kb_pre, kb_post)
                    if any(DMG_LO <= x <= DMG_HI for x in states):
                        results.append((v, rot))
                    r = f"ROT {rot:+.1f}" if rot is not None else "no clean post-kb"
                    print(f"[full] HIT #{events} ({len(active)}f) {r} -> {v}\n",
                          flush=True)
                    active = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[full] stopped")

    try:
        last_ctr = [h.read_word(COUNTERS + i * 4) for i in range(len(CNAMES))]
    except Exception:
        pass
    print("\n[full] ===== SUMMARY =====")
    print("[full] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, last_ctr)))
    down = [v for v in cs_seen if v is not None and v < -0.9]
    print(f"[full] hits observed={events}  frames sampled={len(cs_seen)}  "
          f"cstickY < -0.9 on {len(down)}")
    teched = sum(1 for v, _ in results if v.startswith("TECH"))
    rots = [r for _, r in results if r is not None]
    print(f"[full] victim hits={len(results)}  teched={teched}"
          + (f"  rotation avg {sum(rots) / len(rots):+.1f} "
             f"{[round(x, 1) for x in rots]}" if rots else ""))
    for i, (v, _) in enumerate(results, 1):
        print(f"[full]   {i}. {v}")
    idx = CNAMES.index
    if not last_ctr[idx("dmgst")]:
        first = next((n for n, v in zip(CNAMES, last_ctr) if v == 0), None)
        print(f"[full] [ISSUE] stick gate never reached a victim; first clause "
              f"with 0 = {first}")
    elif not last_ctr[idx("tech")]:
        print("[full] [ISSUE] hook B never pressed R -- gate reached "
              f"b_chain={last_ctr[idx('b_chain')]} but the tech window never hit.")
    print("[full] Dolphin left running. CONFIRM ON THE PEER'S SCREEN: no desync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
