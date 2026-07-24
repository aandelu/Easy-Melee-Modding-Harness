# Up-Bound Wavedash

Hold **up** on the left stick → the macro jumps and airdodges into the ground on the
frame-perfect frame (= wavedash). Direction from the horizontal you hold; up alone =
straight down; holding up repeats. Works on any character. Shared addresses/offsets:
[`../REFERENCE.md`](../REFERENCE.md). Dev loop: [`../../WORKFLOW.md`](../../WORKFLOW.md).
Status board: [`../STATUS.md`](../STATUS.md).

## Mechanic + constants

- **Sequence:** in a grounded-actionable state (**`0x0E`–`0x17`**, Wait..RunBrake) with up
  held → inject **Y** (`0x800`) and set the per-port latch `WD_PEND` (byte `0x803FA470`);
  in **KneeBend `0x18`** on the target frame → inject **digital L** (`0x40`) + override the
  stick to a shallow down-diagonal → `LandingFallSpecial 0x2B` (~10f lag).
- **Up check:** Player Data **`+0x624`** (Analog Stick Data Y, float) `>= 0.5625`
  (`0x3F100000`). The raw pre-calibration stickY byte at the `0x8034E2AC` hook proved
  unreliable on real hardware (flicker → spurious hops), so the cave reads the engine's
  processed stick instead.
- **Airdodge angles** (signed stick bytes): right `(0x6A, 0xE0)` → processed
  `(+0.95, −0.287)`; left `(0x96, 0xE0)`; up-only → straight down `(0x00, 0x90)`.
  Direction is read **live** on the airdodge frame (the whole jumpsquat to switch);
  up is **latched** (a quick tap still wavedashes).
- **Frame-perfect timing:** fire at **`asfc == jumpsquat − 1 − delay`**, clamped `>= 1`.
  Jumpsquat is per-character from Player Data **`+0x148`** (int-or-float handled); asfc =
  `+0x894` (float, no-FPU decode); delay = `ODB_DELAY_FRAMES` (ODB `+0x21`) at runtime.
- **Delay floor:** frame-perfect requires `jumpsquat >= delay + 2`. Fox (js 3) is perfect
  at delay 1, **~1 frame late at delay 2** (a producer-side limit, not a tuning miss);
  Marth and most of the cast (js ≥4) stay perfect at delay 2. The clamp means it degrades
  to 1-late, never "no wavedash".
- **Luigi fix:** up-held-now during KneeBend also sets the latch — bridges a quick up-tap
  through to the airdodge frame for later-target characters (Luigi/Marth, target asfc 2),
  which otherwise full-hop on taps.

## Hook / cave logic

**Two producer-side hooks** inside PAD_Read (netplay-safe), each re-resolving the local
player via ODB (`port = *(*(r13-0x49E4)+0)`), all pointers MEM1-checked:

- **`0x8034E2AC` — digital buttons** (cave `0x803FA800`; displaced `rlwinm` =
  `0x540084BE`): Y in grounded-actionable + up; L in KneeBend on the latch at the target
  frame. Digital L **must** be here — `0x8034E680` is downstream of the analog→digital
  conversion, and the airborne airdodge check reads the digital L/R timer `+0x680`.
- **`0x8034E680` — stick angle** (cave `0x803FA600`; displaced `lbz r0,7(r3)` =
  `0x88030007`): same gate, writes `2/3(r4)` by live stickX sign. Runs after E2AC in the
  same PAD_Read call, so the two agree per frame.

The frame-perfect target was found by **in-cave instrumentation** (perfect-vs-floaty
counters + a per-frame state ring) after the lossy Python observer called the 1-late
timing "tight" — trust in-cave counters over the observer for anything frame-precise.

## Coexistence rule (critical — deploy-time)

**Never enable the standalone L-cancel gecko together with the wavedash gecko** — both
hook `0x8034E680`, and the second branch clobbers the first. **The wavedash gecko already
contains the auto-L-cancel**: since 2026-06-05 the analog-L pulse is folded into the
`0x8034E680` stick cave (`INCLUDE_LCANCEL=True` in the generator; aerials write byte
`6(r4)`, KneeBend writes `2/3(r4)` — disjoint). Validated live: 12 wavedashes + 19/0
L-cancels in one match. See [`lcancel.md`](lcancel.md).

**OFFLINE scratch overlap too:** the offline wavedash `WD_PEND` latch and the
`auto_lcancel/` cycle counter both use scratch `0x803FA470` — don't install both offline
macros in the same session either.

## Files

| File | Role |
| --- | --- |
| `online_wavedash.gecko.txt` | **Shipped gecko** (C2 form + RAW 06+04 fallback form, both in the file). |
| `make_wavedash_gecko.py` | Generator (keystone build + capstone verify, both hooks + folded L-cancel). |
| `play_wavedash_offline.py` | Offline playable macro (slot 2, real input, the cave the online port came from). |
| `play_wavedash_monitor.py` | Play + monitor in one process (dme can't re-attach from a fresh process). |
| `attach_observe_wavedash.py` | Observation helper for live sessions. |

Discovery/tuning one-offs (probe, tune sweep, state-ring trace, online bring-up) were
deleted in the 2026-07-24 cleanup (git history). History/narrative: `docs/archive/`;
full online-port findings: [`../archive/WAVEDASH_ONLINE_RESULTS.md`](../archive/WAVEDASH_ONLINE_RESULTS.md).

## Current status

**OFFLINE + ONLINE SHIPPED.** Offline user-confirmed "perfect" (Fox js 3, Marth js 4:
direction, mid-jumpsquat switching, repeat, quick-tap all work). Online validated vs a
live peer at delay 1 (Fox): **no desync, frame-perfect** (33/33 direct
KneeBend→LandingFallSpecial, 0 air frames), up-latch rollback-safe in practice, repeat
works without a predictive buffer-jump.

## Open items

1. **User delay-2 real-input test** on their main machine. Expect Fox ~1 frame late —
   that's the floor, not a bug; confirm Marth-class characters stay perfect.
2. **Real-controller up-check validation.** The up-read now uses Player Data `+0x624`
   (processed stick) after the raw `0x8034E2AC` byte misbehaved with real hardware; only
   a real controller session can confirm hold-up reliably wavedashes.
3. **Hold-vs-tap auto-repeat question:** does *holding* up auto-repeat with real input
   (the self-drive said yes via the grounded-actionable Y gate)? Confirm feel in real play.
