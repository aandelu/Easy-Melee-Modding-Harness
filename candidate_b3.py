"""Candidate B.3: shine input shifted one frame earlier vs B.2.

Identical to candidate_b.py except the shine window is counter 3..5 (not
4..6). The intent: in the canonical JC-shine technique, B+down is buffered
DURING jumpsquat so the engine transitions KneeBend -> aerial shine
directly, without ever passing through JumpF (0x0019).

B.2 empirical run showed Fox in JumpF for exactly 1 frame between KneeBend
and aerial shine startup -- a stale-by-1 JC. If B.3 eliminates that JumpF
frame, the macro is producing canonical JC-shine output.

The only logic change vs candidate_b.py:
  pos 26: cmpwi r9, 4   ->   cmpwi r9, 3   (encoding 0x2C090003)
  pos 28: cmpwi r9, 7   ->   cmpwi r9, 6   (encoding 0x2C090006)

State machine becomes:
  counter 0:           idle / start (Y press if flag set)
  counter 1..2:        no input, Fox in KneeBend (jumpsquat frame 1..2)
  counter 3..5:        write B + stickY=-127 (shine input buffered during
                       last jumpsquat frame + first 2 airborne frames)
  counter 6..255:      terminal

Hook + register usage + counter address unchanged from candidate_b.py.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-b3-jc-shine-early"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x3D60803F,  # 0  lis   r11, 0x803F
    0x616BB000,  # 1  ori   r11, r11, 0xB000
    0x894B0000,  # 2  lbz   r10, 0(r11)
    0x3D80803F,  # 3  lis   r12, 0x803F
    0x618CA424,  # 4  ori   r12, r12, 0xA424
    0x892C0000,  # 5  lbz   r9,  0(r12)
    0x2C180000,  # 6  cmpwi r24, 0
    0x4082001C,  # 7  bne   PORT1_CHECK
    0x2C0A0000,  # 8  cmpwi r10, 0
    0x4182007C,  # 9  beq   END
    0xA0190000,  # 10 lhz   r0, 0(r25)
    0x60000010,  # 11 ori   r0, r0, 0x10
    0xB0190000,  # 12 sth   r0, 0(r25)
    0x4800006C,  # 13 b     END
    0x2C180001,  # 14 cmpwi r24, 1
    0x40820064,  # 15 bne   END
    0x2C090000,  # 16 cmpwi r9, 0
    0x40820024,  # 17 bne   COUNTER_NZ
    0x2C0A0000,  # 18 cmpwi r10, 0
    0x41820054,  # 19 beq   END
    0x38000001,  # 20 li    r0, 1
    0x980C0000,  # 21 stb   r0, 0(r12)
    0xA0190000,  # 22 lhz   r0, 0(r25)
    0x60000800,  # 23 ori   r0, r0, 0x800
    0xB0190000,  # 24 sth   r0, 0(r25)
    0x4800003C,  # 25 b     END
    0x2C090003,  # 26 cmpwi r9, 3         <-- was 4 in B.2
    0x4180002C,  # 27 blt   NO_INPUT_INC
    0x2C090006,  # 28 cmpwi r9, 6         <-- was 7 in B.2
    0x4080002C,  # 29 bge   END
    0xA0190000,  # 30 lhz   r0, 0(r25)
    0x60000200,  # 31 ori   r0, r0, 0x200
    0xB0190000,  # 32 sth   r0, 0(r25)
    0x38000081,  # 33 li    r0, 0x81
    0x98190003,  # 34 stb   r0, 3(r25)
    0x39290001,  # 35 addi  r9, r9, 1
    0x992C0000,  # 36 stb   r9, 0(r12)
    0x4800000C,  # 37 b     END
    0x39290001,  # 38 addi  r9, r9, 1
    0x992C0000,  # 39 stb   r9, 0(r12)
]
