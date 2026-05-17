"""Candidate B (revised): jump-cancelled shine via per-frame state machine.

B.1 (initial form, kept in git history) was a flag-gated single-Y press to
confirm that Y on Fox's PADStatus -> KneeBend (0x0018). Empirical jumpsquat
duration was exactly 3 frames; Fox transitions to JumpF (0x0019) on frame 4
post-trigger.

B.2 (this file) extends that into the full JC-shine sequence using a counter
byte at 0x803FA424.

State machine (driven by counter, read each Fox-port pad pass; Marth port
behavior is unchanged from B.1):

  counter == 0 AND flag set   -> press Y on Fox, counter = 1
  counter == 0 AND flag clear -> idle, counter stays 0
  counter == 1..3             -> no input, counter += 1 (Fox is in KneeBend)
  counter == 4..6             -> press B + stickY=-127 on Fox, counter += 1
  counter == 7..255           -> terminal (no input, counter stays)

The macro auto-completes after a single trigger -- harness can hold flag for
fewer frames than the state machine runs, and once counter > 0 the flag is
ignored. h.reset() (F1 load slot 1) restores counter to 0 between trials.

Why counter==4 fires B+down (not counter==3): empirically Fox enters
KneeBend on the frame the Y press is consumed, stays for 3 frames, and
transitions to JumpF on frame 4. Pressing B+down on frame 4 (counter==4)
puts the shine input into Fox's PADStatus on the first airborne frame,
which is the classic JC-shine timing.

Hook:  0x803775B8 (HSD_PadRead, BEFORE PADStatus.buttons read)
Orig:  lhz r0, 0(r25)  ->  0xA0190000

Register usage inside the gecko body:
  r9  = counter (loaded once near top)
  r10 = flag    (loaded once near top)
  r11 = pointer to flag    addr 0x803FB000
  r12 = pointer to counter addr 0x803FA424
  r24 = pad port index (set by HSD_PadRead caller; preserved by us)
  r25 = ptr to current port's PADStatus (set by caller; preserved)

Layout (position -> mnemonic). Labels in CAPS. Displaced original is
appended by gecko_c2_lines() at position 40 and is the implicit END label.

   0  lis   r11, 0x803F
   1  ori   r11, r11, 0xB000
   2  lbz   r10, 0(r11)        ; r10 = flag
   3  lis   r12, 0x803F
   4  ori   r12, r12, 0xA424
   5  lbz   r9,  0(r12)        ; r9 = counter
   6  cmpwi r24, 0
   7  bne   PORT1_CHECK (+0x1C)
   8  cmpwi r10, 0
   9  beq   END         (+0x7C); Marth port, flag clear -> end
  10  lhz   r0,  0(r25)
  11  ori   r0,  r0, 0x10      ; Z
  12  sth   r0,  0(r25)
  13  b     END         (+0x6C)
  14  cmpwi r24, 1              ; PORT1_CHECK
  15  bne   END         (+0x64); not port 1 -> end
  16  cmpwi r9,  0
  17  bne   COUNTER_NZ  (+0x24)
  18  cmpwi r10, 0
  19  beq   END         (+0x54); flag clear -> idle, leave counter 0
  20  li    r0,  1
  21  stb   r0,  0(r12)        ; counter = 1
  22  lhz   r0,  0(r25)
  23  ori   r0,  r0, 0x800     ; Y
  24  sth   r0,  0(r25)
  25  b     END         (+0x3C)
  26  cmpwi r9,  4              ; COUNTER_NZ
  27  blt   NO_INPUT_INC (+0x2C); counter in 1..3
  28  cmpwi r9,  7
  29  bge   END         (+0x2C); counter >= 7 -> terminal
  30  lhz   r0,  0(r25)
  31  ori   r0,  r0, 0x200     ; B
  32  sth   r0,  0(r25)
  33  li    r0,  0x81          ; -127
  34  stb   r0,  3(r25)        ; stickY
  35  addi  r9,  r9, 1
  36  stb   r9,  0(r12)
  37  b     END         (+0xC)
  38  addi  r9,  r9, 1          ; NO_INPUT_INC
  39  stb   r9,  0(r12)
  40  lhz   r0,  0(r25)        ; END / displaced (appended)
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000     # lhz r0, 0(r25)
NAME = "candidate-b2-jc-shine"

# Counter byte sits within the debug-menu free-memory region, well clear of
# both the gecko cave body and the Z flag at 0x803FB000.
COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x3D60803F,  # 0  lis   r11, 0x803F
    0x616BB000,  # 1  ori   r11, r11, 0xB000
    0x894B0000,  # 2  lbz   r10, 0(r11)
    0x3D80803F,  # 3  lis   r12, 0x803F
    0x618CA424,  # 4  ori   r12, r12, 0xA424
    0x892C0000,  # 5  lbz   r9,  0(r12)
    0x2C180000,  # 6  cmpwi r24, 0
    0x4082001C,  # 7  bne   PORT1_CHECK   (+0x1C)
    0x2C0A0000,  # 8  cmpwi r10, 0
    0x4182007C,  # 9  beq   END           (+0x7C)
    0xA0190000,  # 10 lhz   r0, 0(r25)
    0x60000010,  # 11 ori   r0, r0, 0x10
    0xB0190000,  # 12 sth   r0, 0(r25)
    0x4800006C,  # 13 b     END           (+0x6C)
    0x2C180001,  # 14 cmpwi r24, 1        ; PORT1_CHECK
    0x40820064,  # 15 bne   END           (+0x64)
    0x2C090000,  # 16 cmpwi r9, 0
    0x40820024,  # 17 bne   COUNTER_NZ    (+0x24)
    0x2C0A0000,  # 18 cmpwi r10, 0
    0x41820054,  # 19 beq   END           (+0x54)
    0x38000001,  # 20 li    r0, 1
    0x980C0000,  # 21 stb   r0, 0(r12)
    0xA0190000,  # 22 lhz   r0, 0(r25)
    0x60000800,  # 23 ori   r0, r0, 0x800
    0xB0190000,  # 24 sth   r0, 0(r25)
    0x4800003C,  # 25 b     END           (+0x3C)
    0x2C090004,  # 26 cmpwi r9, 4         ; COUNTER_NZ
    0x4180002C,  # 27 blt   NO_INPUT_INC  (+0x2C)
    0x2C090007,  # 28 cmpwi r9, 7
    0x4080002C,  # 29 bge   END           (+0x2C)
    0xA0190000,  # 30 lhz   r0, 0(r25)
    0x60000200,  # 31 ori   r0, r0, 0x200
    0xB0190000,  # 32 sth   r0, 0(r25)
    0x38000081,  # 33 li    r0, 0x81
    0x98190003,  # 34 stb   r0, 3(r25)
    0x39290001,  # 35 addi  r9, r9, 1
    0x992C0000,  # 36 stb   r9, 0(r12)
    0x4800000C,  # 37 b     END           (+0xC)
    0x39290001,  # 38 addi  r9, r9, 1     ; NO_INPUT_INC
    0x992C0000,  # 39 stb   r9, 0(r12)
]
