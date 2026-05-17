"""exp12: verify 0x80003040 is the PADStatus base.

If it is, writing a button mask to 0x80003040 + 0x44*port_index (or
similar offset) should make Fox jump via the SAME code path the gecko
uses, with 100% reliability.

Test: write Y (0x0800) to several candidate offsets and see if Fox
enters KneeBend.
"""
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (  # noqa: E402
    BIT_Y, BIT_B, Harness, snapshot_frame, runs_dir,
)


def dump_region(h, addr, n_bytes=64):
    """Dump n_bytes from addr in hex + structured PADStatus interpretation."""
    data = h.read_bytes(addr, n_bytes)
    print(f"\n0x{addr:08X} ({n_bytes} bytes):")
    for i in range(0, n_bytes, 16):
        hex_part = " ".join(f"{b:02X}" for b in data[i:i+16])
        print(f"  +{i:02X}: {hex_part}")
    # PADStatus interpretation if struct is 12 bytes
    print("  PADStatus[4] (12-byte stride):")
    for p in range(4):
        o = p * 12
        if o + 12 > n_bytes:
            break
        btn = struct.unpack(">H", data[o:o+2])[0]
        stx = data[o+2] - 256 if data[o+2] > 127 else data[o+2]
        sty = data[o+3] - 256 if data[o+3] > 127 else data[o+3]
        cx = data[o+4] - 256 if data[o+4] > 127 else data[o+4]
        cy = data[o+5] - 256 if data[o+5] > 127 else data[o+5]
        lt = data[o+6]
        rt = data[o+7]
        aA = data[o+8]
        aB = data[o+9]
        err = struct.unpack(">h", data[o+10:o+12])[0]
        print(f"    [{p}] btn=0x{btn:04X} stick=({stx:+d},{sty:+d}) "
              f"cStick=({cx:+d},{cy:+d}) L={lt} R={rt} aA={aA} aB={aB} "
              f"err={err}")


def test_write(h, addr, port_offset, mask, duration_s=0.5):
    """Write button mask to addr+port_offset for duration_s, watch Fox.

    Idea: if addr is the PADStatus base, writing to port 1's (Fox's)
    PADStatus.buttons (halfword at offset 0) should make Fox jump.
    Different from the engine-plane writes, this should win the race
    every time because the engine READS this -- it doesn't get
    overwritten by Dolphin's input pipeline (because THIS is the raw
    plane Dolphin writes TO).
    """
    h.reset()
    h.wait_frames(3)
    pre_state = h.action_state(2) & 0xFFFF
    print(f"  pre p2_action=0x{pre_state:04X}")
    write_addr = addr + port_offset
    t_end = time.time() + duration_s
    writes = 0
    while time.time() < t_end:
        # Write button halfword (offset 0 of PADStatus)
        h.write_bytes(write_addr, struct.pack(">H", mask))
        writes += 1
    print(f"  wrote {writes} times to 0x{write_addr:08X}")
    # Sample for some frames
    samples = []
    for _ in range(10):
        samples.append(snapshot_frame(h))
        h.wait_frames(1)
    states = []
    for r in samples:
        s = r["p2_action"] & 0xFFFF
        if not states or states[-1] != s:
            states.append(s)
    states_str = [f"0x{s:04X}" for s in states]
    print(f"  -> states: {' -> '.join(states_str)}")
    return states


def main():
    h = Harness()
    try:
        h.launch()
        h.hook_dme()
        h.seed_snapshot()
        h.save_savestate(1)

        # First, just look at what's at the candidate base addresses.
        # 0x80003040 -- direct candidate from disasm
        # 0x80003080 -- adjacent
        # 0x800030C0 -- adjacent
        candidates = [0x80003040, 0x80003080, 0x800030C0, 0x80003020,
                      0x800030A0]
        for addr in candidates:
            try:
                dump_region(h, addr, n_bytes=48)
            except Exception as e:
                print(f"0x{addr:08X}: {e}")

        # Reset to clean state
        h.reset()
        h.wait_frames(3)

        # Try writing Y to port 1 (Fox's PADStatus, assuming port index 1)
        # at 0x80003040 with various per-port strides.
        print("\n\n=== Try writing Y press to candidate PADStatus addresses ===")
        for addr in (0x80003040, 0x80003080):
            for stride in (0xC, 0x10, 0x14, 0x44):
                for port_idx in (0, 1, 2):
                    label = f"base=0x{addr:08X} stride=0x{stride:X} port_idx={port_idx}"
                    write_addr_off = stride * port_idx
                    try:
                        print(f"\nTrying {label}")
                        test_write(h, addr, write_addr_off, BIT_Y,
                                   duration_s=0.3)
                    except Exception as e:
                        print(f"  {label}: {e}")
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
