"""Diagnostic probe: read Marth's action state via the gecko's pointer
chain and stash to scratch 0x803FA428 (half-word) so we can inspect it
from Python via dme. No state machine, no inputs written.

If after force_action_state(h, 1, 0xD4), the scratch half-word reads back
as 0x00D4, the gecko's chain works. If it reads as 0 (or stale), the
chain is broken.
"""

HOOK_ADDR = 0x803775B8
DISPLACED_ORIG = 0xA0190000
NAME = "candidate-d-probe-action-state"

PROBE_ADDR = 0x803FA428

LOGIC = [
    0x2C180001,  # 0  cmpwi  r24, 1
    0x40820034,  # 1  bne    END           (+0x34, 13 instr ahead)
    0x3D808045,  # 2  lis    r12, 0x8045
    0x618C3130,  # 3  ori    r12, r12, 0x3130
    0x818C0000,  # 4  lwz    r12, 0(r12)
    0x2C0C0000,  # 5  cmpwi  r12, 0
    0x41820020,  # 6  beq    END           (+0x20, 8 instr ahead)
    0x818C002C,  # 7  lwz    r12, 0x2C(r12)
    0x2C0C0000,  # 8  cmpwi  r12, 0
    0x41820014,  # 9  beq    END           (+0x14, 5 instr ahead)
    0xA00C0012,  # 10 lhz    r0, 0x12(r12)
    0x3D60803F,  # 11 lis    r11, 0x803F
    0x616BA428,  # 12 ori    r11, r11, 0xA428
    0xB00B0000,  # 13 sth    r0, 0(r11)
    # pos 14 = END = displaced
]
