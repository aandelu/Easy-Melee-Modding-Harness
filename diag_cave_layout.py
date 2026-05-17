"""Dump what Slippi's codehandler actually wrote into the cave for the
standalone JC-shine gecko, so we can see whether the trailing 0x00000000
padding ends up between the displaced instruction and the codehandler's
branch-back -- which would make CPU fall through into an illegal opcode
after every macro pass."""
import sys
import time

from melee_harness import Harness, POWERON_COUNT
import candidate_d_standalone as cs


HOOK_ADDR = cs.HOOK_ADDR  # 0x803775B8


def main():
    h = Harness()
    try:
        h.install_gecko_c2(
            name=cs.NAME, hook_addr=cs.HOOK_ADDR,
            logic_words=cs.LOGIC, displaced_orig=cs.DISPLACED_ORIG,
        )
        h.launch()
        h.hook_dme()

        print("waiting for CPU to start ticking...", flush=True)
        prev = h.read_word(POWERON_COUNT)
        for _ in range(60):
            time.sleep(1.0)
            cur = h.read_word(POWERON_COUNT)
            if cur != prev:
                print(f"  CPU live ({prev}->{cur})", flush=True)
                break
            prev = cur

        # Wait a beat for codehandler to finish installing
        time.sleep(2.0)

        # Read the hook word -- should be a branch
        hook = h.read_word(HOOK_ADDR)
        print(f"\nhook word at 0x{HOOK_ADDR:08X}: 0x{hook:08X}", flush=True)
        # Decode branch target
        opcode = (hook >> 26) & 0x3F
        if opcode != 18:
            print(f"  not a b-form opcode (got {opcode}); aborting")
            return 1
        li = hook & 0x03FFFFFC
        if li & 0x02000000:
            li |= 0xFC000000  # sign-extend
        li = li if li < 0x80000000 else li - 0x100000000
        cave_addr = (HOOK_ADDR + li) & 0xFFFFFFFF
        print(f"  branch target (cave): 0x{cave_addr:08X}", flush=True)

        # Dump cave: 44 logic + 1 displaced + 1 pad + a few trailing words
        # We expect the codehandler to append at least one branch-back word.
        DUMP = 50  # plenty of headroom
        print(f"\ncave contents ({DUMP} words):")
        for i in range(DUMP):
            w = h.read_word(cave_addr + i * 4)
            tag = ""
            if i < len(cs.LOGIC):
                tag = f"LOGIC[{i}]"
            elif i == len(cs.LOGIC):
                tag = "displaced lhz r0,0(r25)"
            elif i == len(cs.LOGIC) + 1:
                tag = "pad / first appended"
            else:
                tag = f"trailing+{i-(len(cs.LOGIC)+1)}"
            # Decode branch back if it looks like a b
            op = (w >> 26) & 0x3F
            if op == 18 and w != 0:
                tli = w & 0x03FFFFFC
                if tli & 0x02000000:
                    tli |= 0xFC000000
                tli = tli if tli < 0x80000000 else tli - 0x100000000
                bt = (cave_addr + i * 4 + tli) & 0xFFFFFFFF
                tag += f"  [b -> 0x{bt:08X}]"
            print(f"  +0x{i*4:03X}  0x{w:08X}   {tag}", flush=True)

        return 0
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
