# L-cancel notes (SSBM v1.02 NTSC)

## Mechanic (from the address sheet, to be confirmed in `discover_lcancel.py`)

When a player lands during an aerial attack (action states 0x46–0x4A:
LandingAirN/F/B/Hi/Lw), the engine checks whether L, R, or Z was pressed
recently. If yes within a 7-frame window, the landing-lag animation plays at
**2x speed** — the L-cancel.

PlCo (universal constants):

| Offset | Value | Meaning |
| --- | --- | --- |
| `0xA0C4` | `7` | L-cancel window: frames before landing the press must occur. |
| `0xA0C8` | `40000000` (= 2.0f) | Animation-speed divisor applied on success. |

The "press too early then press again to restart the window" behavior the
player described matches **rising-edge detection** — each press resets a
per-player "frames since pressed" counter to 0.

## Per-player observables (Player Data / Char Data offsets)

These live at `*(GObj + 0x2C) + offset`, where the GObj pointer is
`0x80453130 + 0xE90*(port-1)`:

| Offset | Meaning | Use |
| --- | --- | --- |
| `0x0010` | Action state (word) | aerials 0x41–0x45; landings 0x46–0x4A. |
| `0x065C` | Digital buttons (word, processed) | what the engine consumes. |
| `0x0678` | Frames since analog trigger was moved (byte) | analog L/R timer. |
| `0x067F` | Frames since Z button was pressed (byte) | Z timer (Z L-cancels too). |
| `0x0680` | **Frames since L/R digital was pressed** (byte) | **the field we drive.** |
| `0x2354` | Landing lag divisor (float) | **the field we observe — 2.0 on L-cancel.** |
| `0x2358` | Act-out-of-landing flag (byte) | secondary signal; 1 = can act early. |

Reset on press, increment otherwise — so to keep `0x0680 ≤ 7` we register a
fresh rising edge every ≤6 frames while the player is aerial.

## Controller digital data (where we write)

`CONTROLLER_DIGITAL = 0x804C1FAC + 0x44*(port-1)` is a 32-bit BE word. Bit
layout (top to bottom): `xxxx xxxx UDLR UDLR xxxS YXBA xLRZ UDRL`.

Lowest byte (`xLRZ UDRL`): bit 6 = L (mask `0x40`), bit 5 = R, bit 4 = Z, and
the low nibble is the D-pad.

So `L_BIT = 0x40` ORed into the 32-bit word at `CONTROLLER_DIGITAL`.

Caveat (from `melee_harness.set_digital_buttons` docstring): Dolphin's input
pipeline rewrites this region every controller poll, so dme writes from
Python race the poll. If reliability becomes an issue we'll move the toggle
into a runtime-installed PPC routine via the meta-flush primitive.

## Action states of interest (Action_State_Reference.csv)

| ID | Name | Meaning |
| --- | --- | --- |
| `0x0018` | KneeBend | jumpsquat (all characters) |
| `0x0019` / `0x001A` | JumpF / JumpB | initial jump |
| `0x001D` | Fall | falling, no aerial out yet |
| `0x0041` | AttackAirN | nair |
| `0x0042` | AttackAirF | fair |
| `0x0043` | AttackAirB | bair |
| `0x0044` | AttackAirHi | uair |
| `0x0045` | AttackAirLw | dair |
| `0x0046–0x004A` | LandingAir* | landing during the matching aerial (L-cancel target) |
| `0x002A` | Landing | normal landing (no aerial active — not our target) |

## "L-cancel check function" — CORRECTION (2026-05-22)

**This function is actually the AIRBORNE TRIGGER / AIRDODGE check, not the landing
L-cancel.** Re-disassembled this session (`disasm_lcancel_analog.py`): the function
at `0x8008E498` only runs for action states `0x19`–`0x26` (jumps/falls) and `0xEC`
(EscapeAir/airdodge), and reads the **digital** L/R timer `0x680`. The *landing*
L-cancel runs in a different routine that checks landing states `0x46`–`0x4A` and
sets `LCancelStatus` (Player Data `+0x25FF`) — that's the correct observable. The
`0x680`-reads-here detail still explains airdodge behavior (and why a light analog
L `< 0xAA`, which never touches `0x680`, can't airdodge). Keep the rest below for
the offline digital macro, but don't treat it as the landing-cancel check.

Found at `0x8008E4A8`-`0x8008E4E8` (Slippi-build address). Key lines:

```
0x8008E4BC  lwz   r5, -0x514c(r13)    ; r5 = global pointer (PlCo-related)
0x8008E4C0  lbz   r4, 0x680(r3)       ; r4 = "Frames Since L/R Pressed"
0x8008E4C4  lwz   r0, 0x18c(r5)       ; r0 = L-cancel window (= 7)
0x8008E4C8  cmpw  r4, r0
0x8008E4CC  bgt   0x8008e4e8          ; if timer > window -> FAIL
...
0x8008E4E0  li    r3, 1               ; SUCCESS
0x8008E4E4  blr
0x8008E4E8  li    r3, 0               ; FAIL
```

The check is `bgt` (greater-than), so **`timer ≤ 7` succeeds**. Max safe timer at
landing = 7. The cycle-7 macro keeps timer in 0..6, one full frame of safety
margin.

## Cycle-7 vs toggle-every-frame: why cycle-7

Both patterns trigger L-cancel reliably (timer ≤ window). The difference is
how often a rising-edge L press occurs:

| Pattern | Press frames/cycle | Rising edges / 7 frames | Airdodge risk at aerial->Fall |
| --- | --: | --: | --: |
| Toggle every frame | 1 / 2 | 3.5 | ~50% |
| **Cycle 7 (1 press / 6 release)** | 1 / 7 | 1 | ~14% |
| 6 held / 1 released | 6 / 7 | 1 | ~14% |

Cycle-7 ties with "6 held / 1 released" on rising edges (only 1 per cycle), but
keeps L released most of the time -- cleaner pad state, less interference with
user's own L taps. Verified empirically (lcancel_rig.py 4-trial run): short hop
and full hop both produce 15f -> 7f, no airdodge observed.

## Per-aerial landing-lag data (Fox, slot 2, cycle-7)

From `test_fox_aerials.py` (20 trials: 5 aerials × short/full × L off/cycle-7).
Fox faces **left** in slot 2 -- stick+x produces BAIR, stick-x produces FAIR.

| Aerial | Short hop / L off | Short hop / L cycle-7 | Ratio |
| --- | ---: | ---: | ---: |
| NAIR (0x41) | 15 f | 7 f  | 0.467 |
| FAIR (0x42) | 22 f | 11 f | 0.500 |
| BAIR (0x43) | 20 f | 10 f | 0.500 |
| UAIR (0x44) | 18 f | 9 f  | 0.500 |
| DAIR (0x45) | 18 f | 9 f  | 0.500 |

Full-hop variants that *land during the aerial* (NAIR, FAIR) cancel
identically to short hop. Full-hop variants whose aerial animation finishes
mid-air (BAIR, UAIR, DAIR — Fox is in the air long enough that the aerial
completes, he transitions through FallAerial 0x20, and plain-lands at 0x2A)
**cannot be L-cancelled** in melee at all — there's no LandingAir state to
cancel. The macro behaves correctly in those cases: it doesn't airdodge,
doesn't extend the aerial; the move just isn't L-cancellable in that config.

Crucially: **0 airdodges observed across all 20 trials**. The cycle-7
counter-reset-on-non-aerial design is safe at the aerial→FallAerial
boundary.

## Universal-invariant verification (test_l_timer_invariant.py)

Empirical proof the cycle is bounded. NAIR across 13 trials sweeping
fast-fall delays (FF@0..FF@20, short and full hop) so Fox lands on 4
distinct airborne durations (19, 26, 29, 34 frames). For each trial we
sample `Char Data + 0x680` at every aerial frame. Result:

- **Steady-state max(0x680) = 6 in every trial** (samples form a clean
  `0,1,2,3,4,5,6,0,...` cycle).
- **Landing-frame 0x680 ∈ {0, 4, 5}** — well below the 7-frame engine
  window in every case.
- All 13 trials L-cancelled (land-dur 7f vs control 15f).

Note: the first aerial sample (sample[0]) shows pre-aerial carry-over
(values 12-17) because at PadRead time on the engine's first aerial
frame, action_state is still Fall (the documented PadRead-state lag).
The macro starts pressing L on the second aerial frame from Python's
view; from sample[1] onwards the cycle is bounded by 6.

Theoretical edge case: if Fox is in an aerial state for fewer than 2
engine frames (input A → aerial → land within one frame), the macro
never gets to press L for that aerial and 0x680 reflects pre-aerial
history. Not reachable in normal play -- aerial action states require
the airborne flag and aerials have intrinsic startup frames.

## Empirical results (2026-05-17)

Path #2 (Python writes to `0x804C1FAC`) is non-functional: writes are
clobbered by Dolphin's input pipeline. Confirmed by `discover_lcancel.py`
test C — holding L on the controller for 5 frames left `0x0680` at 255.

Path #3 (PadRead hook at runtime via meta-flush) is what works. The shipped
macro lives in `auto_lcancel.py` and uses this path.

`Char Data + 0x2354` is **not** the L-cancel observable. It reads 0.96f
both with and without a successful L-cancel on Fox's nair — see
`LESSONS.md` #3. The real observable is **landing-state duration**:
Fox's NAIR-landing lasts 15 frames without L-cancel, 7 frames with.
`lcancel_rig.py` measures this directly.

Also: writes to `0x680` from Python persist long enough to read back, but
the engine increments the field every frame — see `discover_lcancel.py`
test B (wrote `0x680 = 50`, observed `50, 51, 52, ..., 57` over 8 frames).
So you can't pin it from Python; you have to drive it via real L-press
detection (the L bit set on the pad halfword at `(r25)` in the PadRead hook).
