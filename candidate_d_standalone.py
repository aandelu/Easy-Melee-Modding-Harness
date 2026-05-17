"""Self-resetting netplay-safe Frame-1 JC-shine.

Same trigger logic as candidate_d2, but adds automatic counter reset so the
macro re-arms after each grab. No Python harness needed -- install the
gecko code, start a match, and every Marth grab triggers Fox's JC-shine.

State machine (Fox port pass only):
  state == 0, Marth in grab (0xD4..0xD6):
      state = 1, press Y
  state in 1..2:
      state += 1
  state in 3..5:
      state += 1, press B + stickY=-127
  state >= 6:
      Marth in grab     -> stay (waiting for grab to release)
      Marth not in grab -> state = 0 (ready for next grab)

Hardcoded assumptions:
  - Fox is on port 2 (r24 == 1). Adjust the cmpwi at idx 0 if Fox is on a
    different port.
  - Marth/grabbing character is on port 1 (P1 GObj at 0x80453130).
    Works for any character on P1 -- the trigger is "P1 is in Catch /
    CatchDash / CatchTurn", which is universal across characters.
  - Does NOT include a Slippi-online-only check. The macro spoofs only
    local Fox inputs, so it's netplay-safe in the sense of "doesn't
    corrupt opponent state". But it fires offline too. If you want
    online-only, gate behind the scene check at 0x80489D30 == 0x208
    (per Gecko_Code_Analysis.md).
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000         # lhz r0, 0(r25)
NAME = "jc-shine-netplay-safe-standalone"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    # 0..4: setup, load state
    0x2C180001,    # 0   cmpwi  r24, 1                ; Fox port only
    0x408200AC,    # 1   bne    END                   ; (44-1)*4 = 0xAC
    0x3D60803F,    # 2   lis    r11, 0x803F
    0x616BA424,    # 3   ori    r11, r11, 0xA424
    0x892B0000,    # 4   lbz    r9, 0(r11)            ; r9 = state
    # 5..8: dispatch on state range
    0x2C090000,    # 5   cmpwi  r9, 0
    0x41820034,    # 6   beq    READ_MARTH (idx 19)   ; (19-6)*4 = 0x34
    0x2C090005,    # 7   cmpwi  r9, 5
    0x4181002C,    # 8   bgt    READ_MARTH (idx 19)   ; (19-8)*4 = 0x2C
    # 9..15: state in 1..5 -- advance sequence (with shine input on 3..5)
    0x2C090003,    # 9   cmpwi  r9, 3
    0x41800018,    # 10  blt    INC_ONLY (idx 16)     ; (16-10)*4 = 0x18
    0xA0190000,    # 11  lhz    r0, 0(r25)            ; load PADStatus buttons
    0x60000200,    # 12  ori    r0, r0, 0x200         ; B
    0xB0190000,    # 13  sth    r0, 0(r25)
    0x38000081,    # 14  li     r0, 0x81              ; -127 as int8
    0x98190003,    # 15  stb    r0, 3(r25)            ; stickY
    # 16..18: increment + return (shared between shine and no-shine paths)
    0x39290001,    # 16  addi   r9, r9, 1     [INC_ONLY]
    0x992B0000,    # 17  stb    r9, 0(r11)
    0x48000068,    # 18  b      END (idx 44)          ; (44-18)*4 = 0x68
    # 19..27: READ_MARTH -- pointer chain to action state
    0x3D808045,    # 19  lis    r12, 0x8045    [READ_MARTH]
    0x618C3130,    # 20  ori    r12, r12, 0x3130
    0x818C0000,    # 21  lwz    r12, 0(r12)
    0x2C0C0000,    # 22  cmpwi  r12, 0
    0x41820054,    # 23  beq    END                   ; (44-23)*4 = 0x54
    0x818C002C,    # 24  lwz    r12, 0x2C(r12)
    0x2C0C0000,    # 25  cmpwi  r12, 0
    0x41820048,    # 26  beq    END                   ; (44-26)*4 = 0x48
    0xA00C0012,    # 27  lhz    r0, 0x12(r12)         ; action state low half
    # 28..31: range-check 0xD4..0xD6
    0x280000D4,    # 28  cmplwi r0, 0xD4
    0x4180002C,    # 29  blt    NOT_GRAB (idx 40)     ; (40-29)*4 = 0x2C
    0x280000D6,    # 30  cmplwi r0, 0xD6
    0x41810024,    # 31  bgt    NOT_GRAB (idx 40)     ; (40-31)*4 = 0x24
    # 32..38: IN_GRAB -- arm if state==0, else stay
    0x2C090000,    # 32  cmpwi  r9, 0
    0x4082002C,    # 33  bne    END                   ; (44-33)*4 = 0x2C
    0x38000001,    # 34  li     r0, 1
    0x980B0000,    # 35  stb    r0, 0(r11)            ; state = 1
    0xA0190000,    # 36  lhz    r0, 0(r25)
    0x60000800,    # 37  ori    r0, r0, 0x800         ; Y
    0xB0190000,    # 38  sth    r0, 0(r25)
    0x48000014,    # 39  b      END                   ; (44-39)*4 = 0x14
    # 40..43: NOT_GRAB -- reset state to 0 if it had advanced
    0x2C090000,    # 40  cmpwi  r9, 0          [NOT_GRAB]
    0x4182000C,    # 41  beq    END                   ; (44-41)*4 = 0x0C
    0x38000000,    # 42  li     r0, 0
    0x980B0000,    # 43  stb    r0, 0(r11)            ; state = 0
    # 44 = END = displaced + branch-back (auto-appended)
]
assert len(LOGIC) == 44
