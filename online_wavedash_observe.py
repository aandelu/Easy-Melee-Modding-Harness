"""
online_wavedash_observe.py -- READ-ONLY observation for the wavedash macro.

Step 1 of wavedash dev: get into a live online match and watch the LOCAL
player's inputs + action-state sequence so we can see exactly what "up all the
way" looks like in memory and how a manual wavedash unfolds frame-by-frame.

Makes ZERO writes to game state -> cannot desync. No gecko staged.

What it does:
  1. launch Slippi Dolphin via the harness hardlink, hook dme, wait for CPU
  2. F4 (load slot 4 = direct-connect) -> 3s -> Enter (connect) -> ~15s
  3. confirm SCENE_ONLINE_IN_GAME (0x0208) by majority vote
  4. resolve the LOCAL player via the ODB (port + input delay)
  5. for OBSERVE_SECS, sample the local player once per game frame and print a
     line whenever something changes (state / stick / c-stick / trigger /
     buttons), plus a heartbeat. Also dumps the ODB header so we can eyeball the
     delay byte.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_wavedash_observe.py [secs]

Leaves Dolphin RUNNING (does not close) so we can re-observe / move to injection
without relaunching.
"""
import struct
import subprocess
import sys
import time

import melee_harness as mh
from melee_harness import Harness
import dolphin_memory_engine as dme

VK_F4 = 118
VK_RETURN = 36

SCENE_ADDR = 0x80479D30          # getMinorMajor source
FRAME_ADDR = 0x80479D60          # global frame counter
SCENE_ONLINE_IN_GAME = 0x0208

# r13 (SDA base) verified 0x804DB6A0 in a prior online session; constant per run.
# ODB pointer lives at *(r13 - 0x49E4).
R13 = 0x804DB6A0
ODB_PTR_SLOT = R13 - 0x49E4      # 0x804D6CBC

P1_GOBJ = 0x80453130
STRIDE = 0xE90
OFF_PLAYER_DATA = 0x2C

# Player Data offsets (Char_Data_Offsets.csv)
OFF_STATE = 0x10                 # action state (low 16 bits)
OFF_FACING = 0x2C                # float, +1 right / -1 left
OFF_ASTICK_X = 0x620             # float analog stick X
OFF_ASTICK_Y = 0x624             # float analog stick Y
OFF_CSTICK_X = 0x638             # float c-stick X
OFF_CSTICK_Y = 0x63C             # float c-stick Y
OFF_TRIGGER = 0x650              # float analog trigger
OFF_BUTTONS = 0x65C             # processed buttons word
OFF_ASFC = 0x894                 # action-state frame counter (float, resets 1.0)
OFF_IASA = 0x2218                # IASA flags byte (0x80 = interruptible/actionable)
OFF_HITSTUN = 0x2340             # frames of hitstun left (float, decrements)

# ODB offsets (Online/Online.s)
ODB_LOCAL_PLAYER_INDEX = 0x00    # u8  (this machine's player)
ODB_ONLINE_PLAYER_INDEX = 0x01   # u8  (the PEER == the user on the other machine)
ODB_FRAME = 0x03                 # u32
ODB_DELAY_FRAMES = 0x21          # u8  (= 21 + PAD_REPORT_SIZE(0xC))

STATE_NAMES = {
    0x0E: "Wait", 0x0F: "WalkSlow", 0x10: "WalkMid", 0x11: "WalkFast",
    0x12: "Turn", 0x13: "TurnRun", 0x14: "Dash", 0x15: "Run",
    0x16: "RunDirect", 0x17: "RunBrake", 0x18: "KneeBend(jumpsquat)",
    0x19: "JumpF", 0x1A: "JumpB", 0x1B: "JumpAerialF", 0x1C: "JumpAerialB",
    0x1D: "Fall", 0x1E: "FallF", 0x1F: "FallB", 0x20: "FallAerial",
    0x21: "FallAerialF", 0x22: "FallAerialB", 0x23: "FallSpecial",
    0x24: "FallSpecialF", 0x25: "FallSpecialB", 0x26: "DamageFall",
    0x27: "Squat", 0x28: "SquatWait", 0x29: "SquatRv", 0x2A: "Landing",
    0x2B: "LandingFallSpecial(WAVEDASH)", 0x41: "AttackAirN", 0x42: "AttackAirF",
    0x43: "AttackAirB", 0x44: "AttackAirHi", 0x45: "AttackAirLw",
    0x46: "LandingAirN", 0x47: "LandingAirF", 0x48: "LandingAirB",
    0x49: "LandingAirHi", 0x4A: "LandingAirLw", 0xEC: "EscapeAir(AIRDODGE)",
}


def st_name(s):
    return STATE_NAMES.get(s, f"0x{s:02X}")


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


def rehook():
    try:
        dme.un_hook()
    except Exception:
        pass
    for _ in range(20):
        dme.hook()
        if dme.is_hooked():
            return True
        time.sleep(0.1)
    return False


def sw(addr):
    """safe read_word with one re-hook retry"""
    try:
        return dme.read_word(addr) & 0xFFFFFFFF
    except Exception:
        if rehook():
            try:
                return dme.read_word(addr) & 0xFFFFFFFF
            except Exception:
                return None
        return None


def sbytes(addr, n):
    try:
        return dme.read_bytes(addr, n)
    except Exception:
        if rehook():
            try:
                return dme.read_bytes(addr, n)
            except Exception:
                return None
        return None


def u32(b):
    return struct.unpack(">I", b)[0]


def f32(addr):
    b = sbytes(addr, 4)
    if b is None:
        return None
    return struct.unpack(">f", b)[0]


def valid_ptr(p):
    return p is not None and 0x80000000 <= p < 0x81800000


def getmm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def majority_scene(n=9):
    vals = []
    for _ in range(n):
        w = sw(SCENE_ADDR)
        if w is not None:
            vals.append(getmm(w))
        time.sleep(0.006)
    if not vals:
        return -1
    return max(set(vals), key=vals.count)


def main():
    observe_secs = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0

    kill_stale_dolphins()
    h = Harness()
    print("[obs] launching Dolphin (read-only; no gecko staged)", flush=True)
    h.launch()
    h.hook_dme()
    h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid
    print(f"[obs] Dolphin pid {pid}; CPU live", flush=True)

    # --- enter online --------------------------------------------------------
    print("[obs] entering online: F4 -> 3s -> Enter -> 15s ...", flush=True)
    mh._focus_pid(pid)
    time.sleep(0.3)
    mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid)
    time.sleep(0.3)
    mh._send_key(VK_RETURN)
    time.sleep(15.0)

    scene = majority_scene()
    print(f"[obs] scene (getMinorMajor) = 0x{scene:04X} "
          f"({'ONLINE IN-GAME' if scene == SCENE_ONLINE_IN_GAME else 'NOT in-game'})",
          flush=True)
    if scene != SCENE_ONLINE_IN_GAME:
        print("[obs] not in-game -- the other machine may not be in an active "
              "match. Dolphin left running; re-run to retry F4/Enter.", flush=True)
        return 1

    # --- resolve players via ODB --------------------------------------------
    # The USER is the ONLINE (peer) player on THIS Dolphin, so we observe
    # ODB_ONLINE_PLAYER_INDEX, not the local index.
    odb = sw(ODB_PTR_SLOT)
    print(f"[obs] ODB ptr slot 0x{ODB_PTR_SLOT:08X} -> "
          f"{'0x%08X' % odb if odb is not None else 'None'}", flush=True)
    local_idx = online_idx = None
    if valid_ptr(odb):
        hdr = sbytes(odb, 0x40)
        if hdr is not None:
            print("[obs] ODB header bytes [0x00..0x3F]:", flush=True)
            print("      " + " ".join(f"{b:02X}" for b in hdr[:0x20]), flush=True)
            print("      " + " ".join(f"{b:02X}" for b in hdr[0x20:0x40]),
                  flush=True)
            local_idx = hdr[ODB_LOCAL_PLAYER_INDEX]
            online_idx = hdr[ODB_ONLINE_PLAYER_INDEX]
            odb_frame = u32(hdr[ODB_FRAME:ODB_FRAME + 4])
            delay = hdr[ODB_DELAY_FRAMES]
            print(f"[obs] ODB_LOCAL_PLAYER_INDEX(+0x00)  = {local_idx} "
                  f"(this machine = P{local_idx + 1})", flush=True)
            print(f"[obs] ODB_ONLINE_PLAYER_INDEX(+0x01) = {online_idx} "
                  f"(YOU = P{online_idx + 1})  <-- observing this one", flush=True)
            print(f"[obs] ODB_FRAME(+0x03) = {odb_frame}", flush=True)
            print(f"[obs] ODB_DELAY_FRAMES(+0x21) = {delay}  "
                  f"<-- cross-check vs your known connection delay", flush=True)
    if online_idx is None:
        # ODB unreadable: assume this machine is P1 (local idx 0) so you are P2.
        online_idx = 1
        local_idx = 0
        print("[obs] WARNING: ODB invalid; assuming YOU = P2. Tell me your port "
              "if the state log looks like the opponent.", flush=True)

    # Startup census of both ports so we can confirm which character is yours.
    for idx in sorted({local_idx, online_idx}):
        g = sw(P1_GOBJ + idx * STRIDE)
        p = sw(g + OFF_PLAYER_DATA) if valid_ptr(g) else None
        cid = sw(p + 0x4) if valid_ptr(p) else None
        st = (sw(p + OFF_STATE) & 0xFFFF) if valid_ptr(p) else None
        tag = "YOU(online)" if idx == online_idx else "this-machine(local)"
        print(f"[obs] P{idx + 1} [{tag}]: PlayerData="
              f"{'0x%08X' % p if p else 'None'} char=0x{(cid or 0) & 0xFF:02X} "
              f"state={st_name(st) if st is not None else 'None'}", flush=True)

    obs_idx = online_idx
    gobj = sw(P1_GOBJ + obs_idx * STRIDE)
    pd = sw(gobj + OFF_PLAYER_DATA) if valid_ptr(gobj) else None
    if not valid_ptr(pd):
        print("[obs] could not resolve YOUR player data -- aborting observe.",
              flush=True)
        return 1

    # --- observe loop --------------------------------------------------------
    print(f"\n[obs] ===== OBSERVING P{obs_idx + 1} (YOU) for {observe_secs:.0f}s "
          "-- do some manual wavedashes now (up+dir), and a few from landing-lag "
          "to test the buffer case =====\n", flush=True)
    print("  frame | state                          | stickX stickY | cX    cY"
          "    | trig  | facing | buttons | asfc  | IASA | hitstun", flush=True)

    # pd pointer can move (rare); re-resolve each loop from obs_idx.
    t_end = time.time() + observe_secs
    last_frame = None
    last_key = None
    last_beat = 0.0
    while time.time() < t_end:
        fr = sw(FRAME_ADDR)
        if fr is None:
            time.sleep(0.01)
            continue
        if fr == last_frame:
            time.sleep(0.001)
            continue
        last_frame = fr

        gobj = sw(P1_GOBJ + obs_idx * STRIDE)
        pd = sw(gobj + OFF_PLAYER_DATA) if valid_ptr(gobj) else None
        if not valid_ptr(pd):
            continue
        state = sw(pd + OFF_STATE)
        if state is None:
            continue
        state &= 0xFFFF
        sx = f32(pd + OFF_ASTICK_X)
        sy = f32(pd + OFF_ASTICK_Y)
        cx = f32(pd + OFF_CSTICK_X)
        cy = f32(pd + OFF_CSTICK_Y)
        trig = f32(pd + OFF_TRIGGER)
        facing = f32(pd + OFF_FACING)
        buttons = sw(pd + OFF_BUTTONS)
        asfc = f32(pd + OFF_ASFC)
        iasa_b = sbytes(pd + OFF_IASA, 1)
        iasa = iasa_b[0] if iasa_b else None
        hitstun = f32(pd + OFF_HITSTUN)
        if None in (sx, sy, cx, cy, trig, facing, buttons, asfc, iasa, hitstun):
            continue

        # change-detection key (rounded so stick jitter doesn't spam)
        key = (state, round(sx, 2), round(sy, 2), round(cx, 2), round(cy, 2),
               round(trig, 2), buttons & 0xFFFF, iasa & 0x80)
        now = time.time()
        changed = key != last_key
        beat = now - last_beat > 2.0
        if changed or beat:
            act = "ACT" if (iasa & 0x80) else "  -"
            print(f"  {fr & 0xFFFF:5d} | {st_name(state):30s} | "
                  f"{sx:+5.2f} {sy:+5.2f} | {cx:+4.2f} {cy:+4.2f} | "
                  f"{trig:4.2f} | {facing:+4.1f} | {buttons & 0xFFFF:04X} | "
                  f"{asfc:5.1f} | {iasa:02X} {act} | "
                  f"{hitstun:4.1f}", flush=True)
            last_key = key
            last_beat = now

    print("\n[obs] DONE. Dolphin left RUNNING. Re-run for another window, or "
          "we move to injection next.", flush=True)
    try:
        dme.un_hook()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
