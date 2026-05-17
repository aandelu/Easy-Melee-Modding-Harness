"""Candidate D.1: action-state-keyed JC-shine.

Replaces B.3's dme-flag trigger with a read of Marth's action state. The
gecko fires the JC-shine sequence when Marth (port 0, via the P1 GObj at
0x80453130) has action state in {0xD4 Catch, 0xD5 CatchDash, 0xD6
CatchTurn}.

Why this is the netplay path: in online play the macro can't read the
opponent's raw PADStatus (Marth's controller buttons aren't local input);
it can only read game state. Action states are part of the synced game
state, so reading them is safe.

Caveat documented in MACRO_PLAN section 4D: action-state-keyed triggers
fire AFTER the engine has updated Marth's state for the frame, so they
react 1 frame later than the offline button-keyed trigger (B.3).
Specifically:
  - B.3: trigger frame T -> Fox in KneeBend at frame T+1 (same frame as
    Marth in Catch).
  - D.1: Marth in Catch first visible at frame T -> Fox's pad pass at
    frame T+1 reads the Catch state -> Fox in KneeBend at frame T+2.
The extra frame is structural.

D.1 hardcodes r24==1 (Fox is port 2 / index 1) for offline testing. D.2
will add the Slippi online check + dynamic local-port read.

Hook:   0x803775B8 (same as B/B.3)
Orig:   lhz r0, 0(r25)  ->  0xA0190000

State machine is identical to B.3 once triggered (counter at 0x803FA424,
shine input on counter 3..5 to skip JumpF). The trigger condition differs:
B.3 fires on dme flag, D.1 fires on Marth's action state.

Register usage:
  r9  = counter
  r11 = pointer to counter byte (0x803FA424)
  r12 = scratch (GObj ptr -> Player Data ptr)
  r0  = scratch (action state, then buttons)
  r24, r25 = port + PADStatus ptr (preserved by us)

Layout (positions 0..39 + displaced at 40):
   0  cmpwi  r24, 1
   1  bne    END                (+0x9C)
   2  lis    r11, 0x803F
   3  ori    r11, r11, 0xA424
   4  lbz    r9,  0(r11)
   5  cmpwi  r9, 0
   6  bne    COUNTER_NZ         (+0x50)
   7  lis    r12, 0x8045
   8  ori    r12, r12, 0x3130
   9  lwz    r12, 0(r12)        ; r12 = P1 GObj ptr
  10  cmpwi  r12, 0
  11  beq    END                (+0x74)
  12  lwz    r12, 0x2C(r12)     ; r12 = P1 Player Data ptr
  13  cmpwi  r12, 0
  14  beq    END                (+0x68)
  15  lhz    r0,  0x12(r12)     ; low 16 bits of action state word
  16  cmplwi r0,  0xD4
  17  blt    END                (+0x5C)
  18  cmplwi r0,  0xD6
  19  bgt    END                (+0x54)
  20  li     r0,  1
  21  stb    r0,  0(r11)        ; counter = 1
  22  lhz    r0,  0(r25)
  23  ori    r0,  r0, 0x800     ; Y
  24  sth    r0,  0(r25)
  25  b      END                (+0x3C)
  26  cmpwi  r9,  3             ; COUNTER_NZ
  27  blt    NO_INPUT_INC       (+0x2C)
  28  cmpwi  r9,  6
  29  bge    END                (+0x2C)
  30  lhz    r0,  0(r25)
  31  ori    r0,  r0, 0x200     ; B
  32  sth    r0,  0(r25)
  33  li     r0,  0x81          ; -127
  34  stb    r0,  3(r25)        ; stickY
  35  addi   r9,  r9, 1
  36  stb    r9,  0(r11)
  37  b      END                (+0xC)
  38  addi   r9,  r9, 1         ; NO_INPUT_INC
  39  stb    r9,  0(r11)
  40  lhz    r0,  0(r25)        ; END / displaced (appended)
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d1-action-state"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x4082009C,  # 1  bne    END
    0x3D60803F,  # 2  lis    r11, 0x803F
    0x616BA424,  # 3  ori    r11, r11, 0xA424
    0x896B0000,  # 4  lbz    r9, 0(r11)
    0x2C090000,  # 5  cmpwi  r9, 0
    0x40820050,  # 6  bne    COUNTER_NZ
    0x3D808045,  # 7  lis    r12, 0x8045
    0x618C3130,  # 8  ori    r12, r12, 0x3130
    0x818C0000,  # 9  lwz    r12, 0(r12)
    0x2C0C0000,  # 10 cmpwi  r12, 0
    0x41820074,  # 11 beq    END
    0x818C002C,  # 12 lwz    r12, 0x2C(r12)
    0x2C0C0000,  # 13 cmpwi  r12, 0
    0x41820068,  # 14 beq    END
    0xA00C0012,  # 15 lhz    r0, 0x12(r12)
    0x280000D4,  # 16 cmplwi r0, 0xD4
    0x4180005C,  # 17 blt    END
    0x280000D6,  # 18 cmplwi r0, 0xD6
    0x41810054,  # 19 bgt    END
    0x38000001,  # 20 li     r0, 1
    0x980B0000,  # 21 stb    r0, 0(r11)
    0xA0190000,  # 22 lhz    r0, 0(r25)
    0x60000800,  # 23 ori    r0, r0, 0x800
    0xB0190000,  # 24 sth    r0, 0(r25)
    0x4800003C,  # 25 b      END
    0x2C090003,  # 26 cmpwi  r9, 3        ; COUNTER_NZ
    0x4180002C,  # 27 blt    NO_INPUT_INC
    0x2C090006,  # 28 cmpwi  r9, 6
    0x4080002C,  # 29 bge    END
    0xA0190000,  # 30 lhz    r0, 0(r25)
    0x60000200,  # 31 ori    r0, r0, 0x200
    0xB0190000,  # 32 sth    r0, 0(r25)
    0x38000081,  # 33 li     r0, 0x81
    0x98190003,  # 34 stb    r0, 3(r25)
    0x39290001,  # 35 addi   r9, r9, 1
    0x992B0000,  # 36 stb    r9, 0(r11)
    0x4800000C,  # 37 b      END
    0x39290001,  # 38 addi   r9, r9, 1    ; NO_INPUT_INC
    0x992B0000,  # 39 stb    r9, 0(r11)
]
