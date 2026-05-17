"""Phase-A netplay-safe macro: Fox (P2) jumps the first frame Marth (P1)
enters a grab action state.

Compared to B.3 (the offline JC-shine):
  - replaces the dme MARTH_Z_FLAG_ADDR trigger with a read of Marth's
    Player Data action-state field (the netplay-safe channel).
  - removes Marth's port logic entirely -- on netplay we MUST NOT spoof
    the opponent's PADStatus.
  - drops the shine (counter 3..5 logic) -- this is the bare "jump on
    grab" foundation; the shine is added in candidate_d2.py once this
    passes.

State machine on Fox's port pass:
  counter == 0, Marth in {Catch, CatchDash, CatchTurn} (0xD4..0xD6):
      counter = 1
      press Y (jump)
  counter == 0 otherwise:
      do nothing
  counter != 0:
      do nothing (already fired; the savestate reset re-zeroes the counter
      between trials)

Encoding bug from the previous-session attempt (candidate_d.py idx 4):
0x896B0000 was labeled `lbz r9, 0(r11)` but actually decodes as
`lbz r11, 0(r11)` -- it clobbered the pointer with the counter value, so
every register-state assumption further down was wrong. The bne at idx 6
saw r9 still holding pre-hook garbage and (almost always) took the
COUNTER_NZ branch instead of the arm path. Correct encoding is 0x892B0000.

Hook:  0x803775B8  (HSD_PadRead, BEFORE the buttons read)
Orig:  0xA0190000  (lhz r0, 0(r25))

Register usage:
  r9        = counter (only used inside this macro)
  r11       = pointer to counter byte (0x803FA424)
  r12       = scratch (GObj ptr -> Player Data ptr)
  r0        = scratch (action state read; buttons OR-in)
  r24, r25  = port + PADStatus ptr (read by hook, preserved by us)
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "jump-on-grab"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    # idx 0..1: only act on Fox's port (r24 == 1)
    0x2C180001,    # 0   cmpwi  r24, 1
    0x40820060,    # 1   bne    END         (target idx 25, offset 0x60)
    # idx 2..4: read counter byte
    0x3D60803F,    # 2   lis    r11, 0x803F
    0x616BA424,    # 3   ori    r11, r11, 0xA424
    0x892B0000,    # 4   lbz    r9, 0(r11)   ; <-- BUG-FIXED encoding
    # idx 5..6: skip if already fired (counter != 0)
    0x2C090000,    # 5   cmpwi  r9, 0
    0x4082004C,    # 6   bne    END         (offset (25-6)*4 = 0x4C)
    # idx 7..9: read P1 GObj ptr at 0x80453130
    0x3D808045,    # 7   lis    r12, 0x8045
    0x618C3130,    # 8   ori    r12, r12, 0x3130
    0x818C0000,    # 9   lwz    r12, 0(r12)
    0x2C0C0000,    # 10  cmpwi  r12, 0
    0x41820038,    # 11  beq    END         (offset (25-11)*4 = 0x38)
    # idx 12..14: dereference to Player Data ptr
    0x818C002C,    # 12  lwz    r12, 0x2C(r12)
    0x2C0C0000,    # 13  cmpwi  r12, 0
    0x4182002C,    # 14  beq    END         (offset (25-14)*4 = 0x2C)
    # idx 15..19: read action-state low halfword + range-check 0xD4..0xD6
    0xA00C0012,    # 15  lhz    r0, 0x12(r12)
    0x280000D4,    # 16  cmplwi r0, 0xD4
    0x41800020,    # 17  blt    END         (offset (25-17)*4 = 0x20)
    0x280000D6,    # 18  cmplwi r0, 0xD6
    0x41810018,    # 19  bgt    END         (offset (25-19)*4 = 0x18)
    # idx 20..24: arm -- counter=1, OR Y bit into Fox PADStatus halfword
    0x38000001,    # 20  li     r0, 1
    0x980B0000,    # 21  stb    r0, 0(r11)
    0xA0190000,    # 22  lhz    r0, 0(r25)
    0x60000800,    # 23  ori    r0, r0, 0x800  ; Y bit
    0xB0190000,    # 24  sth    r0, 0(r25)
    # idx 25 = END = displaced original (auto-appended) + branch back
]
assert len(LOGIC) == 25
