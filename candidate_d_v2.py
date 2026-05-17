"""candidate_d_min + counter check at top.

Built up from the WORKING candidate_d_min. Adds the counter check before
the action-state check. If this gecko fires (counter byte at 0x803FA424
becomes 0x42 when Marth in Catch), the counter check is fine. If not, the
counter check is breaking things.

Layout:
   0  cmpwi  r24, 1
   1  bne    END        (+0x40, 16 ahead)
   2  lis    r11, 0x803F
   3  ori    r11, r11, 0xA424
   4  lbz    r9, 0(r11)
   5  cmpwi  r9, 0
   6  bne    END        (+0x2C, 11 ahead) ; exit if counter != 0
   7  lis    r12, 0x8045
   8  ori    r12, r12, 0x3130
   9  lwz    r12, 0(r12)
  10  lwz    r12, 0x2C(r12)
  11  lhz    r0, 0x12(r12)
  12  cmpwi  r0, 0xD4
  13  beq    TRIGGER    (+0x08, 2 ahead)
  14  b      END        (+0xC, 3 ahead)
  15  li     r0, 0x42                ; TRIGGER
  16  stb    r0, 0(r11)
  17  (displaced)
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d-v2"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x40820040,  # 1  bne    END
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x896B0000,  # 4  lbz    r9, 0(r11)
    0x2C090000,  # 5  cmpwi  r9, 0
    0x4082002C,  # 6  bne    END
    0x3D808045,  # 7  lis    r12, 0x8045
    0x618C3130,  # 8  ori    r12, r12, 0x3130
    0x818C0000,  # 9  lwz    r12, 0(r12)
    0x818C002C,  # 10 lwz    r12, 0x2C(r12)
    0xA00C0012,  # 11 lhz    r0, 0x12(r12)
    0x2C0000D4,  # 12 cmpwi  r0, 0xD4
    0x41820008,  # 13 beq    TRIGGER
    0x4800000C,  # 14 b      END
    0x38000042,  # 15 li     r0, 0x42
    0x980B0000,  # 16 stb    r0, 0(r11)
]
