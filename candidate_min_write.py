"""Absolute minimum gecko: on Fox pad pass, write 0x42 to counter address
0x803FA424 via r11. No conditions, no chain, no checks.

If 0x803FA424 doesn't become 0x42 after running this, something is
fundamentally broken in how I'm using r11 / stb / the scratch address.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-min-write"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x40820018,  # 1  bne    END   (6 instr ahead = 0x18)
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x38000042,  # 4  li     r0, 0x42
    0x980B0000,  # 5  stb    r0, 0(r11)
    # pos 6 = END (displaced)
]
