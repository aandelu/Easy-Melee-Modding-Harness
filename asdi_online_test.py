"""asdi_online_test.py -- ONLINE validation of the ASDI floorhug at the PRODUCER hook.

Same gate the offline probe proved (asdi_probe_offline.py), moved to the netplay-safe
producer hook 0x8034E680 and resolving the LOCAL player via the ODB instead of an
indexed port. That indexing was the offline probe's attacker leak; here gate-player and
pad-player are the same by construction (PAD_Read builds only the local pad).

Standalone on purpose: 0x8034E680 is also the wavedash gecko's stick hook, so the two
must not run together. For shipping they get folded into one cave (docs/STATUS.md);
for this test the wavedash is simply not installed.

Runtime injection, not a gecko bake -- only the meta-flush needs to be in slot 4
(WORKFLOW.md 2.2). Everything happens in ONE process: re-attaching dme from a fresh
process yields torn reads.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 asdi_online_test.py [observe_seconds]
"""
import struct
import sys
import time

import attach_observe_wavedash as ao
import gecko_tools as gt
import instr_writer as iw
import observe_hitlag as oh
from melee_harness import Harness, finalize_payload
from peer import Peer

HOOK_ADDR = 0x8034E680
DISPLACED_ORIG = 0x88030007        # lbz r0, 7(r3)
CAVE = 0x803FA600                  # REFERENCE 4: recommended cave for online work
COUNTERS = 0x803FAA00              # clear of the cave, the control plane and WD_PEND

CSTICK_Y_DOWN = 0x90               # signed -112 = full down
PAD_CSTICK_Y = 5                   # PADStatus +0x5; finalized at 0x8034E660, upstream

CNAMES = ["reached", "chain_ok", "hitlag", "hitstun", "air", "fired"]


def _bump(i):
    """r11 = counter base, r6 = scratch. r6 is saved in the prologue and dead here."""
    return f"""
    lwz  6, {i * 4}(11)
    addi 6, 6, 1
    stw  6, {i * 4}(11)
"""


SRC = f"""
    /* r4 = local PADStatus (the wavedash cave writes stick bytes 2/3 here).
       r5-r11 saved; r0/r3 untouched -- the displaced lbz r0,7(r3) runs after us. */
    stwu 1, -0x30(1)
    stw  5, 0x08(1)
    stw  6, 0x0C(1)
    stw  7, 0x10(1)
    stw  8, 0x14(1)
    stw  9, 0x18(1)
    stw  10, 0x1C(1)
    stw  11, 0x20(1)

    lis  11, 0x803F
    ori  11, 11, 0xAA00            /* counter base, live for the whole payload */
{_bump(0)}
    lwz  5, -0x49E4(13)            /* ODB */
    cmpwi 5, 0
    beq  adone
    srwi 10, 5, 24
    cmplwi 10, 0x80                /* MEM1? garbage pointers crash Dolphin */
    bne  adone
    lbz  9, 0(5)                   /* ODB_LOCAL_PLAYER_INDEX */
    cmplwi 9, 3
    bgt  adone
    mulli 9, 9, 0xE90
    lis  5, 0x8045
    ori  5, 5, 0x3130              /* 0x80453130 = P1 GObj ptr */
    add  5, 5, 9
    lwz  5, 0(5)                   /* GObj */
    cmpwi 5, 0
    beq  adone
    srwi 10, 5, 24
    cmplwi 10, 0x80
    bne  adone
    lwz  5, 0x2C(5)                /* local Player Data */
    cmpwi 5, 0
    beq  adone
    srwi 10, 5, 24
    cmplwi 10, 0x80
    bne  adone
{_bump(1)}
    lwz  7, 0x195C(5)              /* hitlag (float; +0.0 is all-zero bits -> no FPU) */
    cmpwi 7, 0
    beq  adone
{_bump(2)}
    lwz  7, 0x2340(5)              /* hitstun -- VICTIM, not the attacker */
    cmpwi 7, 0
    beq  adone
{_bump(3)}
    lwz  7, 0xE0(5)                /* ground/air state */
    cmpwi 7, 1
    bne  adone
{_bump(4)}
    li   8, {CSTICK_Y_DOWN}        /* ASDI down: hold full-down c-stick through hitlag. */
    stb  8, {PAD_CSTICK_Y}(4)      /* No frame targeting -- ASDI samples the LAST hitlag */
{_bump(5)}                         /* frame, so being held by then is the whole job.     */
adone:
    lwz  5, 0x08(1)
    lwz  6, 0x0C(1)
    lwz  7, 0x10(1)
    lwz  8, 0x14(1)
    lwz  9, 0x18(1)
    lwz  10, 0x1C(1)
    lwz  11, 0x20(1)
    addi 1, 1, 0x30
"""

STB_CSTICK = 0x99040005            # stb r8, 5(r4)


def build():
    logic = gt.assemble(SRC, addr=CAVE)
    payload = finalize_payload(logic, HOOK_ADDR, CAVE, DISPLACED_ORIG)
    print(f"[asdi] {len(payload)} words @ 0x{CAVE:08X}; capstone readback:")
    for line in gt.disasm(payload, addr=CAVE):
        print(f"    {line}")
    assert STB_CSTICK in payload, "c-stick store missing (stb r8,5(r4))"
    assert payload[-2] == DISPLACED_ORIG, "displaced original not protected"
    return payload


def dolphin_alive():
    import subprocess
    return bool(subprocess.run(["pgrep", "-x", "Dolphin"],
                               capture_output=True, text=True).stdout.strip())


def bring_online(boots=3, attempts_per_boot=4):
    """Launch + enter online, relaunching if Dolphin dies.

    Observed 2026-07-25: the Mac's Dolphin can die on the MoltenVK backend
    ("VK_NOT_READY ... libc++abi: terminating" in dolphin.log) during the F4
    retry loop. Every remaining enter_online attempt then reads scene 0x-001 and
    burns ~30s each, so detect the death and reboot instead of retrying into a
    corpse. Not our code -- the cave isn't injected until after this returns.
    """
    import play_wavedash_offline as P
    for boot in range(1, boots + 1):
        P.kill_stale()
        h = Harness()
        print(f"[asdi] launching (boot {boot}/{boots}) ...", flush=True)
        h.launch()
        h.hook_dme()
        h._wait_for_cpu_alive(timeout_s=60.0)
        # meta-flush is BAKED into slot 4 -- installing it here would need a
        # seed_snapshot, which is the offline path.
        print("[asdi] entering online (driving the Windows peer) ...", flush=True)
        if h.enter_online(peer=Peer(), max_attempts=attempts_per_boot):
            return h
        if not dolphin_alive():
            print("[asdi] Dolphin died (see dolphin.log) -- relaunching.", flush=True)
            continue
        print("[asdi] Dolphin alive but never reached a match -- relaunching.",
              flush=True)
    return None


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    payload = build()

    h = bring_online()
    if h is None:
        print("[asdi] ABORT: never reached an online match.")
        return 1

    mf = h.read_word(iw.META_FLUSH_HOOK)
    if (mf >> 24) != 0x48:
        print(f"[asdi] ABORT: meta-flush hook 0x{iw.META_FLUSH_HOOK:08X} reads "
              f"0x{mf:08X}, not a branch -- it is not baked into slot 4.")
        return 1
    pre = h.read_word(HOOK_ADDR)
    if pre != DISPLACED_ORIG:
        # Almost certainly the wavedash gecko, which owns this same hook. Taking it
        # over is safe -- its cave is simply orphaned -- but it means no wavedash and
        # no auto-L-cancel for the duration of this test (the L-cancel is folded into
        # that cave). Not an error, but say so, because "my wavedash stopped working"
        # would otherwise look like a desync symptom.
        print(f"[asdi] NOTE: hook 0x{HOOK_ADDR:08X} was 0x{pre:08X}, not vanilla "
              f"0x{DISPLACED_ORIG:08X} -- taking it over from the wavedash gecko. "
              f"Wavedash + auto-L-cancel are OFF for this run.", flush=True)

    h.write_words(COUNTERS, [0] * len(CNAMES))
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, CAVE)
    hooked = h.read_word(HOOK_ADDR)
    print(f"[asdi] hook patched: 0x{hooked:08X}", flush=True)

    pd, port, delay = ao.resolve_pd(h)
    print(f"[asdi] local port {port}, input delay {delay} frames "
          f"(need hitlag >= delay+1; min measured hitlag is 4)", flush=True)
    print("=" * 72)
    print("ASDI ONLINE LIVE -- have the peer hit you. Watching the LOCAL player.")
    print("  cstickY should read -1.00 during your hitlag (the injection landing),")
    print("  and the hit should end in a floorhug instead of an airborne arc.")
    print("=" * 72, flush=True)

    last_ctr = [0] * len(CNAMES)
    active = None
    events = 0
    cs_seen = []
    t_ctr = 0.0
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            now = time.time()
            # Only poll the counters between hits: resolve_pd majority-votes 15 reads
            # and hitlag is 4-9 frames, so anything extra inside the window costs
            # samples. Same reason pd is cached while a hit is active (a respawn can
            # only move it between hits).
            if active is None and now - t_ctr > 0.25:
                t_ctr = now
                try:
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
                active = []
                events += 1
                print(f"[asdi] HIT #{events}", flush=True)
            if active is not None:
                if not active or active[-1] != fr:
                    active.append(fr)
                    cs_seen.append(s["cstick_y"])
                    print(oh.row(fr, s), flush=True)
                if ((s["hitlag"] == 0.0 and s["hitstun"] == 0.0 and s["air"] == 0
                     and len(active) >= 3) or len(active) > 90):
                    print(f"[asdi] HIT #{events} done ({len(active)} frames)\n",
                          flush=True)
                    active = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[asdi] stopped")

    try:
        last_ctr = [h.read_word(COUNTERS + i * 4) for i in range(len(CNAMES))]
    except Exception:
        pass
    print("\n[asdi] ===== SUMMARY =====")
    print("[asdi] " + "  ".join(f"{n}={v}" for n, v in zip(CNAMES, last_ctr)))
    down = [v for v in cs_seen if v is not None and v < -0.9]
    print(f"[asdi] hits observed={events}  frames sampled={len(cs_seen)}  "
          f"frames with cstickY < -0.9 = {len(down)}")
    if last_ctr[CNAMES.index("fired")] and down:
        print("[asdi] [GOOD] the injection fired AND the engine saw c-stick down -- "
              "the producer-side write survives online.")
    elif last_ctr[CNAMES.index("fired")]:
        print("[asdi] [ISSUE] gate fired but no c-stick down observed -- the write is "
              "being overwritten downstream, or the poll missed it.")
    else:
        first_zero = next((n for n, v in zip(CNAMES, last_ctr) if v == 0), None)
        print(f"[asdi] [ISSUE] gate never fired; first clause with 0 = {first_zero}")
    print("[asdi] Dolphin left running. CONFIRM ON THE PEER'S SCREEN: no desync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
