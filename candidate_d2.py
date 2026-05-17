"""Candidate D.2: netplay-safe Frame-1 jump-cancelled shine.

This is candidate_d.py (the previous-session attempt at the netplay-safe
JC-shine) with the encoding bug at index 4 fixed -- that single bug was
the entire reason D.1 "never fired".

Bug recap: idx 4 was `0x896B0000`, labeled `lbz r9, 0(r11)`, but actually
decodes as `lbz r11, 0(r11)` -- clobbering the counter-pointer register
with the counter byte value. Every register-state assumption after that
was wrong, so the bne at idx 6 saw r9 still holding pre-hook garbage and
(almost always) took the COUNTER_NZ branch instead of the arm path.
Correct encoding for `lbz r9, 0(r11)` is `0x892B0000`.

State machine (Fox port pass only):
  counter == 0, Marth in {Catch, CatchDash, CatchTurn}:
      counter = 1
      press Y (jump)
  counter == 1..2:
      counter += 1                (jumpsquat frames 2, 3)
  counter == 3..5:
      counter += 1
      press B + stickY = -127     (buffered JC shine input)
  counter == 6+:
      do nothing (terminal)

Verified PASS by verify_d2.py.

Hook:  0x803775B8  (HSD_PadRead, BEFORE the buttons read)
Orig:  0xA0190000  (lhz r0, 0(r25))
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "jc-shine-netplay-safe-v2"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    # idx 0..1: only Fox's port
    0x2C180001,    # 0   cmpwi  r24, 1
    0x4082009C,    # 1   bne    END           (target idx 40, offset 0x9C)
    # idx 2..6: read counter, dispatch on counter == 0 vs not
    0x3D60803F,    # 2   lis    r11, 0x803F
    0x616BA424,    # 3   ori    r11, r11, 0xA424
    0x892B0000,    # 4   lbz    r9,  0(r11)   ; <-- FIXED (was 0x896B0000)
    0x2C090000,    # 5   cmpwi  r9, 0
    0x40820050,    # 6   bne    COUNTER_NZ    (target idx 26, offset 0x50)
    # idx 7..19: counter == 0 path -- read Marth's action state, range check
    0x3D808045,    # 7   lis    r12, 0x8045
    0x618C3130,    # 8   ori    r12, r12, 0x3130
    0x818C0000,    # 9   lwz    r12, 0(r12)
    0x2C0C0000,    # 10  cmpwi  r12, 0
    0x41820074,    # 11  beq    END           (offset 0x74)
    0x818C002C,    # 12  lwz    r12, 0x2C(r12)
    0x2C0C0000,    # 13  cmpwi  r12, 0
    0x41820068,    # 14  beq    END           (offset 0x68)
    0xA00C0012,    # 15  lhz    r0,  0x12(r12)
    0x280000D4,    # 16  cmplwi r0,  0xD4
    0x4180005C,    # 17  blt    END           (offset 0x5C)
    0x280000D6,    # 18  cmplwi r0,  0xD6
    0x41810054,    # 19  bgt    END           (offset 0x54)
    # idx 20..25: arm -- counter = 1, press Y
    0x38000001,    # 20  li     r0, 1
    0x980B0000,    # 21  stb    r0, 0(r11)
    0xA0190000,    # 22  lhz    r0, 0(r25)
    0x60000800,    # 23  ori    r0, r0, 0x800       ; Y
    0xB0190000,    # 24  sth    r0, 0(r25)
    0x4800003C,    # 25  b      END                  (offset 0x3C)
    # idx 26..29: counter != 0 -- bucket: shine vs increment vs terminal
    0x2C090003,    # 26  cmpwi  r9, 3          [COUNTER_NZ]
    0x4180002C,    # 27  blt    NO_INPUT_INC   (target idx 38, offset 0x2C)
    0x2C090006,    # 28  cmpwi  r9, 6
    0x4080002C,    # 29  bge    END            (offset 0x2C)
    # idx 30..37: counter in 3..5 -- buffered shine, increment counter
    0xA0190000,    # 30  lhz    r0, 0(r25)
    0x60000200,    # 31  ori    r0, r0, 0x200        ; B
    0xB0190000,    # 32  sth    r0, 0(r25)
    0x38000081,    # 33  li     r0, 0x81             ; -127
    0x98190003,    # 34  stb    r0, 3(r25)           ; stickY
    0x39290001,    # 35  addi   r9, r9, 1
    0x992B0000,    # 36  stb    r9, 0(r11)
    0x4800000C,    # 37  b      END                  (offset 0x0C)
    # idx 38..39: counter in 1..2 -- increment only
    0x39290001,    # 38  addi   r9, r9, 1     [NO_INPUT_INC]
    0x992B0000,    # 39  stb    r9, 0(r11)
    # idx 40 = END = displaced + branch back (auto-appended)
]
assert len(LOGIC) == 40
