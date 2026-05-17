"""Diagnostic: v2 with a sentinel write immediately after the counter check.

If 0x803FA428 ends up as 0xCC after running, execution passed the counter
check (pos 6 bne END not taken). If 0x803FA428 stays at slot-1 garbage,
the counter check is taking unexpectedly.

Layout (END = pos 20):
   0  cmpwi  r24, 1
   1  bne    END        (+0x4C, 19 ahead)
   2  lis    r11, 0x803F
   3  ori    r11, r11, 0xA424
   4  lbz    r9, 0(r11)
   5  cmpwi  r9, 0
   6  bne    END        (+0x38, 14 ahead)
   7  li     r0, 0xCC                     ; SENTINEL
   8  stb    r0, 4(r11)                   ; -> 0x803FA428
   9  lis    r12, 0x8045
  10  ori    r12, r12, 0x3130
  11  lwz    r12, 0(r12)
  12  lwz    r12, 0x2C(r12)
  13  lhz    r0, 0x12(r12)
  14  cmpwi  r0, 0xD4
  15  ... wait re-count.
"""

# Re-derived cleanly:
#   0  cmpwi  r24, 1
#   1  bne    END           (+0x4C)
#   2  lis    r11, 0x803F
#   3  ori    r11, r11, 0xA424
#   4  lbz    r9, 0(r11)
#   5  cmpwi  r9, 0
#   6  bne    END           (+0x38)
#   7  li     r0, 0xCC                   ; SENTINEL
#   8  stb    r0, 4(r11)                 ; -> 0x803FA428 (0xCC)
#   9  lis    r12, 0x8045
#  10  ori    r12, r12, 0x3130
#  11  lwz    r12, 0(r12)
#  12  lwz    r12, 0x2C(r12)
#  13  lhz    r0, 0x12(r12)
#  14  cmpwi  r0, 0xD4
#  15  beq    TRIGGER       (+0x08)
#  16  b      END           (+0x10)
#  17  li     r0, 0x42                   ; TRIGGER
#  18  stb    r0, 0(r11)                 ; -> 0x803FA424 (counter = 0x42)
#  19  (displaced)
#
# pos 15 beq TRIGGER (pos 17): 2 ahead = 8 bytes = 0x08
# pos 16 b END (pos 19): 3 ahead = 12 bytes = 0x0C

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d-v3"

COUNTER_ADDR = 0x803FA424
SENTINEL_ADDR = 0x803FA428

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x40820048,  # 1  bne    END  (18 instr ahead = 0x48)
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x896B0000,  # 4  lbz    r9, 0(r11)
    0x2C090000,  # 5  cmpwi  r9, 0
    0x40820034,  # 6  bne    END (counter != 0; 13 instr ahead = 0x34)
    0x380000CC,  # 7  li     r0, 0xCC
    0x980B0004,  # 8  stb    r0, 4(r11)     ; SENTINEL -> 0x803FA428
    0x3D808045,  # 9  lis    r12, 0x8045
    0x618C3130,  # 10 ori    r12, r12, 0x3130
    0x818C0000,  # 11 lwz    r12, 0(r12)
    0x818C002C,  # 12 lwz    r12, 0x2C(r12)
    0xA00C0012,  # 13 lhz    r0, 0x12(r12)
    0x2C0000D4,  # 14 cmpwi  r0, 0xD4
    0x41820008,  # 15 beq    TRIGGER
    0x4800000C,  # 16 b      END
    0x38000042,  # 17 li     r0, 0x42
    0x980B0000,  # 18 stb    r0, 0(r11)
]
