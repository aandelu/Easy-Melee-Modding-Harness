# Lessons from building the auto-L-cancel macro

Things that cost time during the 2026-05-17 session. Future agents: read this
before touching `auto_lcancel/`.

---

## 1. dme writes to `0x804C1FAC` (CONTROLLER_DIGITAL) do not propagate to the engine

The CSV-documented "processed controller digital data" region looks like a
juicy place to write pad inputs. It isn't. Dolphin's input pipeline rewrites
that region every controller poll with the real hardware controller state.
A Python dme-write to set the L bit gets clobbered within milliseconds —
empirically, the player-data "Frames Since L/R Pressed" timer never moves
from its idle value when we set the L bit this way.

**The harness's `set_digital_buttons` docstring already says this** (line
~857 of `melee_harness.py`): "this races Dolphin's input pipeline, which
rewrites the controller region every poll." Read it. The fix is the
PadRead-hook pattern below.

I lost ~30 min building `discover_lcancel.py` test C around the assumption
that writes here would stick. They don't.

---

## 2. Forcing action states does not apply physics

Writing the action state word at Player Data +0x10 transitions the animation
but **does not give Fox velocity / position changes**. Forcing `KneeBend`
(0x18) plays the jumpsquat animation but doesn't launch him; forcing
`AttackAirN` (0x41) plays nair frames but he stays grounded → never lands
during an aerial → no `LandingAirN` (0x46) → can't observe L-cancel.

The fact that `scenario.force_action_state` works for *Marth's Catch* trigger
in JC-shine is misleading: Catch is a self-contained animation. Aerials need
the airborne flag + velocity which come from a real input event, not from
the state field.

The working path is to inject the controller input via a **PadRead hook**
(see #4) and let the engine apply physics naturally.

---

## 3. `Char_Data + 0x2354` ("Landing Lag Divisor") is NOT the L-cancel observable

The address sheet describes 0x2354 as the field set to 2.0 on a successful
L-cancel. Empirically that's wrong (or describes something we never see).
On Fox NAIR the field reads `0.96f` whether or not the cancel succeeded.

**The real observable is landing-state duration.** Count consecutive frames
where action state is in `[0x46..0x4A]` (LandingAir{N,F,B,Hi,Lw}):

- No L-cancel:    15 frames (Fox NAIR)
- L-cancel:        7 frames

That ~2x reduction matches the PlCo 2.0 multiplier exactly. Trust the
duration, not the divisor field.

Same skepticism applies to `0x2358` "Act out of Landing Flag" — the CSV says
0 or 1; we read 63 (0x3F). Unclear what that field actually is; we got the
answer we needed from duration without resolving it.

---

## 4. PadRead hook at 0x803775B8 is the only working input path

`HSD_PadRead` is called once per port per frame, just before the engine reads
the buttons. Hook the vanilla `lhz r0, 0(r25)` at `0x803775B8` (= `0xA0190000`)
and modify the pad buffer at `(r25)` before letting the displaced original
re-read it. The JC-shine macro (`candidate_d2.py`) does exactly this.

Register state at the hook:
- `r24` = 0-indexed port (0=P1, 1=P2, ...) — the loop iterator from the
  caller's port loop, preserved across the call.
- `r25` = pointer to the per-port PADStatus struct.

Pad-buttons layout at `0(r25)` is **16-bit** (not the 32-bit layout from
`Global_Addresses.csv`):
- A=0x0100, B=0x0200, X=0x0400, Y=0x0800, Start=0x1000
- L=0x0040, R=0x0020, Z=0x0010
- D-pad U=0x0008, D=0x0004, R=0x0002, L=0x0001

Stick X = byte at `2(r25)`, stick Y = byte at `3(r25)` (signed, -127..127).

---

## 5. Runtime patches are wiped by savestate loads

`seed_snapshot()` calls `load_savestate(slot=2)`, which restores MEM1 from
the savestate. **Any PPC instruction patches installed before `seed_snapshot`
are erased.** Similarly, `load_savestate` between trials erases runtime
patches installed earlier.

Rules:
- Install runtime PadRead hooks AFTER `seed_snapshot` finishes.
- Don't `load_savestate` between trials if you want the hook to persist.
- Use in-game cycling (e.g. the hook auto-presses X when Fox is in Wait, so
  he loops jump→nair→land→Wait forever) to drive repeated trials without
  reloading.

The first rig run was completely silent — Fox stayed in Wait — because the
hook had been wiped. I lost ~20 min on this before realizing.

The boot-time gecko path (`install_gecko_c2` → `GameSettings/GALE01r2.ini`)
**does** survive savestate loads because Slippi's codehandler reinstalls
codes after each load. That's why meta-flush, which is installed at boot,
stays alive across `seed_snapshot`.

---

## 6. Dolphin doesn't SIGTERM down fast — use SIGKILL and wait

`pkill -x Dolphin` (without `-9`) returns exit 0 immediately but the process
takes seconds to actually die. If the harness then launches a new instance
right after, **dme.hook() can attach to the dying old process** and every
read fails with `RuntimeError: Could not read memory at <addr>`.

Use `pkill -9 -x Dolphin` and then poll `pgrep -x Dolphin` until empty.
See `kill_stale_dolphins()` in `lcancel_rig.py` for the pattern.

---

## 7. The action state at PadRead is the PREVIOUS frame's state

PadRead runs early in the frame; the engine then uses the read buttons to
update Fox's state for THIS frame. So at the hook, `Player Data +0x10`
holds last frame's state, not what's about to happen.

In practice this only matters for one-frame transitions: e.g. if you only
inject A when state == `JumpF`, the inject happens the frame AFTER Fox
entered JumpF. The engine on that frame still sees A pressed (rising edge
since you weren't pressing A in KneeBend) and starts nair the frame after.
So the macro feels delayed by one frame but works.

---

## 8. PadRead hook fires once per port per frame

The hook-fire counter advanced by 2 per game frame in our setup (P1 Marth +
P2 Fox both being processed). With 4 active ports it would be 4 per frame.
Use `cmpwi r24, <port>` to restrict your logic to the target port — without
the guard, your modifications run on every port and you'll clobber Marth's
inputs while trying to drive Fox.

---

## 9. Verbose-on-state-change leaves you blind when state doesn't change

The first version of `watch_for_landing` only printed when state changed or
was in an "interesting" set. When the rig hook was wiped (lesson #5), state
stayed at 0x000E forever and the script printed exactly one line (`i=0
state=0x000E`). I assumed the hook wasn't firing rather than "the state
literally never changed because the hook isn't installed."

Mitigation: always print a heartbeat every N frames as well, and include a
hook-fire counter that you increment unconditionally in the hook prologue.
The fire counter ground-truths "is the hook actually being executed?"
separately from "is its effect visible?"

---

## 10. macOS env var for keystone

`import keystone` needs `DYLD_LIBRARY_PATH=/opt/homebrew/lib` on this
machine. Every script that imports keystone should be invoked with that env
var, e.g.:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/lcancel_rig.py
```

The existing `verify_v2_with_keystone.py` documents this at the top — match
that convention.

---

## 11. The `r0`-as-`rA` PPC trap

In `lis`, `addi`, `addis`, `stmw`, and any load/store with a base register
field, `rA = 0` reads as the literal value 0, **not** the contents of GPR0.
`addi r0, r0, 16` computes `16`, not `r0 + 16`. Use r3..r12 (volatile) as
base registers in your hook. `instr_writer.py` and `bp.py` both call this
out. Keystone won't warn you.

---

## Worth keeping for next time

- The `auto_lcancel/lcancel_rig.py` design (combined driver + L toggle gated
  by a scratch flag, with hook-fire counter and per-frame verbose dump) is
  the right template for any "does this hook actually do what I think"
  exercise. Copy and adapt rather than starting from scratch.
- `keystone` over hand-encoded PPC. Always.
- `finalize_payload(logic, hook_addr, cave_addr, expected_orig)` from
  `melee_harness.py` is the cleanest way to append the displaced original +
  branch back when installing runtime hooks via `instr_writer.write_instrs`.
