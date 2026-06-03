"""
online_orL_test.py -- does producer-side L injection at 0x8034E2AC work online?

Minimal cave (touches ONLY r0, so no register save/restore needed):
    oris  r0, r0, 0x0040               # set L bit in raw SI high-16
    rlwinm r0, r0, 0x10, 0x12, 0x1f    # displaced original -> buttons land in [r4+0]
    b 0x8034E2B0                        # back
With L set every local poll, the local character should hold shield (L = shield
from standing), transmitted to the peer with no desync (producer-side, local).

Observes both ports' action states before/after the patch to see which character
enters a Guard state (0x0B2..0x0B6) -- that's the local player, and proves the L
injection reached the engine.

keystone-assembled, capstone-verified before flushing.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_orL_test.py
"""
import struct
import subprocess
import sys
import time
from collections import Counter

import capstone
import keystone
import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness, DEFAULT_CAVE, finalize_payload
import instr_writer as iw

VK_F4 = 118
VK_RETURN = 36
SCENE_WORD = 0x80479D30
FRAME = 0x80479D60
HOOK = 0x8034E2AC
DISPLACED = 0x540084BE
CAVE = DEFAULT_CAVE
OFF_ACTION_STATE = 0x10
GUARD_STATES = set(range(0x0B2, 0x0B7))   # GuardOn..GuardReflect


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def scene_maj(h, n=15):
    vals = [mm(h.read_word(SCENE_WORD)) for _ in range(n)]
    return Counter(vals).most_common(1)[0]


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def states(h):
    out = {}
    for port in (1, 2):
        try:
            pd = h.player_data_ptr(port)
            out[port] = (h.read_word(pd + OFF_ACTION_STATE) & 0xFFFF) if pd != -1 else None
        except Exception:
            out[port] = None
    return out


def main():
    ks = keystone.Ks(keystone.KS_ARCH_PPC, keystone.KS_MODE_PPC32 | keystone.KS_MODE_BIG_ENDIAN)
    raw, _ = ks.asm("oris 0, 0, 0x40")
    logic = [struct.unpack(">I", bytes(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]
    payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)

    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print("[orL] cave verify (capstone):", flush=True)
    code = b"".join(w.to_bytes(4, 'big') for w in payload)
    for i in md.disasm(code, CAVE):
        print(f"   0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
              flush=True)
    assert logic[0] == 0x64000040, f"oris encoding unexpected: {logic[0]:#010x}"
    print("   [ok] oris r0,r0,0x40 == 0x64000040", flush=True)

    kill_stale()
    h = Harness()
    print("\n[orL] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[orL] online entry: F4,+3s,Enter,+15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(4):
        top, cc = scene_maj(h)
        print(f"[orL] scene 0x{top:04X} ({cc}/15)", flush=True)
        if top == 0x0208 and cc >= 9:
            online = True; break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[orL] not online; abort", flush=True); dme.un_hook(); return 1

    w = h.read_word(iw.META_FLUSH_HOOK)
    if (w & 0xFC000000) != 0x48000000:
        print(f"[orL] meta-flush not present (0x{w:08X}); abort", flush=True)
        dme.un_hook(); return 1
    print("[orL] meta-flush present", flush=True)

    print("\n[orL] baseline action states (no patch), 4 samples:", flush=True)
    for _ in range(4):
        print(f"   {states(h)}", flush=True); time.sleep(0.5)

    print(f"\n[orL] installing oris-L patch (WATCH: a character should start shielding) ...",
          flush=True)
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    w = h.read_word(HOOK)
    print(f"[orL] hook 0x{HOOK:08X} = 0x{w:08X} "
          f"({'BRANCH' if (w & 0xFC000000)==0x48000000 else '?!'})", flush=True)

    print("\n[orL] post-patch action states (~10s):", flush=True)
    guard_seen = {1: False, 2: False}
    last = h.read_word(FRAME)
    for i in range(10):
        s = states(h)
        for p in (1, 2):
            if s[p] in GUARD_STATES:
                guard_seen[p] = True
        f = h.read_word(FRAME)
        print(f"   t+{i}s {s}  frame +{f-last}", flush=True)
        last = f
        time.sleep(1.0)

    print(f"\n[orL] guard state seen: {guard_seen}", flush=True)
    if guard_seen[1] or guard_seen[2]:
        port = 1 if guard_seen[1] else 2
        print(f"[orL] [PASS] port {port} entered Guard -> producer-side L injection "
              f"WORKS online (that's the local player).", flush=True)
    else:
        print("[orL] [?] no Guard state observed -- character may not have been in a "
              "standing state, or injection didn't take. Check with user.", flush=True)
    print("[orL] DONE. Dolphin left running. >>> is a character shielding? any desync? <<<",
          flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
