# ASDI Floorhug

Auto-**ASDI down** during hitlag → the victim collides with the floor on the frame after
hitlag, dumping all hitstun for ~4 frames of landing lag (below tumble) instead of a long
airborne hitstun arc. Shared addresses/offsets: [`../REFERENCE.md`](../REFERENCE.md).
Dev loop: [`../../WORKFLOW.md`](../../WORKFLOW.md). Status: [`../STATUS.md`](../STATUS.md).

Source material: the two "misunderstood mechanic" / "live forever" SSBM videos. Every
number below was **re-measured on this build** — the videos were used for the mechanic,
not for the constants.

## Mechanic

- **ASDI ≠ SDI.** SDI multiplies the stick by 6 and teleports you on any hitlag frame but
  the first; it may **not** cross into the floor while airborne. ASDI multiplies by 3,
  is sampled on the **last hitlag frame**, applies on the frame after — and *is* allowed
  to cause the air→ground transition. Floorhugging is an ASDI effect, not an SDI one.
- **C-stick wins.** If both sticks are held, the C-stick takes priority for ASDI. That is
  why this macro drives the C-stick and leaves the analog stick alone.
- **No frame targeting needed.** ASDI is *sampled* on the last hitlag frame, so the
  C-stick merely has to be down by then — holding it from detection onward is enough.
  The constraint is the **release**, not the press (see the down-smash trap below).
- It is **hitlag**, not hitstun. Hitstun exists but the window we act in is hitlag.

## Measured constants (2026-07-25, Marth vs Fox, offline)

| Fact | Value | How |
| --- | --- | --- |
| `air` (`+0xE0`) during victim hitlag | **always 1** | 7/7 baseline events, then 12/12 |
| `hitstun` (`+0x2340`) during victim hitlag | **always > 0** | same |
| `hitstun` during **attacker** hitlag | **always 0** | 43 attacker rows — the only clean victim/attacker discriminator |
| `air` during attacker hitlag | **0 or 1** | an *aerial* attacker is airborne too, so `air` alone does NOT exclude the attacker |
| Hitlag length | 4–9 frames | matches `3 + dmg/3` |
| Position during hitlag | frozen (`y == y_last_landed` to ~1e-4) | position does not update until hitlag ends |
| **Floorhug succeeds** | `kb_y` (`+0x90`) **≤ 2.99** | 9/12 hits, `maxRise = 0.00` |
| **Floorhug fails** | `kb_y` **≥ 3.47** | 3/12 hits, `maxRise` 17–53 |

The success/failure split brackets **3.0 units** — exactly the ASDI reach, independently
confirming the videos' 116%-vs-117% Falcon math. Hitstun is genuinely dumped, not
suppressed: one hit carried 36 frames of hitstun and resolved in 9.

## Gate (v1.5, ASDI + tech — supersedes v1)

All plain word compares — floats compare against 0 as raw ints since `+0.0` is all-zero
bits, so **no FPU** is needed.

```
hitlag  != 0    +0x195C     in hitlag
hitstun != 0    +0x2340     belt-and-suspenders ONLY -- see below
air     == 1    +0xE0       the hit launched us (also blocks the grounded d-smash case)
state - 0x4E <= 0xD  +0x10  Damage state 0x4E..0x5B: the DEFINITIONAL victim check
                -> stb 0x90, 5(pad)               c-stick Y full down, every gated frame
hitlag < 2.0 (raw unsigned)                       last hitlag frame only:
                -> ori buttons, 0x20; sth         ONE digital-R press = the tech input
```

⚠️ **v1's `hitstun != 0` victim discriminator was wrong.** After a player has ever
been hit, `+0x2340` decays to a parked denormal `0x00000001` — prints as `0.00`,
passes `cmpwi != 0` (raw-bits proof, run 3). v1 therefore also injected into the
*attacker* during their attack hitlag (the real mechanism behind the old "61/137
attacker leak", which is **not** an r24/r25 register mismatch — REFERENCE §2.2 now
has the measured pad-ring structure). The Damage-state range clause is the fix;
run 3 counters showed it rejecting 27 poisoned attacker frames (`hitstun=91` vs
`dmgst=60`).

Stick byte encoding (from the wavedash gecko): full deflection is **`±0x70`**, so full
down = **`0x90`**. Injected bytes bypass the controller's physical octagon gate, so a
diagonal can be written at magnitude 1.41 where a human is capped at ~0.7 by the rim.

### Traps

- **Down-smash on the actionable frame.** C-stick down still held when the victim becomes
  actionable is a d-smash. The `air == 1` gate prevents this (you are never airborne in
  grounded hitstun or shieldstun), and online the injection is `delay` frames stale, so
  the tail lands inside hitstun or landing lag. Re-verify online.
- **No grounded-when-hit gate in v1.** `y == y_last_landed` looked *exact* in the first
  log only because it printed 2 decimals; the raw words differ (~1e-4) and an exact word
  compare rejected 100% of hits. v2 wants `(y - land_y) < 3.0` as a real float compare.
- **The "attacker leak" was the denormal, not the registers.** The old reading — that
  `r24`'s player isn't `r25`'s pad owner — is **wrong**: ring-logging (2026-07-25) proved
  `r24` IS the pad owner (`r25 = 0x8046B108 + k*0x30 + r24*0xC`, REFERENCE §2.2). The
  61/137 injections landed on the attacker because the attacker *passed the v1 gate* via
  the parked-denormal hitstun counter (§1.3). Fixed by the Damage-state clause (gate
  v1.5). Online is still cleaner by construction (local pad only, player from `ODB+0`) —
  still never port `r24` indexing.

## Hooks

- **Offline probe** (`asdi_probe_offline.py`): `0x803775B8`, displaced `lhz r0,0(r25)` =
  `0xA0190000`. C-stick byte `5(r25)` is consumed at `0x803775D8`, i.e. **downstream** of
  the hook, so the write propagates. Desyncs online — probe only.
- **Online** (`asdi_online_test.py`, VALIDATED): `0x8034E680`, displaced `lbz r0,7(r3)`
  = `0x88030007`, cave `0x803FA600` (74 words), counters `0x803FAA00`. Static DOL
  disassembly said c-stick bytes `4/5(r4)` finalize at `0x8034E604`/`0x8034E660`, both
  **upstream** of the hook (the only store after it is the R trigger at `0x8034E698`) —
  and the live run confirmed it: **a c-stick write here survives**.
  - The local player comes from the ODB (`*(*(r13-0x49E4)+0)`), not an index. That is
    what removes the offline attacker leak: PAD_Read builds only the local pad, so
    gate-player and pad-player are the same register chain.
  - ⚠️ **`0x8034E680` is already owned by the wavedash gecko** (which also contains the
    auto-L-cancel). A third claimant would clobber it — the ASDI logic must be *folded
    into that cave*, the way the L-cancel already was, not shipped as a separate gecko.
    (`asdi_online_test.py` takes the hook over on purpose and says so; it is a test
    harness, not a ship path.)

## Tech layer (offline VALIDATED 2026-07-25, `asdi_tech_offline.py`)

One **digital-R press (buttons bit `0x20`) on the last hitlag frame** — the only
frame that is both reachable in-cave and inside the 20-frame tech window
(PlCo `0xA230`) when the floorhug collision lands on the frame after hitlag.
`hitlag < 2.0` as a raw unsigned compare picks that frame (positive IEEE floats
order as ints), robust to fractional hitlag. The press propagates because the
displaced original `lhz r0,0(r25)` reloads the buttons *after* the cave runs.

Results across runs 2+3 (Marth vs Fox, 70–130%):

| Case | n | Outcome |
| --- | --- | --- |
| Tumble hit, `kb_y ≤ ~3` | 10 | **10/10 TECH (Passive)** — zero `DownBoundU` with the press live |
| Below tumble | 4 | clean `Landing` (~4f); the R press does nothing — harmless |
| Grounded victim (jab at low %) | 1 | gate refused at `air == 1` (the d-smash guard) — correct |
| `kb_y ≥ 4.2`, or hit mid-air | 3+1 | escaped — the ASDI never connects; this is the TDI layer's case |

Known ceilings (`ponytail:` comments in the probe): multi-hit moves press once
per hit, and presses < 40 frames apart are ignored by the game's lockout — drills
may not tech. Throws give the victim **no hitlag**, so the current design never
presses for throw knockdowns.

Offline-only so far: the probe drives the consumer hook. The ship path must move
the press to the producer side, where the analog→digital conversion at
`0x8034E250` means an analog write `≥ 0xAA` (or the button bit at `0x8034E2AC`)
is required, and the L/R button contention with wavedash + auto-L-cancel applies.

## Online results (2026-07-25, live peer, delay 1, 23 hits)

The gate fired on 294 of 302 local hitlag frames (the 8 rejects were grounded hitstun —
the `air == 1` clause doing its job). Injection visible in the engine's processed c-stick
(`+0x63C = -1.00`) from hitlag frame **2–3** and held through the last hitlag frame on
22/23 hits; the miss was a 4-frame-hitlag hit with `kb_y = -0.00` (nothing to hug), and
is as likely a lossy Python poll as a real miss.

| `kb_y` | n | maxRise | Verdict |
| --- | --- | --- | --- |
| ≤ 2.92 | 18 | 0.00 (two at 0.62) | floorhug |
| 3.28 | 2 | 0.00 / 19.88 | **the boundary** — one hugged, one escaped |
| 3.77 – 4.88 | 3 | 21.4 – 54.8 | escaped |

Same 3.0-unit ASDI reach the offline run bracketed (hug ≤ 2.99, escape ≥ 3.47) — the
online split straddles 3.28 because `kb_y` is only the vertical component at the sampled
frame, while what actually matters is 3.0 units against the *remaining distance to the
floor*. **4 of 23 hits escaped**: that is the TDI case, measured, not theorised.

**Outcome split is the tech argument.** Only `kb_y ≤ 1.00` / `hitstun ≤ 19` gave a clean
`Landing` (0x2A, ~4f). Everything above — 11 of 23 hits — gave **`DownBoundU` (0xB7),
the missed-tech bounce**, then `DownWaitU`. So without the tech layer the macro converts
a launch into a hard knockdown next to the opponent, which at those percents is a *worse*
position than the launch. Below-tumble floorhugs are the only ones that pay off today.

**Match end is safe.** After the game ended `reached` kept climbing ~2200 more executions
while `chain_ok` froze — the null/MEM1 guards rejected every one. No crash, no stale-pointer
write.

## SDI layer (pattern VALIDATED offline 2026-07-25, `asdi_sdi_offline.py`)

Purpose: drag the victim downward during hitlag so hits that start above the ground end
within the 3-unit ASDI reach by the last hitlag frame. Two pattern-race runs (one
pattern per victim-hit, 40+ instrumented hits, per-frame position deltas cross-checked
against the per-input SDI offsets `+0x18B8/+0x18BC`):

| Pattern (alternating per frame) | Down-rate | Result |
| --- | --- | --- |
| A — hold pure down | one −6.0 tick | control: no repeat on a hold |
| B — down ↔ neutral | −3.0/f | threshold re-entry, every other frame |
| C — downL ↔ downR (±0.71, −0.71) | −4.2/f | X sign flip re-arms EVERY frame |
| D — down ↔ down-away (45°) | −2.1/f **+ one-sided drift** | REFUTED: diagonal→cardinal return never re-arms |
| E — V ±0.35, −0.94 | −5.55/f | every frame |
| **F — V ±0.30, −0.95 (raw ±24, −76)** | **−5.7/f** | **WINNER: 95% of the −6 ceiling, drift self-cancels** |
| G — V ±0.25 | one tick | X below the 0.2875 deadzone → engine zeroes it (= a hold) |

Mechanism now in REFERENCE §2.8: a tick needs an *axis* freshly entering a directional
region; the tick applies the **whole stick vector including the stale held Y**; an X
sign flip is the free every-frame re-arm; the X deadzone (0.2875) bounds how steep the
V can go. Largest single-hit drag observed: **−58 units over 13 hitlag frames**.

**Integration status: SDI and ASDI stack — no stop gate needed.** The c-stick owns ASDI,
the analog stick owns SDI; the whole round-2 run held c-stick down while the pattern ran
and every floor-level hit still TECHED through it. SDI cannot cross into the floor (the
floor eats vertical ticks — see the header: ASDI is the *only* input that causes the
air→ground transition), and the probe's `FLOORED mid-hitlag` flag never fired — so the
drag can never produce a landing that beats the tech press. SDI all the way down; at
floor level the extra ticks are harmless (dy eaten, ±1.8 x wobble). The DownBounds seen
on dragged high hits are hits whose hug was still out of reach at hitlag end: knockback
carries Fox up/away and he falls back **in tumble 20+ frames later** — outside the
press's 20f window and inside the 40f lockout. Those are the TDI layer's cases (or a
future "press again when a tumble landing approaches" enhancement), not an SDI bug.

Remaining gate question for this layer: **offstage only** — don't drag a victim over the
void. (The engine partially self-protects by eating vertical SDI in some below-plane
geometries — observed, not characterized. Design this when folding into the combined cave.)

Ship shape: per-port toggle flips stick X between raw ±24 with Y = −76 on every gated
hitlag frame (same v1.5 victim gate); the ASDI c-stick hold and last-frame R press are
unchanged — the c-stick owns ASDI regardless of the analog stick.

## TDI layer (mechanic VALIDATED offline 2026-07-25, `asdi_tdi_offline.py`)

Trajectory DI, sampled from the analog stick. One rule, no quadrant cases: **stick =
perpendicular to `(kb_x, kb_y)`, whichever end has y < 0** — that reduces the launch
elevation on every trajectory, upward or sideways. Knockback velocity comes straight from
Player Data `+0x8C`/`+0x90`, so the `+0x1848` / `+0x1844` angle fields were never needed.

**The frame was the whole problem.** DI is read on `hitlag == 2`, *not* the `hitlag < 2.0`
frame the tech press uses (mechanism + proof → REFERENCE §2.9). Run 1 wrote TDI on the
tech frame and measured nothing from it — every rotation instead matched the **SDI**
pattern to ≤0.1°, i.e. the SDI down-drag had been doing incidental trajectory DI all
along. Moving the write to a `hitlag < 3.0` window fixed it.

| Run | Config | TDI ON | TDI OFF |
| --- | --- | --- | --- |
| 1 | TDI @ hitlag<2, SDI on | rotations match the **SDI** stick, not TDI | same as ON |
| 2 | TDI @ hitlag<4, SDI neutralized | **+15.0, +15.1, +15.1, +15.2** | **0.0 × 5** |
| 3 | TDI @ hitlag<3, SDI on (full stack) | **+15.0, +15.0, +15.2, +15.2, −16.7** | **0.0 × 8** |

Run 3's OFF arm reading 0.0 while SDI ran on frames ≥3 is what pins the sampled frame to
exactly 2, and makes **window 3.0 minimal** — one SDI frame given back, not two.

Direction is quantized to 8 sectors with integer compares on the raw float bits (no FPU,
no division, ~16 instructions; `ponytail:` in the source). Costs ~3° of the 18° cap.
Upgrade path if it ever matters: `fabs`+`fdivs` to saturate the true perpendicular, with
f0–f3 saved to scratch — the hook's live FPRs are unaccounted for.

⚠️ **Validated as a mechanic, NOT yet as an outcome.** The 15° rotation is real and
repeatable, but on this sample it did not convert vertical launches into techs — a 90°
launch rotated to 75° is still going up, and those hits still ended DownBound/escaped.
The payoff there is distance survived. Tech conversion should show on mid-angle launches;
that measurement has not been made.

Ship shape: `hitlag < 3.0` → TDI direction; `hitlag ≥ 3.0` → SDI flip; c-stick ASDI every
gated frame; digital R at `hitlag < 2.0`. Four writes, one cave, no conflicts.

## Online: full stack VALIDATED (2026-07-25, live peer, delay 1, 28 hits, `asdi_online_full.py`)

ASDI + SDI + the tech press, both producer hooks, one run. TDI off (see below).
**Nobody was holding the local controller** — the user played the peer side — so every
R press was the macro's. That makes the tech column a clean natural control, not an A/B.

| Verdict | n |
| --- | --- |
| **TECH** (Passive / StandF / StandB / Wall) | **13** |
| missed tech (DownBoundU/D, DownWaitD) | 5 |
| clean Landing (below tumble, no tech needed) | 3 |
| escaped / other | 7 |

**13 of the 18 hits that produced a tech situation teched (72%)**, against the 11-of-23
`DownBoundU` hard knockdowns the ASDI-only online run produced. That is the tech layer
paying for itself online. Gate: `asdi=342 sdi=342 tech=118 tdi=0`, `cstickY = -1.00` on
198 sampled hitlag frames. User confirmed no desync (eyeball — the standing caveat).

Measured rotations were mostly ~0 with several at **±4.1 and ±14.1/−14.3** — SDI's
*incidental* DI, parity-dependent, exactly the bimodal split §2.9 predicts, reproduced
through the delay buffer. Independent confirmation that the SDI stick lands on the
`hitlag == 2` read frame online.

### Delay compensation is the whole online difference

A pad byte written at a producer hook is consumed `delay` frames later, so every
frame-targeted layer shifts (general rule → REFERENCE §2.10):

| Layer | Targeting | Online window |
| --- | --- | --- |
| ASDI | held all of hitlag | none needed — this is why it worked online first |
| tech | last hitlag frame | `hitlag < 2.0 + delay` |
| TDI | one frame, engine `hitlag == 2` | `hitlag < 3.0 + delay` |
| SDI | cadence only | takes whatever TDI leaves |

Both windows are poked as float bits from Python off `ODB+0x21`, so they stay sweepable
the way the offline TDI window was. **TDI is off by default online**: at delay 1 a
4-frame hitlag leaves SDI a single frame, and SDI's drag is the layer with proven
benefit while TDI's is not (open item 1).

Two things this run forced, both now in the script:

- **SDI alternation comes from frame-counter parity (`0x80479D60` bit 0), not a stored
  toggle byte** — REFERENCE §3.4, data in `0x803FAxxx` is not rollback-safe, and the
  frame counter is engine state that rolls back correctly. Also one instruction shorter.
- **Wait for the meta-flush gecko to *respond* before writing code**, not just for its
  hook to read as a branch: `iw.wait_for_meta_flush_alive`, plus a retry around each
  flush. Its control plane is in `0x803FAxxx` too.

## Open items

1. **TDI outcome measurement** — does the layer actually convert hits? Needs mid-angle
   launches (the vertical ones it demonstrably does not save) and a tech-rate A/B, not a
   rotation A/B. The rotation question is closed; this one is open.
2. ~~**Tech layer**~~ — **DONE offline** (10/10) **and online** (13/18, section above).
   Ported producer-side as digital **R** via `oris r0,r0,0x20` at `0x8034E2AC`, which
   sidesteps the analog `≥ 0xAA` threshold entirely — the auto-L-cancel deliberately
   writes `0x80` to stay below it and wavedash owns digital L for the airdodge, so R is
   the only uncontended button. Lockout facts (measured PlCo: window `0xA230` = 20f,
   dead window `0x9FFC` = 40f) are what make the last-hitlag-frame press right.
   Remaining: the 5 online misses were all DownBound/DownWait on 91-frame arcs — the
   late-landing case the parked "press R again as a tumble landing approaches" idea
   targets, not a mistimed press.
3. ~~**SDI layer**~~ — **pattern DONE offline** (section above): F-pattern raw (±24, −76)
   at −5.7 units/frame. (The originally planned down ↔ down-away alternation measured
   WORST — see the race table.) SDI + ASDI stack; no near-floor stop gate needed. Still
   to do: the **offstage guard** decision (don't drag a victim over the void), then fold
   into the combined cave and port producer-side with the rest.
   The guard question now covers TDI too, and is sharper there: the downward perpendicular
   has **two ends**, and picking by `sign(kb_x)` is stage-blind — it can rotate a victim
   toward the nearer ledge instead of toward centre stage.
4. Online delay margin: minimum measured hitlag is 4 frames, so at delay 3 the injection
   lands on the ASDI sample frame itself with **zero** margin.

## Gotchas that cost time here

- **`install_meta_flush(h)` must be staged before `install_gecko_c2`.** Without it,
  `seed_snapshot`'s save+overlay+load round-trip wedges the CPU — even for a 2-instruction
  no-op payload (bisected in `bisect_asdi.py`). Failure looks like a payload crash and
  is not one.
- **Kill your own Python between runs, not just Dolphin.** A probe still on its timer
  holds the dme attach and the next `dme.hook()` fails.
- **`addi rD, r0, N` is not an increment.** An `rA` field of 0 means literal zero, so
  `addi r0,r0,1` assembles to `li r0,1`. Capstone readback caught it; hand-trusting the
  hex would not have.
- Re-resolve Player Data every frame — a death and respawn moves the pointers.
