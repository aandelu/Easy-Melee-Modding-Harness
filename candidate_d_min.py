"""Minimal D.1 variant: only the action-state trigger (no state machine).

If Marth's action state == 0xD4, write 0x42 to the counter byte and stop.
No counter check, no state machine. Diagnostic-only: confirms whether the
action-state comparison can fire end-to-end.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d-min"

COUNTER_ADDR = 0x803FA424

LOGIC = [
    0x2C180001,  # 0  cmpwi r24, 1
    0x40820034,  # 1  bne   END    (+0x34, 13 instr ahead)
    0x3D808045,  # 2  lis   r12, 0x8045
    0x618C3130,  # 3  ori   r12, r12, 0x3130
    0x818C0000,  # 4  lwz   r12, 0(r12)
    0x818C002C,  # 5  lwz   r12, 0x2C(r12)
    0xA00C0012,  # 6  lhz   r0, 0x12(r12)
    0x2C0000D4,  # 7  cmpwi r0, 0xD4
    0x41820008,  # 8  beq   TRIGGER (+0x08)
    0x48000014,  # 9  b     END     (+0x14)
    0x38000042,  # 10 li    r0, 0x42      ; TRIGGER
    0x3D60803F,  # 11 lis   r11, 0x803F
    0x616BA424,  # 12 ori   r11, r11, 0xA424
    0x980B0000,  # 13 stb   r0, 0(r11)
    # pos 14 = END = displaced
]
