"""
auto_lcancel/test_fox_aerials.py

Comprehensive verification: every Fox aerial in both short-hop and full-hop,
with L-cancel off vs cycle-7. 20 sub-trials total (5 aerials x 2 hops x 2 L
modes). For each (aerial, hop) we expect the L-on landing duration to be
<= 60% of the L-off baseline.

Hook is parameterized via three scratch bytes (so we install ONE hook and
just flip bytes between trials, no re-install needed):

    L_FLAG       (0=off, 1=cycle-7 L)
    AERIAL_TYPE  (0=NAIR, 1=FAIR(stick+x), 2=BAIR(stick-x),
                  3=UAIR(stick+y), 4=DAIR(stick-y))
    FULL_HOP_FLAG (0=short hop, 1=full hop)

Aerial type translates to stick deflection injected on the first JumpF/Fall
frame alongside the A press. The engine then triggers the corresponding
aerial. FAIR/BAIR depend on Fox's facing -- we don't try to fix that
mechanically; we just report the observed action state. If Fox faces left,
'FAIR (stick+x)' produces BAIR (state 0x43) and vice versa. That's fine for
the L-cancel test -- both still cancel.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/test_fox_aerials.py
"""
import os
import struct
import subprocess
import sys
import time

import keystone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from melee_harness import Harness, DEFAULT_CAVE, finalize_payload
import instr_writer as iw

OFF_ACTION_STATE = 0x0010
OFF_LR_TIMER = 0x0680
OFF_LANDING_LAG_DIVISOR = 0x2354

S_WAIT = 0x000E
S_KNEEBEND = 0x0018
AERIAL_STATES = set(range(0x41, 0x46))
LANDING_AERIAL_STATES = set(range(0x46, 0x4B))
S_LANDING_PLAIN = 0x2A
S_AIRDODGE = 0x00EC

TARGET_PORT = 2

HOOK_ADDR = 0x803775B8
DISPLACED = 0xA0190000
RIG_CAVE = DEFAULT_CAVE + 0x400     # past meta-flush + macro caves

# Scratch bytes (debug-menu free region, well past meta-flush control plane).
L_FLAG_ADDR = 0x803FA480
AERIAL_TYPE_ADDR = 0x803FA481
FULL_HOP_FLAG_ADDR = 0x803FA482
L_CYCLE_COUNTER_ADDR = 0x803FA483

# Aerial type -> (stick offset, stick value as signed byte). For NAIR
# (type 0) we don't touch the stick.
AERIAL_NAMES = {0: "NAIR (A only)",
                1: "FAIR? (stick +x)",
                2: "BAIR? (stick -x)",
                3: "UAIR  (stick +y)",
                4: "DAIR  (stick -y)"}


def _build_src():
    return f"""
        cmpwi 24, 1
        bne   end

        # P2 GObj -> Player Data -> action state
        lis   12, 0x8045
        ori   12, 12, 0x3FC0
        lwz   12, 0(12)
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24
        cmplwi 9, 0x80
        bne   end
        lwz   12, 0x2C(12)
        cmpwi 12, 0
        beq   end
        srwi  9, 12, 24
        cmplwi 9, 0x80
        bne   end

        lwz   11, 0x10(12)            # r11 = action state
        lhz   0, 0(25)                 # r0  = current 16-bit pad buttons

        # Wait (0x0E) -> press X (jump)
        cmpwi 11, 0x000E
        bne   not_wait
        ori   0, 0, 0x0400
        sth   0, 0(25)
        b     reset_l_counter
    not_wait:

        # KneeBend (0x18) -> press X iff FULL_HOP_FLAG set (full hop)
        cmpwi 11, 0x0018
        bne   not_kneebend
        lis   9, 0x{(FULL_HOP_FLAG_ADDR >> 16):04X}
        ori   9, 9, 0x{(FULL_HOP_FLAG_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        beq   reset_l_counter         # short hop -> don't hold X
        ori   0, 0, 0x0400
        sth   0, 0(25)
        b     reset_l_counter
    not_kneebend:

        # JumpF / Fall (0x19..0x22) -> press A + stick based on AERIAL_TYPE.
        cmpwi 11, 0x0019
        blt   not_jump
        cmpwi 11, 0x0022
        bgt   not_jump

        # Press A regardless of aerial type.
        ori   0, 0, 0x0100
        sth   0, 0(25)

        # Read AERIAL_TYPE, then write stick.
        lis   9, 0x{(AERIAL_TYPE_ADDR >> 16):04X}
        ori   9, 9, 0x{(AERIAL_TYPE_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)

        cmpwi 10, 1                   # FAIR (stick +x)
        bne   not_aerial_fair
        li    10, 80
        stb   10, 2(25)
        b     reset_l_counter
    not_aerial_fair:
        cmpwi 10, 2                   # BAIR (stick -x)
        bne   not_aerial_bair
        li    10, -80
        stb   10, 2(25)
        b     reset_l_counter
    not_aerial_bair:
        cmpwi 10, 3                   # UAIR (stick +y)
        bne   not_aerial_uair
        li    10, 80
        stb   10, 3(25)
        b     reset_l_counter
    not_aerial_uair:
        cmpwi 10, 4                   # DAIR (stick -y)
        bne   not_aerial_dair
        li    10, -80
        stb   10, 3(25)
        b     reset_l_counter
    not_aerial_dair:
        # NAIR (type 0 or unknown): no stick injection
        b     reset_l_counter
    not_jump:

        # Aerial states 0x41..0x45 -> reset stick (neutral) + cycle-7 L.
        cmpwi 11, 0x0041
        blt   reset_l_counter
        cmpwi 11, 0x0045
        bgt   reset_l_counter

        # Force stick neutral so we don't drift/fastfall accidentally.
        li    10, 0
        stb   10, 2(25)
        stb   10, 3(25)

        # Gated by L_FLAG: 0 = no L, 1 = cycle-7
        lis   9, 0x{(L_FLAG_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_FLAG_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        beq   apply

        # cycle-7 counter at L_CYCLE_COUNTER_ADDR.
        lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
        lbz   10, 0(9)
        cmpwi 10, 0
        bne   inc_counter
        ori   0, 0, 0x0040            # press L (rising edge)
    inc_counter:
        addi  10, 10, 1
        cmpwi 10, 7
        blt   store_counter
        li    10, 0
    store_counter:
        stb   10, 0(9)
        b     apply

    reset_l_counter:
        # Non-aerial frame: reset cycle counter so next aerial entry begins on
        # a press frame.
        li    10, 0
        lis   9, 0x{(L_CYCLE_COUNTER_ADDR >> 16):04X}
        ori   9, 9, 0x{(L_CYCLE_COUNTER_ADDR & 0xFFFF):04X}
        stb   10, 0(9)

    apply:
        sth   0, 0(25)

    end:
    """


def assemble(src):
    ks = keystone.Ks(keystone.KS_ARCH_PPC,
                     keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm(src)
    if raw is None:
        raise RuntimeError("keystone returned None")
    return [struct.unpack(">I", bytes(raw[i:i+4]))[0]
            for i in range(0, len(raw), 4)]


def kill_stale_dolphins():
    r = subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True)
    if r.returncode == 0:
        for _ in range(40):
            p = subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                               text=True)
            if not p.stdout.strip():
                return
            time.sleep(0.25)
        raise RuntimeError("stale Dolphin refused to die within 10s")


def read_state(h, pd):
    a = h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF
    lr = h.read_bytes(pd + OFF_LR_TIMER, 1)[0]
    return a, lr


def wait_for_wait(h, pd, max_frames=120):
    for _ in range(max_frames):
        a, _ = read_state(h, pd)
        if a == S_WAIT:
            return True
        h.wait_frames(1)
    return False


def watch_one_cycle(h, pd, max_frames=320):
    """Wait until Fox enters a Landing-Aerial state (or plain landing or
    airdodge), then continues until back to Wait. Return data about the
    aerial that fired and the landing state observed."""
    aerial_seen = None
    landing_index = None
    cycle_end = None
    airdodge_seen = False
    plain_landing_seen = False
    states_observed = set()
    for i in range(max_frames):
        a, lr = read_state(h, pd)
        states_observed.add(a)
        if a in AERIAL_STATES and aerial_seen is None:
            aerial_seen = a
        if a == S_AIRDODGE:
            airdodge_seen = True
        if a == S_LANDING_PLAIN:
            plain_landing_seen = True
        if a in LANDING_AERIAL_STATES and landing_index is None:
            landing_index = i
        if landing_index is not None and a == S_WAIT:
            cycle_end = i
            break
        h.wait_frames(1)
    duration = None
    if landing_index is not None and cycle_end is not None:
        duration = cycle_end - landing_index
    return {
        "aerial_state": aerial_seen,
        "landing_index": landing_index,
        "duration": duration,
        "airdodge": airdodge_seen,
        "plain_landing": plain_landing_seen,
        "states": states_observed,
    }


def run_one_trial(h, pd, aerial_type, full_hop, l_on):
    h.write_bytes(L_FLAG_ADDR, b"\x01" if l_on else b"\x00")
    h.write_bytes(AERIAL_TYPE_ADDR, bytes([aerial_type]))
    h.write_bytes(FULL_HOP_FLAG_ADDR, b"\x01" if full_hop else b"\x00")
    h.write_bytes(L_CYCLE_COUNTER_ADDR, b"\x00")
    if not wait_for_wait(h, pd):
        return {"error": "Fox never returned to Wait", "duration": None}
    return watch_one_cycle(h, pd)


def main():
    kill_stale_dolphins()
    h = Harness()
    iw.install_meta_flush(h)
    print("[test] launching Dolphin ...", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    iw.wait_for_meta_flush_alive(h, timeout_s=30.0)
    print("[test] meta-flush alive. seeding slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)
    pd = h.player_data_ptr(TARGET_PORT)
    if pd == -1:
        print("[test] P2 player data invalid -- abort", flush=True)
        return 1

    # Init all scratch bytes BEFORE installing hook.
    for addr in (L_FLAG_ADDR, AERIAL_TYPE_ADDR, FULL_HOP_FLAG_ADDR,
                 L_CYCLE_COUNTER_ADDR):
        h.write_bytes(addr, b"\x00")

    # Install hook
    logic = assemble(_build_src())
    payload = finalize_payload(logic, HOOK_ADDR, RIG_CAVE, DISPLACED)
    iw.write_instrs(h, RIG_CAVE, payload)
    iw.patch_branch(h, HOOK_ADDR, RIG_CAVE)
    print(f"[test] hook installed at 0x{HOOK_ADDR:08X} -> cave 0x{RIG_CAVE:08X} "
          f"({len(payload)} words)", flush=True)
    h.wait_frames(20)               # let any startup-cycling settle

    # Settle to a clean Wait.
    wait_for_wait(h, pd, max_frames=300)
    h.wait_frames(3)

    print("\n" + "=" * 80, flush=True)
    print("Running 20 sub-trials (5 aerials x 2 hops x [L off, L cycle-7]):",
          flush=True)
    print("=" * 80, flush=True)
    results = {}       # (aerial, hop) -> {'off': ..., 'on': ...}
    for hop_full in (False, True):
        for aerial_type in (0, 1, 2, 3, 4):
            key = (aerial_type, hop_full)
            results[key] = {}
            for l_on in (False, True):
                label = (f"  {AERIAL_NAMES[aerial_type]:24s}  "
                         f"hop={'FULL' if hop_full else 'short'}  "
                         f"L={'cycle-7' if l_on else 'off    '}")
                print(label, end="  ", flush=True)
                r = run_one_trial(h, pd, aerial_type, hop_full, l_on)
                duration = r.get("duration")
                aerial_state = r.get("aerial_state")
                airdodge = r.get("airdodge")
                plain = r.get("plain_landing")
                tag = ""
                if airdodge:
                    tag += " AIRDODGE!"
                if plain:
                    tag += " plain-landing!"
                print(f"-> aerial=0x{aerial_state or 0:04X}  "
                      f"duration={duration}f{tag}", flush=True)
                results[key]["on" if l_on else "off"] = {
                    "duration": duration,
                    "aerial_state": aerial_state,
                    "airdodge": airdodge,
                    "plain_landing": plain,
                }

    # Summary table.
    # Verdict logic:
    #   PASS  -- both trials landed during the aerial AND ratio <= 0.6
    #   N/A   -- both trials plain-landed (aerial finished in air); the move
    #            can't be L-cancelled in this configuration, not a macro bug
    #   FAIL  -- L on caused airdodge, OR L on plain-landed while L off didn't,
    #            OR ratio > 0.6
    print("\n" + "=" * 80, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 80, flush=True)
    print(f"  {'config':28s}  {'aerial':8s}  {'L off':>6s}  {'L on':>6s}  "
          f"{'ratio':>6s}  {'verdict':8s}", flush=True)
    failures = []
    for hop_full in (False, True):
        for aerial_type in (0, 1, 2, 3, 4):
            key = (aerial_type, hop_full)
            r_off = results[key]["off"]
            r_on = results[key]["on"]
            cfg = (f"{AERIAL_NAMES[aerial_type]:18s} "
                   f"{'FULL' if hop_full else 'short':5s}")
            a = r_on["aerial_state"]
            d_off = r_off["duration"]
            d_on = r_on["duration"]
            off_plain = r_off.get("plain_landing")
            on_plain = r_on.get("plain_landing")
            off_air = r_off.get("airdodge")
            on_air = r_on.get("airdodge")

            verdict = "—"
            ratio = None
            if on_air or off_air:
                verdict = "FAIL-AD"
                failures.append(f"{cfg}: airdodge (off={off_air} on={on_air})")
            elif off_plain and on_plain and d_off is None and d_on is None:
                # Move can't be L-cancelled in this config -- aerial finished
                # in air both times. Not a macro bug.
                verdict = "N/A"
            elif d_off and d_on:
                ratio = d_on / d_off
                if ratio <= 0.6:
                    verdict = "PASS"
                else:
                    verdict = "FAIL"
                    failures.append(f"{cfg}: ratio {ratio:.3f} > 0.6 "
                                    f"({d_off}f -> {d_on}f)")
            elif d_off and not d_on:
                # L on lost the landing-aerial that L off had -- bad sign.
                verdict = "FAIL-OBS"
                failures.append(f"{cfg}: L-on no LandingAir (off={d_off}f, "
                                f"on plain={on_plain})")
            else:
                verdict = "FAIL-OBS"
                failures.append(f"{cfg}: unexpected (off={d_off}, on={d_on}, "
                                f"off_plain={off_plain}, on_plain={on_plain})")

            print(f"  {cfg:28s}  0x{(a or 0):04X}    {str(d_off):>6s}  "
                  f"{str(d_on):>6s}  "
                  f"{(f'{ratio:.3f}' if ratio else '   —'):>6s}  {verdict}",
                  flush=True)

    print(flush=True)
    if failures:
        print(f"[FAIL] {len(failures)} problem(s):", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    n_pass = sum(1 for (af, hf) in [(a, h) for a in range(5) for h in (False, True)]
                 if results[(af, hf)]["on"]["duration"] is not None
                 and results[(af, hf)]["off"]["duration"] is not None)
    n_na = 10 - n_pass
    print(f"[PASS] {n_pass} (aerial, hop) combos L-cancelled (ratio <= 0.6); "
          f"{n_na} N/A (move finished in air, can't be L-cancelled). "
          f"No airdodge observed in 20 trials.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
