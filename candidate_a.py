"""Candidate A: dme-gated input driver at 0x803775C0 (smoke test).

When the dme-controlled flag byte at MARTH_Z_FLAG_ADDR is non-zero, the
gecko fires BOTH legs in the same pad-process frame:
  - On Marth's pad pass (r24 == 0), ORs the Z bit into Marth's raw
    PADStatus.buttons.
  - On Fox's pad pass  (r24 == 1), ORs the B bit and writes -127 to Fox's
    stickY.

When the flag is zero, the gecko is a no-op. This was a load-bearing
correction: an earlier version keyed Fox's leg on Marth's raw PADStatus Z
bit (reading the opponent port's buttons at -12(r25)). The hardware
PADStatus can already have Z set independently of our flag (residual
controller state in the savestate), so Fox kept crouching on every
restore_snapshot. Gating both legs on the dme flag makes the macro
inert at rest, so the harness can sample baselines cleanly.

The same-frame guarantee still holds: when flag=1, port 0 fires Marth's
leg and port 1 fires Fox's leg within one pad-process loop iteration --
both writes land before Melee's engine reads either character's input.

Hook:  0x803775B8 (HSD_PadRead, JUST BEFORE the buttons read)
Orig:  lhz r0, 0(r25)  ->  0xA0190000

Disassembling around 0x803775C0 showed:
  0x803775B8: 0xA0190000  ; lhz r0, 0(r25)     -- LOAD PADStatus.buttons
  0x803775BC: 0x901A0000  ; stw r0, 0(r26)     -- STORE to local struct
  0x803775C0: 0x88190002  ; lbz r0, 2(r25)     -- load stickX  (was the old hook)
  ...                                          ; further byte-by-byte copies

The earlier candidate hooked at 0x803775C0 -- AFTER the buttons read -- so
modifying PADStatus.buttons there had no effect (the engine already had the
old value in r0). Stick writes worked because stickX/stickY are read AFTER
0x803775C0. Hooking at 0x803775B8 puts us BEFORE the buttons read; our
modification of r25's buttons is then picked up by the displaced lhz.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000     # lhz r0, 0(r25)
NAME = "candidate-a-merged"

# Layout (position -> mnemonic). The displaced original is appended by
# gecko_c2_lines() at position 18 and is the implicit "end" label.
#    0  lis   r11, 0x803F
#    1  ori   r11, r11, 0xB000
#    2  lbz   r0, 0(r11)
#    3  cmpwi r0, 0
#    4  beq   end           (+0x38)   <- flag clear -> no-op
#    5  cmpwi r24, 0
#    6  bne   port1_check   (+0x14)
#    7  lhz   r0, 0(r25)
#    8  ori   r0, r0, 0x10  (Z)
#    9  sth   r0, 0(r25)
#   10  b     end           (+0x20)
#   11  cmpwi r24, 1        <-- port1_check
#   12  bne   end           (+0x18)
#   13  lhz   r0, 0(r25)
#   14  ori   r0, r0, 0x200 (B)
#   15  sth   r0, 0(r25)
#   16  li    r0, 0x81      (-127)
#   17  stb   r0, 3(r25)    (stickY)
#   18  lbz   r0, 2(r25)    <-- end / displaced (appended)
LOGIC = [
    0x3D60803F,
    0x616BB000,
    0x880B0000,
    0x2C000000,
    0x41820038,
    0x2C180000,
    0x40820014,
    0xA0190000,
    0x60000010,
    0xB0190000,
    0x48000020,
    0x2C180001,
    0x40820018,
    0xA0190000,
    0x60000200,
    0xB0190000,
    0x38000081,
    0x98190003,
]
