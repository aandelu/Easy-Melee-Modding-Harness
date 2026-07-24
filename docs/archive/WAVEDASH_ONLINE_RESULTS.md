> **HISTORICAL (archived 2026-07-24).** Findings absorbed into docs/macros/wavedash.md; the instrument-over-observer lesson lives in WORKFLOW.md.

# Wavedash — Online Port: Results & Findings (2026-06-04)

The online port of the up-bound wavedash (offline build = `play_wavedash_offline.py`,
user-confirmed "perfect"). **Outcome: SHIPPED, frame-perfect at 1-frame delay, no
desync.** Read [`WAVEDASH_HANDOFF.md`](WAVEDASH_HANDOFF.md) first for the mechanic.

## What shipped
- **`online_wavedash.gecko.txt`** (regen + capstone-verify: `make_wavedash_gecko.py`).
  Both a RAW (06+04) form (recommended — exact validated memory state) and a C2 form.
  Add **both** codes (buttons + stick), enable, play online normally. No savestate/harness.

## The cave (two producer-side hooks, each re-resolves the local player)
- **`0x8034E2AC` — digital buttons.** `oris r0,0x0800` (Y jump) in grounded-actionable
  states + up held; `oris r0,0x0040` (digital L airdodge) in KneeBend on the latch at the
  target frame. Displaced `rlwinm`=`0x540084BE`. Cave `0x803FA800`.
  - Digital L **must** be here — `0x8034E680` is downstream of PAD_Read's analog→digital
    conversion, so an L written there sets no digital bit (that's exactly why the L-cancel
    used it). The airborne airdodge trigger reads the **digital** L timer `0x680`.
- **`0x8034E680` — stick angle.** On the same gate (KneeBend + latch + target frame),
  overrides stick `2/3(r4)` to the airdodge angle by live `stickX` (right `0x6A,0xE0` /
  left `0x96,0xE0` / up-only → down `0,0x90`). Displaced `lbz r0,7(r3)`=`0x88030007`. Cave
  `0x803FA600`. Runs after E2AC in the same PAD_Read call, so the two agree per frame.
- Both: local player via ODB `+0x00`, input delay ODB `+0x21`, jumpsquat `0x148` &
  asfc `0x894` float-decoded without FPU. Latch `WD_PEND` byte @ `0x803FA470`.

## The frame-perfect timing (the crux)
**`target asfc = jumpsquat − 1 − delay`** (clamped ≥1). On delay=1 Fox → asfc 1 → the
airdodge buffers during KneeBend and the character goes KneeBend→LandingFallSpecial with
**0 air frames**. The neighbouring target (`jumpsquat − delay`, asfc 2) gives exactly **one**
`JumpF` air frame — a frame late. Matches offline (`asfc==jumpsquat−1` at delay 0).

### How it was found — and the big lesson
The **lossy Python observer was WRONG**: it called the 1-late timing "tight" (it can't
reliably sample a single 1-frame air state at 60 fps), which sent an early conclusion the
wrong way. The user's frame-accurate eye flagged "still a frame late." Ground truth came
from **instrumenting the cave itself**:
- `online_wavedash_tune.py` — per-target PERFECT (KneeBend→LandingFallSpecial direct) vs
  FLOATY (via an air frame) counters. Sweep result was unambiguous: BASE+1 = 33 perfect / 0
  floaty; BASE 0 = 0 / 34.
- `online_wavedash_trace.py` — a 32-slot per-frame state ring, dumped. BASE+1 = `KneeBend →
  LandingFallSpecial` directly; BASE 0 = `KneeBend → JumpF → LandingFallSpecial`. Note the
  producer hook records **~2×/frame** (rollback re-sim runs it too): `LandingFallSpecial×20`
  = the ~10-frame landing lag, `KneeBend×6` = the 3-frame jumpsquat.

**Takeaway for next time: trust an in-cave counter / state-ring over the Python observer for
anything frame-precise online. Build the instrument before concluding.**

## The delay floor (matters for real play)
The airdodge can only be injected as early as the **first jumpsquat frame** (asfc 1), so
frame-perfect requires **`jumpsquat ≥ delay + 2`**:

| char | jumpsquat | delay 1 | delay 2 |
|---|---|---|---|
| Fox | 3 | frame-perfect | ~1 frame late (floor) |
| Marth / most cast | ≥4 | frame-perfect | frame-perfect |

This is a fundamental producer-side limit (a consumer-side edit would avoid it but desyncs).
The target is **clamped ≥1** so it always fires — degrades to 1-late, never "no wavedash."
**1-frame delay is the low-ping LAN case** between the two dev machines; **2-frame is the
common real-online case**, so Fox in real play will usually be ~1 late. Not yet tested at
delay 2 (the model + the delay-1 data predict it; user to confirm on their delay-2 machine).

## What ported cleanly / what differed from the plan
- **Up-latch (`WD_PEND`) is rollback-safe in practice** — set on the grounded jump, airdodge
  fires off the latch (not current up), failsafe-cleared when airborne. No double-jumps,
  repeat works. Validated.
- **Repeat needed no predictive buffer-jump** (the handoff feared it). Gating Y on
  grounded-actionable (`0x0E..0x17`, released off-ground) gives a fresh rising edge each
  landing → natural repeat (delay frames slower per cycle, fine).
- **Grounded-only by construction** — jump only in `0x0E..0x17`, airdodge L only in KneeBend.
  No airborne airdodge possible. Latch also cleared on grounded-without-up (prevents a stale
  boot value airdodging a normal jump).

## Gotchas hit (add to the online guides)
- **Self-drive: force sim-up in GROUNDED states only.** Forcing stickY=up every frame makes
  an up→(airdodge down)→up flip that spuriously double-jumps in the air. With the latch, the
  airdodge doesn't need up during KneeBend, so gate the sim to grounded.
- **dme re-attach to a running Dolphin = torn garbage** (scene read `0x3E00`, cave reads
  `0x0000403F`). Verify a known address (hook branch / cave word) before writing; abort if it
  fails. Can't change a live cave from a fresh process — relaunch in one process.
- **Online entry: re-F4+Enter each attempt over ~110s** (reloads the direct-connect savestate
  fresh) is the robust retry. Blind Enter-only at the CSS drifted into an **offline VS**
  (`0x0202`) match. Peer must be at "waiting for opponent" during the attempt window.
- Make every online read detach-tolerant (re-hook on failure) — heavy entry polling detaches
  dme and a raw `read_word` crashes the run.

## Files
- `make_wavedash_gecko.py` → `online_wavedash.gecko.txt` — the shipped generator + gecko.
- `online_wavedash_tune.py` — instrumented airdodge-frame sweep (perfect/floaty counters).
- `online_wavedash_trace.py` — per-frame state ring (the definitive frame-by-frame proof).
- `online_wavedash_test.py` — first online bring-up (bare self-drive, no-desync proof).
- `online_wavedash_ship.py` — up-gate + sim validation (superseded by tune/trace).
- Memory: `wavedash_mechanic.md`, `wavedash_observe_setup.md`.

## Pending
1. **User delay-2 real-input test** (their main machine). Expect Fox ~1-late (floor); confirm.
2. **The `0x8034E2AC` real-stick up-check** (`lbz 3(r4)`, centered pre-calibration stickY) is
   the one piece only a real controller can validate. If it never wavedashes on up-hold, move
   the up-read to player-data stick or set an up-flag at `0x8034E680` (reliable post-calib) and
   read it 1 frame late at `0x8034E2AC`.
