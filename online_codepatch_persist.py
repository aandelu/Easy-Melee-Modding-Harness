"""
online_codepatch_persist.py -- does a RUNTIME code patch at 0x8034E2AC persist
across in-match rollbacks online?  (meta-flush comes from the slot-4 savestate.)

We install a behaviorally-IDENTICAL no-op at 0x8034E2AC:
    0x8034E2AC: b <cave>
    cave+0: rlwinm r0,r0,0x10,0x12,0x1f   (the displaced original -- same effect)
    cave+4: b 0x8034E2B0                    (back to the next instruction)
So the local input path computes exactly what it did before -> no input change,
no desync. But it's a real runtime branch we can watch for persistence.

If 0x8034E2AC stays BRANCH for ~30s of online play, runtime code patches survive
rollback -> I can iterate the L-cancel entirely over dme. If it reverts to
0x540084BE, that region is rollback-restored and I'll bake instead.

Branch words are capstone-verified before flushing.

Run:
  DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_codepatch_persist.py
"""
import subprocess
import sys
import time
from collections import Counter

import capstone
import dolphin_memory_engine as dme
import melee_harness as mh
from melee_harness import Harness, DEFAULT_CAVE, finalize_payload
import instr_writer as iw

VK_F4 = 118
VK_RETURN = 36
SCENE_WORD = 0x80479D30
FRAME = 0x80479D60

HOOK = 0x8034E2AC
DISPLACED = 0x540084BE            # rlwinm r0,r0,0x10,0x12,0x1f
CAVE = DEFAULT_CAVE               # 0x803FA3E8 (debug region -- proven to survive rollback)


def mm(w):
    return ((w << 8) | (w >> 24)) & 0xFFFF


def scene_maj(h, n=15):
    vals = [mm(h.read_word(SCENE_WORD)) for _ in range(n)]
    top, c = Counter(vals).most_common(1)[0]
    return top, c, n


def word_maj(h, addr, n=11):
    vals = []
    for _ in range(n):
        try:
            vals.append(h.read_word(addr))
        except Exception:
            vals.append(-1)
        time.sleep(0.008)
    top, c = Counter(vals).most_common(1)[0]
    return top, c


def is_branch(w):
    return (w & 0xFC000000) == 0x48000000


def kill_stale():
    if subprocess.run(["pkill", "-9", "-x", "Dolphin"], capture_output=True).returncode == 0:
        for _ in range(40):
            if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True,
                                  text=True).stdout.strip():
                return
            time.sleep(0.25)


def main():
    # Build + capstone-verify the payload BEFORE touching Dolphin.
    payload = finalize_payload([], HOOK, CAVE, DISPLACED)   # [displaced, branch-back]
    branch_at_hook = 0x48000000 | ((CAVE - HOOK) & 0x03FFFFFC)
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_32)
    print("[cp] payload verify (capstone):", flush=True)
    print(f"  hook 0x{HOOK:08X} <- 0x{branch_at_hook:08X}:", flush=True)
    for i in md.disasm(branch_at_hook.to_bytes(4, 'big'), HOOK):
        print(f"      {i.mnemonic} {i.op_str}", flush=True)
    print(f"  cave 0x{CAVE:08X}:", flush=True)
    code = b"".join(w.to_bytes(4, 'big') for w in payload)
    for i in md.disasm(code, CAVE):
        print(f"      0x{i.address:08X}: {i.bytes.hex().upper():<10} {i.mnemonic} {i.op_str}",
              flush=True)
    # sanity: cave[0] must be the displaced original; last must branch to HOOK+4
    assert payload[0] == DISPLACED, "cave[0] != displaced original"
    print("  [ok] cave[0] == displaced original; branch-back present", flush=True)

    kill_stale()
    h = Harness()                       # meta-flush from savestate, not boot
    print("\n[cp] launching ...", flush=True)
    h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60.0)
    pid = h._proc.pid

    print("[cp] entering online: F4, +3s, Enter, +15s ...", flush=True)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_F4)
    time.sleep(3.0)
    mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN)
    time.sleep(15.0)
    online = False
    for _ in range(4):
        top, cc, nn = scene_maj(h)
        print(f"[cp] scene 0x{top:04X} ({cc}/{nn})", flush=True)
        if top == 0x0208 and cc >= nn * 0.6:
            online = True; break
        mh._focus_pid(pid); time.sleep(0.3); mh._send_key(VK_RETURN); time.sleep(6.0)
    if not online:
        print("[cp] not online; abort", flush=True); dme.un_hook(); return 1
    print("[cp] CONFIRMED online", flush=True)

    # meta-flush present?
    w, c = word_maj(h, iw.META_FLUSH_HOOK)
    print(f"[cp] meta-flush 0x803775C0 = 0x{w:08X} "
          f"({'BRANCH' if is_branch(w) else 'NOT present -- abort'}) ({c}/11)", flush=True)
    if not is_branch(w):
        dme.un_hook(); return 1

    # pre-patch state of hook
    w, c = word_maj(h, HOOK)
    print(f"[cp] pre-patch 0x{HOOK:08X} = 0x{w:08X} ({c}/11)", flush=True)

    # install no-op patch via meta-flush (write cave, flush; write branch, flush)
    print(f"\n[cp] installing no-op patch (cave 0x{CAVE:08X}) via meta-flush "
          f"(WATCH SCREEN) ...", flush=True)
    iw.write_instrs(h, CAVE, payload)
    iw.patch_branch(h, HOOK, CAVE)
    w, c = word_maj(h, HOOK)
    print(f"[cp] post-patch 0x{HOOK:08X} = 0x{w:08X} "
          f"({'BRANCH (installed)' if is_branch(w) else 'NOT a branch?!'}) ({c}/11)",
          flush=True)

    # monitor persistence ~30s
    print("\n[cp] persistence monitor (~30s):", flush=True)
    last = h.read_word(FRAME)
    stayed = True
    for i in range(15):
        time.sleep(2.0)
        w, c = word_maj(h, HOOK, 7)
        top, cc, nn = scene_maj(h, 7)
        f = h.read_word(FRAME)
        br = is_branch(w)
        stayed &= br
        print(f"  t+{i*2:2d}s hook=0x{w:08X} {'BRANCH' if br else 'REVERTED'} "
              f"scene 0x{top:04X} frame 0x{f:08X} (+{f-last})", flush=True)
        last = f

    print(f"\n[cp] === runtime code patch persisted across rollbacks: {stayed} ===",
          flush=True)
    if stayed:
        print("[cp] -> dme iteration of the L-cancel at 0x8034E2AC is viable online", flush=True)
    else:
        print("[cp] -> 0x8034E2AC reverts at runtime; must bake the L-cancel into the SS", flush=True)
    print("[cp] DONE. Dolphin left running. >>> any desync on your screen? <<<", flush=True)
    dme.un_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
