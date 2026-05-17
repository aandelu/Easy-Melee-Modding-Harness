"""Diagnostic probe v2: on Fox pad pass, read BOTH the counter byte (from
0x803FA424) AND Marth's action state half-word, write each to a separate
scratch location. This shows what the gecko sees at runtime for both.

Layout (END = pos 17):
   0  cmpwi  r24, 1
   1  bne    END        (+0x40, 16 ahead)
   2  lis    r11, 0x803F
   3  ori    r11, r11, 0xA424
   4  lbz    r9,  0(r11)            ; r9 = counter
   5  stb    r9,  8(r11)            ; -> 0x803FA42C (echo counter to scratch)
   6  lis    r12, 0x8045
   7  ori    r12, r12, 0x3130
   8  lwz    r12, 0(r12)
   9  lwz    r12, 0x2C(r12)
  10  lhz    r0,  0x12(r12)         ; r0 = action state
  11  sth    r0,  10(r11)           ; -> 0x803FA42E (echo action state)
  12-16 (no-ops, padding)
  17 (displaced)

Wait — I want to keep instructions minimal. Let me reduce.

Final layout (END = pos 12):
   0  cmpwi  r24, 1
   1  bne    END        (+0x2C, 11 ahead)
   2  lis    r11, 0x803F
   3  ori    r11, r11, 0xA424
   4  lbz    r9,  0(r11)
   5  stb    r9,  8(r11)
   6  lis    r12, 0x8045
   7  ori    r12, r12, 0x3130
   8  lwz    r12, 0(r12)
   9  lwz    r12, 0x2C(r12)
  10  lhz    r0,  0x12(r12)
  11  sth    r0,  10(r11)
  12 (displaced)

11 - 1 = 11 instr ahead = 44 bytes = 0x2C.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d-probe2"

COUNTER_ADDR = 0x803FA424
COUNTER_ECHO_ADDR = 0x803FA42C        # byte at COUNTER_ADDR + 8
ACTION_ECHO_ADDR = 0x803FA42E         # halfword at COUNTER_ADDR + 10

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x4082002C,  # 1  bne    END (11 ahead = 0x2C)
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x896B0000,  # 4  lbz    r9, 0(r11)
    0x996B0008,  # 5  stb    r9, 8(r11)
    0x3D808045,  # 6  lis    r12, 0x8045
    0x618C3130,  # 7  ori    r12, r12, 0x3130
    0x818C0000,  # 8  lwz    r12, 0(r12)
    0x818C002C,  # 9  lwz    r12, 0x2C(r12)
    0xA00C0012,  # 10 lhz    r0, 0x12(r12)
    0xB00B000A,  # 11 sth    r0, 10(r11)
]
