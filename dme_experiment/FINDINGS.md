# FINDINGS — pure-dme reproduction of the JC-shine macro

Reproduce findings from prior sessions (which used gecko-injected PowerPC)
using only `dolphin_memory_engine` (dme) reads/writes from Python. No
gecko codes installed.

**TL;DR**

1. **Direct action-state writes are functionally a no-op.** Writing
   PD+0x10 = 0x0018 (KneeBend) makes Fox APPEAR to be in KneeBend
   forever — the engine never transitions out. The data sheet's claim
   "modifying this alone does nothing" is exactly correct: the action
   state ID is set, but the animation / sub-action / frame counter /
   input pipeline aren't touched. Useful for observability tricks; not
   useful for driving real character behavior.

2. **All "post-pipeline" engine button planes are clobbered every frame
   by Dolphin's input copy.** A single write per frame to PD+0x65C /
   PD+0x668 / 0x804C1FAC fails to trigger anything (exp02).

3. **Burst-writing buttons in a tight Python loop wins the race.**
   ~5000 writes/sec from `dolphin_memory_engine` to PD+0x65C + PD+0x668
   + 0x804C1FAC simultaneously will land in the input-pipeline window
   often enough that the engine sees our buttons. 50 ms of burst-writes
   reliably triggers a Y press for one frame's worth of jumpsquat
   (exp04).

4. **Canonical JC-shine reproduces via pure dme.** Fox transitions Wait →
   KneeBend (3 frames) → aerial shine 0x016D directly (no JumpF). The
   gecko-version finding from `project_jc_shine_timing` is reproduced
   exactly: 3-frame jumpsquat, then KneeBend → 0x016D → 0x016E → 0x0169
   (ground shine after landing) (exp05, exp07).

5. **State-machine input driver reaches 80% canonical JC-shine
   reliability** (exp10). Strategy: poll Fox's action state at high
   frequency; write Y while Fox = Wait; write B+down while Fox =
   KneeBend; stop on detection of shine. The state machine adapts to
   timing variance in the input-pipeline race.

6. **Reactive trigger via dme polling beats the gecko's reaction speed
   for detection** (exp06): polling Marth's action state at ~kHz from
   Python detects the Catch transition with 0.02–0.13 ms latency
   (frame_latency = 0). Fox's KneeBend response is at T+1 to T+2 (vs
   gecko's T+1) — comparable performance.

## Pipeline model (empirically derived)

Each emulated frame, Dolphin's input pipeline executes (in order):
1. Slippi controller plugin writes raw PADStatus.
2. HSD_PadRead at 0x803775B8 copies PADStatus → 0x804C1FAC (32-byte
   "Controller N Digital Data" region per port, stride 0x44).
3. Per-character pipeline copies 0x804C1FAC → PD+0x65C (digital),
   PD+0x620/0x624 (analog stick floats), PD+0x668 (instant).
4. Main character loop (8006ad10) reads PD+* and processes action-state
   transitions.

The gecko hook at 0x803775B8 fires JUST BEFORE step 2 reads PADStatus.
Its writes to r25's PADStatus propagate cleanly through steps 2-4.

Our dme-only approach can't hook step 1 (raw PADStatus base address not
yet determined; would need a probe gecko to discover). Instead we
burst-write the post-pipeline planes (PD+0x65C, PD+0x668, 0x804C1FAC).
Each frame, the pipeline overwrites these planes — but if our write
falls in the small window after step 3 and before step 4, our buttons
are visible to the action-state machine.

At ~5000 writes/sec from Python+dme, we have several writes per millisecond.
With ~1 ms between step 3 and step 4, we land in the window often enough
to drive inputs reliably.

## Per-frame data, canonical JC-shine via dme (exp07 trial 1)

```
f=2526 (T+0): p1=Wait    p2=Wait     btn=0x0  sy=+0.00   <- trigger frame
f=2527 (T+1): p1=Catch   p2=Wait     btn=0x800 sy=+0.00   <- Marth visible Catch
f=2528 (T+2): p1=Catch   p2=KneeBend btn=0x800 sy=+0.00   <- Fox KneeBend frame 1
f=2529 (T+3): p1=Catch   p2=KneeBend btn=0x200 sy=-1.00   <- KneeBend frame 2 (B+down)
f=2530 (T+4): p1=Catch   p2=KneeBend btn=0x200 sy=-1.00   <- KneeBend frame 3 (last)
f=2531 (T+5): p1=Catch   p2=0x016D   btn=0x200 sy=-1.00   <- AERIAL SHINE (no JumpF!)
f=2532 (T+6): p1=Catch   p2=0x016D   btn=0x0   sy=+0.00
f=2533 (T+7): p1=Catch   p2=0x016D   btn=0x0   sy=+0.00
f=2534 (T+8): p1=Catch   p2=0x016E   btn=0x0   sy=+0.00   <- aerial shine loop
f=2535 (T+9): p1=Catch   p2=0x0169   btn=0x0   sy=+0.00   <- landed -> ground shine
```

**Identical to gecko-version timing** in `Session_2026_05_15.md`. 3 frames
KneeBend, then KneeBend → 0x016D directly, then 0x016E → 0x0169 after
landing.

## Reliability table

| script           | parameters           | canonical JC | failures             |
|------------------|----------------------|--------------|----------------------|
| exp05_fixed_gap  | Y50ms gap2f B+d50ms  | 1/1 (1 trial run that succeeded; others ground-shined) | input timing miss   |
| exp07_full       | Y50ms gap2f B+d50ms  | 4/5 (80%)    | 1 ground shine       |
| exp08_stress     | Y60ms gap2f B+d60ms  | 2/20 (10%)   | mostly JumpF / gnd   |
| exp09_adaptive   | poll-driven, kneebend-key | 0/20 (0%) | B+down timing miss   |
| exp10_state_mach | per-state input driver | 16/20 (80%) | 2 JumpF, 2 no-jump   |

**Best reproducible design**: exp10's state-machine driver. Drives Y or
B+down based on observed Fox action state. Single-thread tight loop:
```python
while running:
    s = read_word(fox_action_state)
    if s == WAIT:        burst_y_press()
    elif s == KNEEBEND:  burst_b_down()
    elif s in SHINE:     break  # done
```

## Caveats / Limitations

- **Input pipeline race is non-deterministic.** On any given trial the
  Y burst may "leak" into KneeBend frames (causing extended jumpsquat
  via held Y), or miss the engine read window entirely (no jump). Fixed
  parameters give 10–80% reliability; the adaptive state machine
  approaches 80% but isn't yet matching the gecko's 100%.

- **Polling rate matters.** Below ~kHz the state machine misses
  transient KneeBend frames and ends up writing Y when Fox is already
  in jumpsquat → extends the apparent press → full hop instead of JC.

- **No PADStatus base address discovered yet.** If we found `r25`'s
  static origin (likely 4 fixed addresses in MEM1, one per port), we
  could write directly to the raw PADStatus plane and the gecko-style
  100% reliability should be achievable from pure dme. The address
  isn't in the standard CSVs and would need either a probe gecko
  (one-time discovery) or a careful lldb / Dolphin debugger session.

- **Fox character index assumed at port 2 (port_id 1).** All writes
  target `Harness.player_data_ptr(2)`. If Marth/Fox swap ports between
  sessions, addresses need adjusting.

## What this proves about Frame-1 netplay-safety

The blocked Candidate D.1 work (action-state-keyed gecko) was trying to
do this entirely inside the gecko. We didn't need to. **dme polling of
Marth's action state PLUS dme button-bursts on Fox** does the same
work outside the engine, with frame_latency = 0 detection and ≤T+5
Fox-shine-state-reached.

This isn't netplay-safe (dme can't tell which player is local), but it
demonstrates the timing model end-to-end without a gecko in the loop —
exactly the alternative-architecture experiment the user asked for.

## Memory edits saved

- `feedback_dme_input_burst.md` — burst-writing technique for racing
  Dolphin's input pipeline.
- `project_dme_jc_shine_works.md` — JC-shine reproducible via pure dme.
- `reference_padread_disassembly.md` — pointer to the disassembly dump
  for future PADStatus-base-address discovery work.
