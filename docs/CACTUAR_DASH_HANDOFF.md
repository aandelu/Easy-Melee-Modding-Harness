# Cactuar Dash — Handoff / Next-Agent Jumpstart

**Read this, then `ONLINE_MACRO_GUIDE.md` + `ONLINE_REFERENCE.md`.** Current state,
design, and the hard-won delay/timing insight, so you don't re-derive it. (Written
2026-05-22, the session that built and shipped it.)

## What it is
The Cactuar dash skips Melee's slow run-turnaround (`TurnRun 0x13`, ~20–30f). While
the LOCAL player is in **Run `0x15`** or **RunBrake `0x17`** and slams the stick to the
extreme **opposite** of their facing (a turnaround intent), the macro **vetoes** that
input and substitutes a **crouch** (stickX=0, stickY=full down). It holds the crouch
through `Squat 0x27` (~7f) and releases the override so the player's still-held opposite
stick **dashes them out** the new direction. Local player only; works as P1 or P2.

## Status: SHIPPED (validated online, deploy + real-play tuning pending)
- **Offline A/B** (`offline_cactuar_macro.py`, slot 2, [PASS], reproducible): slow
  `TurnRun 0x13` **30f → 0f**, replaced by `Squat 0x27` ×7 → dash, facing flips.
- **Online** (`online_cactuar_test.py`, producer-side, 1-frame-delay machine): **48/49
  run-reversals → crouch**, 0 desync. Delay-comp release at squat frame 5.
- **Shipped gecko:** [`online_cactuar_dash.gecko.txt`](../online_cactuar_dash.gecko.txt)
  (regen: `make_cactuar_dash_gecko.py`). Delay-adaptive (below). Add in Slippi Manager
  like the L-cancel; active in all normal online games — **no savestate/harness needed**.

## The mechanism (settled — don't re-derive)
- **Hook `0x8034E680`** (producer-side, inside `PAD_Read` after stick calibration,
  upstream of `TriggerSendInput`'s EXI scrape → netplay-safe). Displaced original
  `lbz r0,7(r3)` = `0x88030007`; preserve `r3` (calib ptr), `r4` (PADStatus), `r13`.
  Override the stick by writing `2(r4)`=X, `3(r4)`=Y (signed bytes: **+X=right, −Y=down**).
- **Local player via ODB:** `port = *(*(r13-0x49E4)+0)`, then GObj `0x80453130+port*0xE90`,
  Player Data `*(GObj+0x2C)`. MEM1-check every pointer.
- **States:** Run `0x15`, RunBrake `0x17` (triggers); `Squat 0x27` (7f) → `SquatWait 0x28`
  (crouch); facing = Player Data `+0x2C` (float, sign = dir). Dash `0x14` is **excluded**
  (can't crouch from a dash) — this is why a turnaround at the very end of a dash can slip.
- **Trigger gate:** state ∈ {0x15,0x17} AND |stickX| ≥ `0x60` opposite-to-facing AND
  |stickY| ≤ `0x18`.

## The delay-compensation insight (THE crux — read this)
The dash-out must land on the **first actionable frame** out of squat:
- Release **too early** → the player's held-direction rising edge is consumed during the
  non-actionable `Squat 0x27` → **no dash**.
- Release **too late** → extra `SquatWait` frames → dash comes out late.

The correct release frame depends on the connection's **input delay**, which **differs
per machine** (this dev machine = 1 frame; the user's target = 2). So the cave reads
**`ODB_DELAY_FRAMES` (ODB `+0x21`)** at runtime and releases when the squat frame
counter **`+0x894` ≥ `6 − delay`** (→ frame 5 on 1-frame, 4 on 2-frame). `+0x894` is a
float resetting to 1.0 per state; decoded to int **without FPU**
(`exp=(bits>>23)&0xFF; n=((bits&0x7FFFFF)|0x800000) >> (150−exp)`). Calibration **K=6**
came from the 1-frame data (release frame 5 dashed correctly; 4 was too early).
`Squat 0x27` is a 7f animation **floor**; delay-comp only trims the extra `SquatWait`
frames (9f→7f), it can't make the crouch shorter than 7f.

## Open / known
- **Validate on the 2-frame machine** (real play). The dynamic threshold should self-
  correct, but K=6 was only confirmed on 1-frame.
- **~1/49 slip to TurnRun**: a reversal landing in `Dash 0x14` (excluded) or the Dash→Run
  boundary — the user's "turnaround in the last ~2f of a dash" worry. If frequent, widen
  the trigger (carefully — you can't crouch from an early dash).
- **Rollbacks during harness tests** were monitoring overhead (dme polling), gone in the
  pure gecko (confirm in real play).

## Files
- `offline_cactuar_probe.py` — discovery (stick encoding, slot-2 layout, Squat=7f, baseline turn).
- `offline_cactuar_macro.py` — offline veto + A/B ([PASS]).
- `offline_slot2_inspect.py` — read-only slot-2 layout.
- `online_cactuar_test.py` — online test (synthetic F4/Enter entry; delay-adaptive cave).
- `online_cactuar_manual.py` / `online_cactuar_attach.py` — fallbacks (manual entry / attach;
  **stale**: they predate the dynamic cave — fix the THRESH import before reuse).
- `make_cactuar_dash_gecko.py` → `online_cactuar_dash.gecko.txt` — the shipped gecko.

## Deploy (real online play)
Add `online_cactuar_dash.gecko.txt` (everything below the `$Cactuar Dash` line) as a new
Gecko code in your Slippi GALE01 list (same flow as the L-cancel), enable it, play online
normally. No savestate/slot-4 bake needed for normal play (that was only the dev harness).
To retune the timing constant, edit the `6` in `make_cactuar_dash_gecko.py`'s HOLD block
(`subfic 9, 9, 6`) and regenerate.

## Gotchas hit this session (see also CLAUDE.md)
- A **Slippi update** wipes the `Dolphin` hardlink, writes a duplicate `isopaths` in
  `Dolphin.ini` (fixed: `configparser(strict=False)` in `melee_harness.py`), and can leave
  a stale Dolphin that breaks synthetic F4 — **kill ALL Dolphins** (incl. `Slippi Dolphin`)
  before launching. Savestates themselves survived the update (load fine manually).
- **Re-attaching dme in a new process** to a running Dolphin gives torn garbage — launch +
  hook + install in ONE process.
