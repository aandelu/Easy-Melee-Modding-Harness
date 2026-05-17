"""min_write + an lbz r9, 0(r11) before the write. Tests if the lbz
itself somehow breaks the subsequent store.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-min-lbz-write"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x4082001C,  # 1  bne    END (7 ahead = 0x1C)
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x896B0000,  # 4  lbz    r9, 0(r11)
    0x38000042,  # 5  li     r0, 0x42
    0x980B0000,  # 6  stb    r0, 0(r11)
    # pos 7 = END
]
