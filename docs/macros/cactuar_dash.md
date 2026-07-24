# Cactuar Dash

Skips Melee's slow run-turnaround by converting a stick-slam reversal into a crouch →
instant dash-back. Shared addresses/offsets: [`../REFERENCE.md`](../REFERENCE.md).
Dev loop: [`../../WORKFLOW.md`](../../WORKFLOW.md). Status board: [`../STATUS.md`](../STATUS.md).

## Mechanic + constants

- **Problem:** reversing out of a run goes through `TurnRun 0x13` (~20–30f).
- **Trigger:** local player in **Run `0x15`** or **RunBrake `0x17`** AND the stick slammed
  to the extreme opposite of facing: `|stickX| >= 0x60` opposite the facing sign,
  `|stickY| <= 0x18`. Facing = Player Data `+0x2C` (float; sign = direction).
  **Dash `0x14` is deliberately excluded** — you can't crouch out of a dash; this is why
  a reversal in the last ~2f of a dash can still slip into TurnRun.
- **Action:** **veto** the turnaround input and substitute a crouch (stickX=0, stickY=full
  down) held through `Squat 0x27` (a 7f animation floor) into `SquatWait 0x28`; on release
  the player's still-held opposite stick dashes them out the new direction.
  Net: TurnRun 30f → 0f, replaced by ~7f of squat.
- **Delay compensation (the crux):** the dash-out must land on the **first actionable
  frame** out of squat — too early and the rising edge is eaten during non-actionable
  Squat (no dash); too late wastes SquatWait frames. Input delay differs per machine, so
  the cave reads **`ODB_DELAY_FRAMES` (ODB `+0x21`)** at runtime and releases when the
  squat frame counter `+0x894` reaches **`6 − delay`** (frame 5 on a 1-frame connection,
  4 on 2-frame). `+0x894` is a float resetting to 1.0 per state, decoded to int without
  FPU. **K=6** was calibrated on the 1-frame dev machine only; it is the one tunable
  (edit the `subfic 9, 9, 6` in the generator and regenerate).

## Hook / cave logic

Single producer-side hook **`0x8034E680`** (inside PAD_Read after stick calibration,
upstream of the EXI scrape → netplay-safe). Displaced original `lbz r0,7(r3)` =
`0x88030007`; preserve r3 (calib ptr), r4 (PADStatus), r13. Stick override = write
`2(r4)`=X, `3(r4)`=Y (signed bytes, +X=right, −Y=down). Local player resolved via ODB
(`port = *(*(r13-0x49E4)+0)` → GObj `0x80453130 + port*0xE90` → Player Data `+0x2C`);
every pointer MEM1-checked. Two phases per frame: HOLD (in Squat, keep crouch until the
delay-comp threshold) and TRIGGER (in Run/RunBrake with a reversal slam, write the
crouch). Works as P1 or P2.

## Files

| File | Role |
| --- | --- |
| `make_cactuar_dash_gecko.py` | Generator (keystone build + capstone verify; runtime delay-comp cave). |
| `online_cactuar_dash.gecko.txt` | C2 gecko — **known to silently fail via the real Slippi user install path** (see status). |
| `online_cactuar_dash.raw.gecko.txt` | RAW (06+04) form — the identified fix; **never user-tested**. |

The offline probe/macro scripts and online test rigs were deleted in the 2026-07-24
cleanup (git history); to re-validate, regenerate the cave from the generator and drive
it through the harness dev loop. Narrative history: `docs/archive/`.

## Current status

**WIP — validated but not deliverable yet.**

- **Offline: validated.** A/B on slot 2: `TurnRun 0x13` 30f → 0f, replaced by
  `Squat 0x27` ×7 → dash, facing flips. (Dev script deleted; result reproducible from the
  generator's cave.)
- **Online: validated via runtime injection** (dme + meta-flush install on the harness
  machine): **65/65 run-reversals → crouch, 0 TurnRun, 0 desync**, delay-comp release
  confirmed at squat frame 5 on the 1-frame-delay connection.
- **But the user-installable C2 gecko silently fails in real Slippi** — the user-added-code
  append-space limit means the code never actually installs (no error, no effect). The
  identified fix — ship the merged/RAW gecko form instead — **was never executed**: the
  RAW file exists but has never been taken through the real user install path.

## Open items

1. **Ship the merged/RAW gecko** through the real Slippi install path (user adds it in
   Slippi Manager) and verify it actually fires — the top blocker.
2. **2-frame-delay validation** in real play. The runtime `6 − delay` threshold should
   self-correct, but K=6 was only ever confirmed at 1-frame delay.
3. **~1/49 TurnRun slip:** a reversal landing inside `Dash 0x14` (excluded) or at the
   Dash→Run boundary still turns around slowly. If it bothers real play, widen the trigger
   carefully (an early-dash crouch is impossible, so the exclusion can't just be dropped).
