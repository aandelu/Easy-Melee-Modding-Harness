"""Self-resetting netplay-safe Frame-1 JC-shine, v2 (menu-safe).

v2 fixes the cause of Dolphin "Invalid read from 0x???????, PC = 0x806?????"
panic alerts that fire on menus / title screens / scene transitions.

Root cause (diagnosed via diag_cave_layout.py + the reported PC):
  *(0x80453130) is the P1 GObj pointer when a match is loaded. On
  non-gameplay screens that pointer can be non-NULL but contain leftover
  garbage like 0x3B33361C (not in MEM1). The previous version only
  NULL-checked it, then dereferenced +0x2C, producing reads of
  unmapped addresses like 0x3B333648.

Fix (6 extra instructions vs candidate_d_standalone):
  After every load of a player-data pointer (P1 GObj at idx 21 and
  P1 Player Data at idx 27), srwi-extract the top byte and require
  it to equal 0x80 (i.e. the pointer is inside MEM1). If not, branch
  to END just like the NULL path.

LOGIC layout grows 44 -> 50 instructions. All END-targeting branch
offsets are recomputed for the new END (idx 50).

State machine, hardcoded port assumptions, and trigger logic are
identical to candidate_d_standalone (see that file's docstring).
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000         # lhz r0, 0(r25)
NAME = "jc-shine-netplay-safe-standalone-v2"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    # 0..4: port gate + load state byte
    0x2C180001,    # 0   cmpwi  r24, 1                ; Fox port (P2) only
    0x408200C4,    # 1   bne    END (idx 50)          ; (50-1)*4 = 0xC4
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
    0xA0190000,    # 11  lhz    r0, 0(r25)            ; buttons
    0x60000200,    # 12  ori    r0, r0, 0x200         ; B
    0xB0190000,    # 13  sth    r0, 0(r25)
    0x38000081,    # 14  li     r0, 0x81              ; -127
    0x98190003,    # 15  stb    r0, 3(r25)            ; stickY
    # 16..18: increment + return
    0x39290001,    # 16  addi   r9, r9, 1     [INC_ONLY]
    0x992B0000,    # 17  stb    r9, 0(r11)
    0x48000080,    # 18  b      END (idx 50)          ; (50-18)*4 = 0x80
    # 19..33: READ_MARTH -- pointer chain with MEM1 range checks
    0x3D808045,    # 19  lis    r12, 0x8045    [READ_MARTH]
    0x618C3130,    # 20  ori    r12, r12, 0x3130
    0x818C0000,    # 21  lwz    r12, 0(r12)           ; r12 = P1 GObj ptr
    0x2C0C0000,    # 22  cmpwi  r12, 0                ; NULL?
    0x4182006C,    # 23  beq    END (idx 50)          ; (50-23)*4 = 0x6C
    0x5580463E,    # 24  srwi   r0, r12, 24           ; top byte of r12
    0x28000080,    # 25  cmplwi r0, 0x80              ; in MEM1?
    0x40820060,    # 26  bne    END (idx 50)          ; (50-26)*4 = 0x60
    0x818C002C,    # 27  lwz    r12, 0x2C(r12)        ; r12 = Player Data ptr
    0x2C0C0000,    # 28  cmpwi  r12, 0                ; NULL?
    0x41820054,    # 29  beq    END (idx 50)          ; (50-29)*4 = 0x54
    0x5580463E,    # 30  srwi   r0, r12, 24           ; top byte again
    0x28000080,    # 31  cmplwi r0, 0x80              ; in MEM1?
    0x40820048,    # 32  bne    END (idx 50)          ; (50-32)*4 = 0x48
    0xA00C0012,    # 33  lhz    r0, 0x12(r12)         ; action state low half
    # 34..37: range-check 0xD4..0xD6 (Catch / CatchDash / CatchTurn)
    0x280000D4,    # 34  cmplwi r0, 0xD4
    0x4180002C,    # 35  blt    NOT_GRAB (idx 46)     ; (46-35)*4 = 0x2C
    0x280000D6,    # 36  cmplwi r0, 0xD6
    0x41810024,    # 37  bgt    NOT_GRAB (idx 46)     ; (46-37)*4 = 0x24
    # 38..45: IN_GRAB -- arm if state==0, else hold
    0x2C090000,    # 38  cmpwi  r9, 0          [IN_GRAB]
    0x4082002C,    # 39  bne    END (idx 50)          ; (50-39)*4 = 0x2C
    0x38000001,    # 40  li     r0, 1
    0x980B0000,    # 41  stb    r0, 0(r11)            ; state = 1
    0xA0190000,    # 42  lhz    r0, 0(r25)
    0x60000800,    # 43  ori    r0, r0, 0x800         ; Y
    0xB0190000,    # 44  sth    r0, 0(r25)
    0x48000014,    # 45  b      END (idx 50)          ; (50-45)*4 = 0x14
    # 46..49: NOT_GRAB -- reset state to 0 if it had advanced
    0x2C090000,    # 46  cmpwi  r9, 0          [NOT_GRAB]
    0x4182000C,    # 47  beq    END (idx 50)          ; (50-47)*4 = 0x0C
    0x38000000,    # 48  li     r0, 0
    0x980B0000,    # 49  stb    r0, 0(r11)            ; state = 0
    # 50 = END = displaced + branch-back (auto-appended)
]
assert len(LOGIC) == 50
