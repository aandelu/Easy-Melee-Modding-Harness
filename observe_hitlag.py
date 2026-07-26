"""observe_hitlag.py -- log what the victim's Player Data actually looks like through
a hit, so the ASDI (floorhug) macro's gate is built on measurement, not on the video.

Answers, per hit event: is +0xE0 (ground/air) really 1 during hitlag?  How many hitlag
frames?  What are hitstun (+0x2340), KB velocity (+0x8C/+0x90), Y vs last-landed-Y?

Launches Dolphin and observes in ONE process (REFERENCE.md 5.2: re-attaching dme from
a fresh process yields torn reads).  Offline, so ports resolve directly -- no ODB.
Pure observation: nothing is injected, no gecko is installed.

  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 observe_hitlag.py [seconds]
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 observe_hitlag.py [seconds] --attach
"""
import struct
import sys
import time

import dolphin_memory_engine as dme
from melee_harness import Harness, POWERON_COUNT

FRAME = 0x80479D60

# Player Data offsets (REFERENCE.md 1.3 + Char_Data_Offsets.csv)
OFF = {
    "state":    (0x10,   "w"),   # action state ID
    "kb_x":     (0x8C,   "f"),   # horizontal velocity (attack-induced)
    "kb_y":     (0x90,   "f"),   # vertical velocity (attack-induced)
    "y":        (0xB4,   "f"),   # Y position
    "air":      (0xE0,   "w"),   # 0 = ground, 1 = air
    "cstick_y": (0x63C,  "f"),   # c-stick Y as the engine sees it (-1..1)
    "land_y":   (0x834,  "f"),   # position last landed Y
    "hitlag":   (0x195C, "f"),   # counts down; != 0 => in hitlag
    "hitstun":  (0x2340, "f"),   # frames of hitstun left
}

# ID_Lists.csv -- only the ones this savestate can contain; unknown ids print raw
CHARS = {0x01: "Fox", 0x02: "Falco", 0x0A: "Peach", 0x12: "Marth", 0x0E: "Falcon"}


def ensure_hooked():
    if dme.is_hooked():
        return True
    for _ in range(25):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.2)
    return False


def sample(h, pd):
    """Read every tracked field for one player. None on any torn read."""
    out = {}
    for name, (off, kind) in OFF.items():
        try:
            raw = h.read_bytes(pd + off, 4)
        except Exception:
            return None
        out[name] = (struct.unpack(">f", raw)[0] if kind == "f"
                     else struct.unpack(">I", raw)[0] & 0xFFFF)
        out[name + "_raw"] = struct.unpack(">I", raw)[0]
    return out


def row(fr, s):
    return (f"  f{fr:<7} state 0x{s['state']:04X}  hitlag {s['hitlag']:5.2f}  "
            f"air {s['air']}  hitstun {s['hitstun']:5.2f}  "
            f"y {s['y']:8.2f} (landed {s['land_y']:8.2f}, d {s['y'] - s['land_y']:+9.4f} "
            f"[{s['y_raw']:08X}/{s['land_y_raw']:08X}])  "
            f"kb ({s['kb_x']:+6.2f},{s['kb_y']:+6.2f})  cstickY {s['cstick_y']:+5.2f}")


def bring_up(h):
    """Launch Dolphin and load slot 2, in this process. No geckos -- we only read."""
    print("[oh] launching Dolphin ...", flush=True)
    h.launch()
    h.hook_dme()
    prev = h.read_word(POWERON_COUNT)
    for _ in range(60):
        time.sleep(1.0)
        cur = h.read_word(POWERON_COUNT)
        if cur != prev:
            print(f"[oh] CPU live ({prev} -> {cur})", flush=True)
            break
        prev = cur
    print("[oh] loading slot 2 ...", flush=True)
    h.seed_snapshot(timeout_s=60.0)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    secs = float(args[0]) if args else 120.0
    h = Harness()
    if "--attach" in sys.argv:
        if not ensure_hooked():
            print("[oh] dme never attached -- is the harness Dolphin running?")
            return 1
    else:
        bring_up(h)

    # Harness ports are 1-INDEXED (entity_ptr does port-1); player_data_ptr
    # returns -1, not None, when a slot is empty.
    pds, names = {}, {}
    for port in (1, 2, 3, 4):
        try:
            pd = h.player_data_ptr(port)
            cid = h.char_id(port) if pd != -1 else -1
        except Exception:
            continue
        if pd != -1:
            pds[port] = pd
            names[port] = CHARS.get(cid, f"char 0x{cid:02X}")
    if not pds:
        print("[oh] no valid Player Data -- not in a match?")
        return 1
    print("[oh] tracking " + "  ".join(
        f"P{p}={names[p]} @0x{a:08X}" for p, a in sorted(pds.items())))
    who = "  ".join(f"P{p}={names[p]}" for p in sorted(pds))
    print("=" * 70)
    print(f"READY -- {who}. Both are tracked, so hit EITHER one.")
    print("Land hits on a GROUNDED victim (jab, f-tilt, d-tilt, f-smash) -- that is")
    print("the case the floorhug gate has to recognize. Vary the percent if you can.")
    print("=" * 70)
    print(f"[oh] watching {secs:.0f}s. Ctrl-C to stop early.\n", flush=True)

    # per-port: are we mid-event, and the frames captured so far
    active = {p: None for p in pds}
    events = {p: 0 for p in pds}
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            try:
                fr = h.read_word(FRAME)
            except Exception:
                ensure_hooked()
                time.sleep(0.05)
                continue
            for port, pd in pds.items():
                s = sample(h, pd)
                if s is None:
                    continue
                in_hitlag = s["hitlag"] != 0.0
                if in_hitlag and active[port] is None:
                    active[port] = []
                    events[port] += 1
                    print(f"[oh] P{port} {names[port]} HIT #{events[port]}")
                if active[port] is not None:
                    # keep sampling ~10 frames past hitlag to catch the ASDI frame,
                    # the air->ground transition, and the landing lag that follows
                    seen = active[port]
                    if not seen or seen[-1] != fr:
                        seen.append(fr)
                        print(row(fr, s))
                    if not in_hitlag and len(seen) > 0 and s["hitstun"] == 0.0 \
                            and s["air"] == 0 and len(seen) >= 3:
                        print(f"[oh] P{port} {names[port]} settled (grounded, hitstun 0) "
                              f"after {len(seen)} frames\n")
                        active[port] = None
                    elif len(seen) > 90:
                        print(f"[oh] P{port} {names[port]} event ran long -- closing\n")
                        active[port] = None
            time.sleep(0.004)
    except KeyboardInterrupt:
        print("\n[oh] stopped")
    print(f"[oh] events: " + ", ".join(f"P{p} {names[p]}={n}" for p, n in sorted(events.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
