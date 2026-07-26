# REFERENCE.md — Single Source of Truth

Technical reference for the Melee gecko-macro project. **SSBM NTSC 1.02 (GALE01)**
on Slippi Dolphin, macOS. Every address/offset/rule the macros depend on lives
here, once. Scope boundaries:

- **Per-macro state** (what's shipped, pending tests) → `docs/STATUS.md` + `docs/macros/`.
- **History / narrative** → `docs/archive/`.
- **Full raw data** → `SSBM memory address sheet/*.csv` (see §6 — always the first stop for anything not in this file).

Conventions: hex literals are `0x`-prefixed. PPC instructions are stored as
big-endian natural ints in Python lists (e.g. `0x3D80803F` = `lis r12, 0x803F`)
and written via `dme.write_word` / `harness.write_words`.

---

## 1. Memory map

### 1.1 Frame counters

| Address | What | Notes |
| --- | --- | --- |
| `0x80479D60` | Global frame counter | Resets between scenes; may not tick during transitions. Keeps ticking through hitlag (see §2.5). |
| `0x804D7420` | Power-on frame counter | +1 every frame, never resets. |

`Harness._pick_frame_counter()` auto-selects whichever is advancing at
`seed_snapshot()` time.

### 1.2 Scene controller — `0x80479D30`

The scene controller word is at **`0x80479D30`**. Byte 0 = current **major**
scene ID, byte 3 = current **minor** scene ID. Slippi's `getMinorMajor` macro
(`vendor/slippi-ssbm-asm-master/Common/Common.s`) computes
`((w << 8) | (w >> 24)) & 0xFFFF`, i.e. value = `(minor << 8) | major`.

> **WARNING — legacy address error.** Older docs and gecko-code comments in this
> repo cite `0x80489D30`. That is WRONG: the disassembly reads
> `lis r7, 0x8048; lwz r7, 0x9D30(r7)`, and `0x9D30` sign-extends to `−0x62D0`,
> so the effective address is `0x80480000 − 0x62D0` = **`0x80479D30`**.

Scene IDs (`getMinorMajor` value; constants in `Common.s` / `Online/Online.s`):

| Value | Scene |
| --- | --- |
| `0x0208` | **online in-game** (the target for online macros) |
| `0x0008` | online CSS (connected, match not started) |
| `0x0108` | online SSS |
| `0x0308` | online results |
| `0x0408` | online VS |
| `0x0202` | offline VS in-game |

### 1.3 Player entity chain

```
0x80453130 + port*0xE90   → GObj pointer      (port 0-indexed: P1=0x80453130, P2=0x80453FC0)
*(GObj + 0x2C)            → Player Data ptr   (REQUIRED indirection — GObj is not Player Data)
```

All Player-Data-relative offsets below need this double-indirection. From Python
use `Harness.player_data_ptr(port)` — **the Python API is 1-indexed (P1=1)**,
while the raw math above is 0-indexed; don't mix the conventions (`entity_ptr(0)`
raises). In a cave do it by hand and **MEM1-check
every pointer before dereferencing** (`srwi tmp,ptr,24; cmplwi tmp,0x80; bne
bail`) — during scene transitions and rollback the pointers hold garbage and an
unchecked deref crashes Dolphin.

**Re-resolve Player Data every frame you observe.** A death + respawn moves the
struct; a pointer cached at startup silently reads the wrong (or freed) memory
after the first KO (contaminated an ASDI run, 2026-07-26). `observer.players(h)`
does this correctly.

> **Two distinct `0x2C` facts — do not merge them.**
> `GObj + 0x2C` = pointer to Player Data (`Entity_Data_Offsets.csv`).
> `Player Data + 0x2C` = **facing direction** (float: `1.0` right, `−1.0` left;
> `Char_Data_Offsets.csv`). Both are correct; they are different structs.

Player Data offsets:

| Offset | Field | Notes |
| --- | --- | --- |
| `+0x04` | character ID (word) | Fox=`0x01`, Marth=`0x12` |
| `+0x0C` | port ID (byte, 0-indexed) | compare against ODB local index for the netplay gate (§2.6) |
| `+0x10` | action state ID (word) | mask low 16 bits |
| `+0x2C` | facing direction (float ±1) | see warning above |
| `+0x8C` / `+0x90` | attack-induced knockback velocity X / Y (floats) | `kb_y ≤ ~3.0` is the ASDI-floorhug reach (`docs/macros/asdi_floorhug.md`) |
| `+0xB4` | Y position (float) | frozen during hitlag — but only to ~1e-4, see §2.5 |
| `+0xE0` | ground/air flag (word: 0=grounded, 1=airborne) | during hitlag an *aerial attacker* is also 1 — use `+0x2340` to tell victim from attacker |
| `+0x63C` | engine-processed C-stick Y (float −1..1) | the observable that proves a c-stick injection reached the engine |
| `+0x650` | processed analog trigger | what the engine / L-cancel check reads |
| `+0x65C` | processed buttons (post-conversion) | 32-bit; what UnclePunch-style macros read |
| `+0x680` | L/R press timer ("frames since L/R pressed") | tracks **L/R only, NOT Z or light-analog** — misleading observable for Z-cancels (stays 255 while a Z-cancel works) |
| `+0x894` | Action State Frame Counter (float, resets to `1.0` on each new state) | **FREEZES during hitlag.** Rollback-safe (it's game state). `+0x3E8` is the *Sub*-Action State Frame Counter — a different float; don't confuse. |
| `+0x834` | last-landed Y position (float) | with `+0xB4`: near-ground check — real float compare, never exact (§2.5) |
| `+0x195C` | hitlag counter (float, counts down; `!= 0` ⇒ in hitlag) | `+0x2219 & 0x04` is an alternate hitlag flag |
| `+0x2340` | hitstun counter (float, frames remaining) | ⚠️ **NOT a victim/attacker discriminator by itself**: after a player has been hit once, the counter decays to a parked **denormal `0x00000001`** (≈1e-45) instead of 0 and stays there — prints as `0.00` but passes `cmpwi != 0` (raw-bits proof 2026-07-25, `asdi_tech_offline.py` run 3; this denormal is what the earlier "43/43 attacker rows are 0" measurement missed and what produced the bogus 61/137 "attacker leak"). The definitional victim check is **action state in the Damage range `0x4E`–`0x5B`**; keep `hitstun != 0` only as belt-and-suspenders. |
| `+0x25FF` | `LCancelStatus` (u8: 0=none, 1=success, 2=fail) | the direct per-landing L-cancel observable (from Slippi `Recording.s`); prefer over landing-state duration or `+0x680` |

**Float→int decode without FPU** (for `+0x894`, `+0x195C`, any integer-valued
float read in a cave): `n = (0x800000 | (bits & 0x7FFFFF)) >> (150 − exponent)`
where `exponent = (bits >> 23) & 0xFF`.

**Trust empirical reads over CSV descriptions** for any field you can observe:
e.g. `Char Data + 0x2354` ("Landing Lag Divisor") does not change on Fox
L-cancels in this Slippi build.

### 1.4 ODB (Online Data Buffer)

`odb = *(r13 − 0x49E4)` (`OFST_R13_ODB_ADDR`,
`vendor/slippi-ssbm-asm-master/Online/Online.s`). Layout:

| Offset | Field |
| --- | --- |
| `+0` | `ODB_LOCAL_PLAYER_INDEX` (u8) — **use this** for the local player's port |
| `+1` | `ODB_ONLINE_PLAYER_INDEX` (u8) |
| `+2` | `ODB_INPUT_SOURCE_INDEX` (u8) — the local input's *stack slot*, **NOT the controller port** (observed 0 even when local player was P2). Do not use for the GObj lookup. |
| `+3` | `ODB_FRAME` (u32) |
| `+0x21` | `ODB_DELAY_FRAMES` (u8, the online input delay) — read by the wavedash cave (`lbz rX, 0x21(odb)`) |
| further | full layout in `vendor/slippi-ssbm-asm-master/Online/Online.s` |

**Local player's port at runtime: `port = *(odb + 0)`**, then the §1.3 GObj
lookup. The local port varies by host/guest (observed both 0 and 1 across
sessions) — never hardcode it. Observed `r13 = 0x804DB6A0`.

### 1.5 Action states (selected)

| State | Meaning |
| --- | --- |
| `0x000E` | Wait (standing) |
| `0x0018` | KneeBend (jumpsquat) |
| `0x0019`–`0x0022` | JumpF/JumpB/Fall etc. (airborne, no attack); `0x001D` Fall, `0x0020` FallAerial (aerial ended in air — digital-trigger airdodge risk) |
| `0x0041`–`0x0045` | aerials NAIR/FAIR/BAIR/UAIR/DAIR |
| `0x0046`–`0x004A` | LandingAir N/F/B/Hi/Lw (landing lag; Fox NAIR ≈15f uncancelled → ≈7f cancelled) |
| `0x00B2`–`0x00B6` | Guard / shield states |
| `0x00D4`–`0x00D6` | Catch / CatchDash / CatchTurn (grab) |
| `0x00EC` | EscapeAir (airdodge) |
| `0x0155`+ | character-specific; Fox shine: `0x0168` ground startup, `0x0169` ground loop, `0x016D` aerial startup, `0x016E` aerial loop, `0x0170` aerial fall |

Full list: `Action_State_Reference.csv`.

### 1.6 Processed controller region — read-only for us

`0x804C1FAC` = Controller 1 Digital Data (stride `0x44` per port; bit layout
`xxxx xxxx UDLR UDLR xxxS YXBA xLRZ UDRL`). **dme writes here race Dolphin's
input pipeline and do not propagate** — the region is rewritten every poll. It
is fine to *read*; for injection use the hooks in §2.

Likewise, `scenario.force_action_state` animates a state but applies **no
physics** — usable for self-contained triggers, useless for real motion. The
golden rule: **inject inputs by editing pad data at a hook, never by writing
player-data / action-state fields** (direct state writes also desync online).

---

## 2. Input pipeline & hooks

### 2.1 The pipeline and the desync rule

```
SI hardware
  → PAD_Read (0x8034DA00)                     raw local controller read
     ├ 0x8034E2AC  raw SI → PADStatus buttons    *** producer digital hook ***
     └ 0x8034E680  post-calibration analog/stick *** producer analog hook ***
  → HSD_PadRenewRawStatus (0x80376A20–A28)
     └ TriggerSendInput (0x80376A28): scrapes the local PADStatus, ships it over
       EXI to the peer, then OVERWRITES local + remote pad slots from the delay
       buffer / network.  (Slippi-owned; do NOT hook.)
  → engine simulation, incl. HSD_PadRead (0x803775B8)   *** consumer side ***
```

Slippi is deterministic lockstep with rollback: both clients simulate the same
inputs. **Producer-side** edits (upstream of the EXI scrape) change the input
*before* it is serialized, so the peer receives the edited input and both
clients simulate identically — netplay-safe. **Consumer-side** edits (at or
after `0x803775B8`) change only the local simulation — **guaranteed desync
online**. Offline there is no peer, so the consumer hook is fine (and
convenient).

Rollback interaction: `SkipNewInputFetchOnRollback` (`0x80376A20`) skips the
`PAD_Read` call during rollback re-simulation, so producer hooks fire **once per
real frame**, not per replayed frame — a producer-hook cadence is inherently
rollback-safe.

### 2.2 The injection hooks

| Environment | Input type | Hook | Entry state | Displaced original | Branch-back |
| --- | --- | --- | --- | --- | --- |
| OFFLINE | anything | `0x803775B8` (`HSD_PadRead`, consumer) | `r24` = 0-indexed port, `r25` = that port's pad in a **5-entry rotating ring**: `0x8046B108 + k*0x30 + r24*0xC` (4 × 0xC-byte pads per entry; ring-logged 2026-07-25, two per-port call sites LR `0x80377538`/`0x803778A8`). **`r24` IS the pad owner** — the old "61/137 attacker leak" was the parked-denormal hitstun gate (§1.3) passing for the attacker, not a register mismatch. Verify structurally anyway: `(r25 − 0x8046B108 − r24*0xC) < 0xF0` and 16-aligned drops any rogue call path. Still never port `r24` indexing online (use the ODB). | `lhz r0,0(r25)` = `0xA0190000` | `0x803775BC` |
| ONLINE | digital buttons | `0x8034E2AC` (producer, "Altimor's slot") | `r0` = raw SI word, button bits in high 16 → `oris r0,r0,BIT` sets BIT | `rlwinm r0,r0,0x10,0x12,0x1f` = `0x540084BE` | `0x8034E2B0` |
| ONLINE | analog trigger / stick | `0x8034E680` (producer, post-calibration) | `r3` = calib ptr, `r4` = PADStatus → `stb val, N(r4)` | `lbz r0,7(r3)` = `0x88030007` | `0x8034E684` |

Register preservation: at `0x8034E2AC` preserve `r0, r4, r5, r13`; at
`0x8034E680` preserve `r3, r4, r13`. Save/restore (stack frame) everything else
you touch; a displaced original that clobbers `r0` frees `r0` for your logic.

`0x8034E2AC` is **buttons only** — PAD_Read writes sticks/triggers *after* the
button word, so `oris` there can't set them. Analog/stick values go in at
`0x8034E680`, after the per-port calibration finalizes them and before the
builder returns.

At `0x803775B8` (offline hook), pad bytes `2..9(r25)` are all consumed
**downstream** (`0x803775C0`–`0x803775F8`) — a byte write at the hook
propagates to every consumer; only `0xA(r25)` is read before it.

### 2.3 Pad struct layout + button bits

Same byte offsets for the PADStatus PAD_Read builds at `r4` (online hooks) and
the struct HSD_PadRead consumes at `r25` (offline hook). This is a **different
layout** from the 32-bit `0x804C1FAC` region (§1.6).

| Off | Field | Notes |
| --- | --- | --- |
| `+0x0` | u16 buttons | bits below |
| `+0x2` | s8 stick X | centered (PAD_Read subtracts `0x80`), then calibrated |
| `+0x3` | s8 stick Y | |
| `+0x4` | s8 c-stick X | |
| `+0x5` | s8 c-stick Y | |
| `+0x6` | u8 analog L trigger | `< 0xAA` ⇒ no digital bit (§2.4) |
| `+0x7` | u8 analog R trigger | |
| `+0x8` | u8 analog A | rarely used |
| `+0x9` | u8 analog B | rarely used |
| `+0xA` | status/err | |

16-bit button bits (`[+0x0]`, and what `oris r0,r0,BIT` sets at `0x8034E2AC`):

| Button | Bit | Button | Bit |
| --- | --- | --- | --- |
| A | `0x0100` | Start | `0x1000` |
| B | `0x0200` | L (digital) | `0x0040` |
| X | `0x0400` | R (digital) | `0x0020` |
| Y | `0x0800` | Z | `0x0010` |
| D-pad Up/Down | `0x0008` / `0x0004` | D-pad Right/Left | `0x0002` / `0x0001` |

### 2.4 PAD_Read internals (producer side)

Mapped by the (since-deleted) `disasm_lcancel_analog.py` probe:

- **`0x8034E220`–`0x8034E2A4`**: builds analog bytes `6/7/8/9(r4)` from raw SI in
  `r5`, then the **analog→digital trigger conversion** at ~`0x8034E244`:
  `6(r4) ≥ 0xAA` → set digital L bit `0x40` (at `0x8034E250`); `7(r4) ≥ 0xAA` →
  `0x20`. So an injected analog value **`< 0xAA` sets NO digital bit** — it
  L-cancels via the analog path but cannot airdodge or trigger anything keyed to
  digital L/R. Then centers sticks `2..5(r4)` by subtracting `0x80`.
- **`0x8034E2A8`–`0x8034E69C`** (`blr` at end): the PADStatus builder both online
  hooks live in. `0x8034E2AC` sets the buttons from `r5`; a controller-type/
  scene value at `*(r13 − 0x5A60)` (read at `0x8034E2D0`) selects how `4..9(r4)`
  are filled; a per-port calibration pass (`0x8034E4B4`–`0x8034E698`, table at
  `0x804A89B0 + port*0xC`) clamps/offsets each axis. Analog L `6(r4)` is
  finalized at `0x8034E67C`, R `7(r4)` at `0x8034E698` — hence the `0x8034E680`
  hook. C-stick bytes `4/5(r4)` finalize at `0x8034E604`/`0x8034E660`, both
  **upstream** of that hook — so a c-stick write at `0x8034E680` survives to
  the transmitted pad (validated live 2026-07-25); only R lands after it. Online this routine runs for the **local** controller only (remote
  inputs arrive via EXI), so a hook here edits only the transmitted local input —
  no per-port gate needed on the *injection*; gate on the local player's *state*
  via the ODB.
- **Airborne trigger / airdodge check: `0x8008E498`.** Fires only for action
  states `0x19`–`0x26` and `0xEC`, and reads the **digital** L/R timer
  (Player Data `+0x680`) — not analog, not Z-as-trigger timing. This is why a
  digital L/R (or Z, which also re-attacks since Z = A+trigger) rising edge in an
  airborne non-attack state airdodges/re-attacks, while a light analog trigger
  cannot. The landing L-cancel detection is a separate (undisassembled) routine
  that sets `LCancelStatus` `+0x25FF` on the landing frame.
- The analog byte → processed trigger (`+0x650`) mapping is **non-linear**
  (calibration subtracts a per-port minimum): observed `0x80` → `0.91`,
  `0xA0` → `1.0`.

### 2.5 No input buffer → the PULSE rule

The game has **no input buffer**: a held button (down two frames in a row)
registers as ONE press; re-triggering needs a fresh **rising edge** (0→1). True
for analog triggers too (held analog does not re-trigger). So to act on multiple
frames a macro must **pulse** — release between presses, gated on a frame
parity/counter. Every-other-frame (`1,0,1,0`) works; so does 1-press/6-release.

**Hitlag:** while `+0x195C != 0`, the Action State Frame Counter `+0x894`
freezes but the global frame counter `0x80479D60` keeps ticking. A pulse cadence
that must survive hitlag (e.g. on aerials that connect) must anchor to the
**global** counter, not `+0x894`. Game mechanic: a trigger/Z press on any hitlag
frame is re-applied through the rest of hitlag and stays active ~6 frames after
it ends.

Hitlag length is `3 + dmg/3` frames, capped at 20 (PlCo constants
`0xA174`–`0xA184`; electric ×1.5, crouching victim ×0.667). Position (`+0xB4`)
is also frozen during hitlag — **but only to ~1e-4**: never gate on exact float
equality (a bit-exact `y == land_y` compare rejected 100% of real hits in the
ASDI probe; log floats with their raw bits — `observer.ffmt` — before inferring
equality).

### 2.6 Netplay-safety gate pattern

The canonical guard for "only act online, only on my character" (disassembled
from the Flash-Red-on-Failed-L-Cancel code — whose original in-code comments
cite the erroneous `0x80489D30`; see §1.2):

```assembly
# scene check: online?
lis    r7, 0x8048
lwz    r7, -0x62D0(r7)      # load 0x80479D30 (scene controller)
rlwinm r7, r7, 8, 16, 31    # getMinorMajor
cmpwi  r7, 0x208            # online in-game?
bne    not_online
# local-port check
lwz    r7, -0x49E4(r13)     # ODB ptr
lbz    r7, 0(r7)            # ODB_LOCAL_PLAYER_INDEX
lbz    r8, 0xC(r5)          # port of the player being processed (r5 = Player Data)
cmpw   r7, r8
bne    skip                 # not our character
```

Reference proof-of-concept — Altimor's "Swap X/Z — Netplay Safe" (three `rlwimi`
that swap the X `0x0400` and Z `0x0010` bits at the producer hook):

```
C234E2AC 00000002
5000843E 5000B56A
500056F6 00000000
```

### 2.7 Taken hooks — do not collide

| Address | Owner |
| --- | --- |
| `0x803775C0` | **meta-flush gecko** (this project; vanilla `lbz r0,2(r25)` = `0x88190002`). The neighboring `0x803775B8` is the free offline injection slot. |
| `0x80376A20` / `0x80376A24` / `0x80376A28` | Slippi: `SkipNewInputFetchOnRollback` / `ApplyInGameDelay` / `TriggerSendInput` |
| `0x80375380`, `0x8015FF60`, `0x80346314`, `0x801A4CB4`, `0x8016D294`, `0x80068EEC`, `0x801C154C` | Slippi bootloader/common (Bootloader main, AddHeap, EXISpoof, AllocSceneBuffer, IncrementFrameIndex, InitPlayerData, InitStageData) |
| `0x801A4DE4`, `0x801A5014`, `0x8016E748`, `0x8016D26C` | Slippi online/per-frame (StartEngineLoop, updateFunction branch, InitOnlinePlay, PauseCounter) |
| `0x8006B0E0`, `0x8006DA34`, `0x8016E74C`, `0x8016D884` | Slippi recording (SendGamePreFrame, SendGamePostFrame, SendGameInfo, SendGameEnd) |

Complete inventory: grep `vendor/slippi-ssbm-asm-master/**/*.asm` for
`# Address:` headers.

### 2.8 SDI mechanics (measured 2026-07-25, `asdi_sdi_offline.py`, 40+ instrumented hits)

- Displacement per SDI input = engine stick vector × **6** (PlCo `0xA498`);
  requires stick magnitude ≥ **0.7** (PlCo `0xA490`). Direct per-input evidence:
  Char Data `+0x18B8`/`+0x18BC` (SDI x/y offset).
- **Re-arm rule:** an input fires only on a frame where a stick *axis* freshly
  enters a directional region. Hold = ONE input. down↔neutral re-arms every
  OTHER frame (−3.0 u/f down). An **X sign flip re-arms EVERY frame** — no
  intermediate neutral frame needed. Diagonal→cardinal return (down↔down-away)
  does NOT re-arm — that pattern is refuted (−2.1 u/f + one-sided drift).
- **Whole-vector arming:** a tick armed by the fresh X applies the full
  (x, y) × 6 **including the stale held Y**.
- X must clear the **0.2875 deadzone** to arm: raw ±24 (0.30) re-arms every
  frame; raw ±20 (0.25) is zeroed by the engine → reads as a pure-down hold.
- Raw pad coords are **radially clamped** to the unit circle before use
  ((+80,−80) → (0.707,−0.707)); out-of-bounds synthetic coords buy nothing.
- **Fastest downward drag: alternate raw (±24, −76)** = (±0.30, −0.95) →
  **−5.7 u/frame**, ±1.8 X wobble self-cancels. Beats corner alternation
  (−4.2) and down↔neutral (−3.0). 95% of the unreachable −6.0 hold ceiling.
- **The floor eats vertical SDI** at/below ground level (dx still applies), and
  occasional upward push-out artifacts appear near floors/platforms. Vertical
  SDI is also eaten in some below-plane/offstage geometries (seen, not
  characterized). Measure dy only on victims well above ground.
- The c-stick never SDIs; ASDI stays c-stick-owned regardless of the analog
  stick's SDI pattern.

### 2.9 DI (trajectory DI) mechanics (measured 2026-07-25, `asdi_tdi_offline.py`, 3 runs)

- **DI is read from the pad on the `hitlag == 2` frame.** ONE frame, and it is
  *not* the `hitlag < 2.0` frame the tech press uses. Established by
  elimination, not assumption:
  - run 1 wrote TDI only at hitlag 1, SDI on frames ≥2 → every rotation matched
    the **SDI** stick to ≤0.1° (so the read frame is ≥2, and 1 is not it);
  - run 3 wrote TDI on frames {2,1}, SDI on ≥3 → with TDI **off** the rotation
    was exactly **0.0** on 8/8 hits (so frames ≥3 are not it either).
  The tech press does not care (20-frame window); DI is one frame or nothing.
- Rotation = **18° × p²**, where `p = |stick| · sin(stick_angle − traj_angle)`
  and the stick is radially clamped to 1.0 as in §2.8. Predicted vs measured
  agreed to ≤0.6° over 12 instrumented hits — this formula is now a usable
  oracle, not a hypothesis.
- The rotation is applied **in place** to Player Data `+0x8C`/`+0x90` between
  the last hitlag frame and the next, with **magnitude preserved**. That
  invariant is how a real DI is distinguished from a floor collision, which
  either zeroes `kb_y` or projects the vector onto the ground plane (the
  projection preserves magnitude too, so test `kb_y != 0.0`, not magnitude
  alone — both filters were needed to clean the measurements).
- Max downward rotation needs the stick **perpendicular to the trajectory, on
  the end with y < 0**. Quantizing to 8 sectors via integer compares on the raw
  float bits (positive IEEE floats order as ints — no FPU, no division) costs
  ~3°: measured **15.0–16.7°** against the 18° ceiling, on every clean hit.
- ⚠️ **A rotation is not a floorhug.** A 90° launch rotated to 75° is still
  going up; those hits still ended DownBound/escaped. On near-vertical launches
  DI buys *distance survived*, not a tech.
- ⚠️ **The perpendicular has two ends and the choice is stage-blind.** For a
  near-vertical launch the two downward perpendiculars point left and right;
  picking by `sign(kb_x)` (as the current cave does) can rotate a victim
  *toward* the nearer ledge. Stage-relative selection is unresolved — same open
  question as the SDI offstage guard.

### 2.10 Producer-side delay compensation (validated 2026-07-25, `asdi_online_full.py`)

A pad byte written at a producer hook (§2.2) is not consumed on the frame it is
written: it goes through Slippi's delay buffer and reaches the engine `delay`
frames later. **Every frame-targeted layer therefore shifts by `delay`; layers
that merely *hold* a value do not.** Read the delay at runtime from
`ODB + 0x21` (`ODB_DELAY_FRAMES`, §1.x) — it is fixed for a match but varies by
connection, so never bake it in.

Writing while `hitlag < W` lands on engine hitlag `W-1-delay … 1-delay`, so to
cover engine frame `k` use **`W = k + 1 + delay`**:

| Intent | Engine frame | Window |
| --- | --- | --- |
| hold through hitlag (ASDI c-stick) | all | no compensation needed |
| act on the last hitlag frame (tech press) | `1` | `hitlag < 2.0 + delay` |
| act on the DI read frame (§2.9) | `2` | `hitlag < 3.0 + delay` |

Two consequences worth planning around:

- **Frames are zero-sum.** A window of `k+1+delay` also writes every frame below
  it, stealing them from whatever else owns that byte. At delay 1 a 4-frame
  hitlag leaves a `hitlag < 4.0` layer exactly one frame for anything else.
- **Do the float arithmetic in Python, not the cave.** Poke the threshold as
  float *bits* (compared with `cmplw` — positive floats order as ints); you
  cannot add an integer `delay` to a float bit pattern. For a shipped gecko that
  must self-configure, use a delay-indexed table of 8 floats + `lwzx`, not FPU.

⚠️ **Minimum hitlag is 4 frames**, so a layer needing engine frame 2 has zero
margin at delay 3.

⚠️ **A meta-flush hook that reads as a branch is not a responsive gecko.** After
the F4 slot-4 load there is a window where it is patched but not yet firing, and
its control plane lives in `0x803FAxxx`, which rollback does not reliably
preserve (§3.4). Call `instr_writer.wait_for_meta_flush_alive` before the first
`write_instrs` and retry each flush — re-arming is idempotent, and the
alternative is losing a whole online session to a 1-second timeout.

---

## 3. Injection paths

Three ways to get PPC code into the game; two work.

### 3.1 Path 1 — boot-time C2 gecko (`Harness.install_gecko_c2`)

Stages C2 codes into a tmp `GameSettings/GALE01r2.ini`; Slippi's bootloader
reads the INI at boot, copies each body into its own code cave, and flushes the
icache. **Must be called before `launch()`.** Survives savestate loads offline
(the codehandler reinstalls post-load) — but see the F4 wipe rule in §3.4.
Format INI lines with `gecko_c2_lines(hook, logic, displaced, name)`.

**Precondition: stage the meta-flush gecko too, first**
(`instr_writer.install_meta_flush(h)` before your `install_gecko_c2` calls).
Without it, `seed_snapshot()`'s save+overlay+load round-trip deterministically
wedges the CPU — even for a 2-instruction no-op payload — and the symptom is a
generic `TimeoutError: CPU never started ticking` that looks like a payload
crash (bisected in `bisect_asdi.py`, 2026-07-26; mechanism unexplained). The
harness raises a self-explanatory error if you forget.

**Size limit:** the harness's minimal boot codehandler cave is small — a large
C2 (~50 words) silently fails to install (hook stays vanilla); Altimor-sized
codes install fine. The user's full Slippi codeset has a bigger cave, so big
shipped geckos work there. Consequence: validate a big macro's *logic* via the
dme path (§3.2) and its *C2 packaging* separately with a small probe
(`verify_codehandler_displaced.py`).

### 3.2 Path 2 — runtime dme + meta-flush (`instr_writer`)

Install ONE meta-flush gecko at boot (hook `0x803775C0`) whose only job is to
`dcbf`/`sync`/`icbi`/`isync` a dme-controlled range on demand. After that,
`write_instrs` / `patch_branch` can dme-write fresh PPC anywhere in MEM1 and
have it take effect within ~1 frame. **This is the iteration workhorse** —
change code without rebooting. Control plane (scratch in the debug region):

| Address | Field |
| --- | --- |
| `0x803FA440` | `FLUSH_REQUEST` — write `0xDEADBEEF` to arm; gecko clears to 0 when done |
| `0x803FA444` | `FLUSH_START` (inclusive) |
| `0x803FA448` | `FLUSH_END` (exclusive) |

Regenerate the paste-able gecko text with
`melee_harness.gecko_c2_lines(iw.META_FLUSH_HOOK, iw.META_FLUSH_LOGIC,
iw.META_FLUSH_ORIG, 'x')`; the canonical body lives in
`instr_writer.META_FLUSH_LOGIC`.

### 3.3 Path 3 — raw dme write to instruction memory: DEAD

Confirmed non-functional on Slippi Dolphin, even with `Core.CPUCore = 0` (pure
interpreter): the emulated CPU's instruction fetch never observes dme writes
without an explicit `dcbf`+`icbi` on the affected lines. (Proved by the
`diag_inject_no_savestate.py` diagnostic — deleted 2026-07-24, in the Desktop
archive tarball.) Don't retry this.

### 3.4 Savestate / wipe rules

- **Offline reset model:** no programmatic savestate API. Slot 2 is loaded once
  (user or synthetic F2); `seed_snapshot()` waits for in-game then snapshots all
  of MEM1 (24 MB); `restore_snapshot()` writes it back per iteration — reverting
  game state, patches, and frame counter together.
- **Runtime (Path-2) patches are wiped by `seed_snapshot()` and any savestate
  load** — both rewrite MEM1, and the snapshot predates the patches. Install
  runtime patches AFTER seeding; iterate by in-game cycling and re-`write_instrs`,
  never by reloading slot 2 mid-session. Boot geckos survive offline reloads
  (codehandler reinstalls them).
- **Online entry wipes boot geckos.** The harness enters online by F4-loading
  slot 4 (a savestate of the direct-connect menu), and loading a savestate wipes
  any gecko not present when the state was captured. **The F4-bake rule:** any
  gecko needed online (meta-flush for dev; the finished macro to ship) must be
  *baked into the slot-4 savestate* — add it in Slippi Manager, enter a match
  normally (NOT via F4), save state to slot 4. Baked-in geckos are part of the
  rollback baseline and survive the match.
- **Runtime code patches persist across rollback** online (validated); **data
  flags in `0x803FAxxx` are not reliably preserved** across rollback, even
  though the meta-flush control-plane scratch responded correctly in validation.
  Operative rule: for a runtime toggle online, patch a **code** instruction
  (`oris`↔`nop`, `stb`↔`nop`), not a data flag.
- **Not online-safe at all:** `seed_snapshot`/`restore_snapshot` (write MEM1),
  `bp.py` software breakpoints (spin halts the whole PPC core — offline/dev
  only), boot-installed geckos (F4-wiped).

### 3.5 C2 codehandler footgun — the eaten last word

The C2 codehandler **overwrites the body's LAST word with its branch-back** — it
does not append. A body whose displaced original (or any needed instruction) is
last loses it silently (symptom: garbled inputs — "A dead, stick acts as
D-pad"). `gecko_c2_lines` therefore always reserves a trailing `0x00000000`
branch-slot (+ a `nop` to keep the word count even); never hand-roll a C2 that
ends on a real instruction. The dme install path (`finalize_payload(logic,
hook, cave, displaced)` → `[logic][displaced][real branch]`) appends its own
branch and is immune. Verify any built C2's cave with
`verify_codehandler_displaced.py`.

### 3.6 Cave map

| Address | What |
| --- | --- |
| `0x803FA3E8` | `DEFAULT_CAVE` (debug-menu tables region, `0x1F04` bytes, safe to clobber). Only `0x58` bytes (22 words) sit below the control plane — a **>22-word cave here collides with it** and `flush_range` then corrupts the cave → crash. Start scratch/small caves at `+0x200`. (The meta-flush body itself installs as a boot C2 in the codehandler's own cave, NOT here.) |
| `0x803FA424` | JC-shine state counter (`candidate_d_standalone_v2.py`) |
| `0x803FA440`–`0x803FA44C` | **meta-flush control plane — never overlap** (§3.2) |
| `0x803FA470` | scratch shared by TWO offline macros: the `auto_lcancel/` cycle counter AND the offline wavedash `WD_PEND` latch — do not install both offline macros together (see `docs/macros/`) |
| `0x803FA600` | **recommended cave** for anything big or shared with online |
| `0x803FA800` | shipped wavedash digital-button cave (`make_wavedash_gecko.py`) |
| `0x803FC420` (`0x17FC`), `0x8022887C` (`0xB0`), `0x8032C848` (`0x38`), `0x8032DCB0` (`0x10C`), `0x8032ED8C` (`0x104`), `0x80393A5C` (`0x1B4`), `0x804D36A0` (`0x60`) | other free regions (more debug tables, unused code functions, develop-mode color table) — `Free_Memory.csv` |

Note: the boot codehandler allocates its OWN cave for Path-1 geckos — it does
not necessarily use `DEFAULT_CAVE`. (The `diag_cave_layout.py` probe that
discovered a gecko's actual landing spot was deleted 2026-07-24 — git history.)

---

## 4. PPC authoring traps

- **r0-as-rA:** in `addi`, `addis`, `lis`, `stmw`, and any load/store, an `rA`
  *field* of 0 reads as the literal value 0, NOT register r0. `addi r0, r0, 16`
  computes `16`. Use r3..r12 as base registers, never r0.
- **`lmw rD, d(rA)` with rA in [rD..r31] is undefined.** Restore r1 with a
  separate `lwz` after `lmw r2, ...` (see `bp.build_bp_handler`).
- **Register discipline (PPC EABI):** volatile = `r0`, `r3`–`r12` (clobberable
  scratch); non-volatile = `r13`–`r31` (save+restore); `r13` is the small-data-
  area pointer — **never clobber**. Plus the per-hook preservation sets in §2.2.
- **Hand-counted branch offsets are the #1 "gecko silently doesn't fire" bug.**
  Assemble with keystone and **capstone-verify the emitted words before
  flushing/launching** — disassemble the full payload and eyeball that branches
  land in-cave and the displaced original is present
  (`verify_v2_with_keystone.py` is the pattern). On this machine keystone needs
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib` prefixed on every run.
- **No FPU in repo caves** — decode integer-valued floats with the integer
  formula in §1.3.

---

## 5. dme / harness operational rules

### 5.1 One-time macOS machine setup

- **SIP disabled** (`csrutil status` → disabled). Without it every
  `task_for_pid` — hence every dme read/write — fails, regardless of
  entitlements.
- **Hardlink `Dolphin`** next to the real binary: `dme.hook()` scans for a
  process literally named `Dolphin`, but Slippi's executable is
  `Slippi Dolphin`. Create with
  `ln "Slippi Dolphin" Dolphin` in
  `~/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/`
  and verify the inodes match (`stat -f '%i %N' "Slippi Dolphin" Dolphin`).
  **Slippi updates/rebuilds wipe the hardlink** — recreate it when `dme.hook()`
  starts failing.
- **Accessibility permission** granted to the terminal/Python (synthetic
  keystrokes via CGEvent; `AXIsProcessTrusted()` confirms). Dolphin's Hotkey
  device must be `Quartz/0/Keyboard & Mouse`.
- **Agents: run every Dolphin-launching command with the sandbox disabled**
  (Claude Code: `dangerouslyDisableSandbox: true`). dme needs `task_for_pid` and
  the launch is a GUI app; a sandboxed run fails at the attach, which looks like
  a stale-process problem and is not one.
- macOS virtual keycodes for `melee_harness._send_key`: F1=122, F2=120,
  **F4=118**, Return/Enter=**36**, Left-Shift=56.
- Hard-coded paths live in `melee_harness.py`: `DOLPHIN_HARDLINK`, `ISO_PATH`,
  `USER_DIR`, `GAME_SETTINGS_INI`.
- The harness builds a tmp Dolphin user dir per launch (symlinking the real user
  dir except `GameSettings`/`Config`) and overrides: `GameSettings/GALE01r2.ini`
  (vendored minimal codeset — Slippi's defaults panic on savestate load with an
  IntCPU "Unknown instruction") and `Config/Dolphin.ini` with
  `Interface.UsePanicHandlers=False` (auto-dismisses the residual benign
  dialog). Online play still works through this: Dolphin layers the user INI on
  top of the app bundle's `Sys/GameSettings/GALE01r2.ini` (the
  `$Required: Slippi Online` codeset).

### 5.2 Process hygiene

- **`pkill -x Dolphin` returns before the process dies.** A lingering Dolphin at
  `dme.hook()` time makes dme attach to the dying/stale process and every read
  fails. Always `pkill -9 -x Dolphin` and **poll `pgrep -x Dolphin` until
  empty** before launching.
- **Kill your own harness Python between runs, not just Dolphin.** A
  backgrounded probe still on its timer holds the dme attach and the next
  `dme.hook()` fails; `pkill -9 -x Dolphin` does not touch it. `hook_dme()`
  detects a live prior holder via a pid lockfile and names it in the error.
- **`dme.hook()` must run on the main thread** — wrapped in a daemon thread it
  returns but `is_hooked()` stays False.
- **One Python process, launch to observe.** Re-attaching dme from a *new*
  process yields garbage/torn reads (scene reading `0x143F`, code addresses
  reading 0). Do launch + online entry + patch + observe in one script; leave
  Dolphin running between steps rather than closing the harness.
- **F2 sent too early is dropped** — wait for the power-on counter
  (`0x804D7420`) to start ticking before sending savestate hotkeys.
- **First Dolphin launches after a macOS reboot can die instantly with SIGKILL
  "Code Signature Invalid"** (crash report says `Taskgated Invalid Signature`,
  `dolphin.log` empty). It is the Rosetta AOT cache rebuilding post-reboot, not
  the hardlink and not SIP — it self-heals within ~15 min. Retry later; do not
  re-sign anything (measured 2026-07-25: two kills at boot+15min, clean
  launches at boot+30min with identical binary, env, and hardlink).

### 5.3 Read/write discipline

- **Throttle polling** (`time.sleep(~0.012)` between reads) — heavy polling
  detaches dme (symptom: `Could not read/write memory` raises). Re-`dme.hook()`
  on failure.
- **Majority-vote every read during an online match.** Rollback rewrites MEM1;
  any single read can be torn. Read N times, take the mode — including the scene
  word when confirming `0x0208`.
- The 24 MB `restore_snapshot()` write occasionally detaches dme; the harness
  re-hooks defensively.
- **The peer's screen is the only ground truth for desync.** The local side can
  look fine while desynced — confirm with the user (or peer machine) after every
  online run, even though producer-side edits shouldn't desync.

### 5.4 Online entry sequence

`F4` (load slot 4 = direct-connect menu with opponent pre-typed) → wait ~3 s →
`Enter` (search/connect) → wait ~15 s → confirm in-game by majority vote on the
scene controller (want `0x0208`). Landing at `0x0008` means the other machine
isn't in an active match — not a savestate problem; each F4+Enter is a fresh
connect. The Windows peer can be driven automatically
(`Harness.enter_online(peer=Peer())`, `peer/SETUP_WINDOWS.md`) or manually.

---

## 6. Authoritative sources — in precedence order

1. **`SSBM memory address sheet/*.csv`** — the full 1.02 data sheet
   (`Global_Addresses`, `Entity_Data_Offsets`, `Char_Data_Offsets`,
   `Function_Addresses`, `Free_Memory`, `Action_State_Reference`, `ID_Lists`,
   …). Search here FIRST for any address, offset, action-state ID, or
   free-memory region. Caveat: for observable fields, empirical measurement
   beats the CSV description (§1.3).
2. **`vendor/slippi-ssbm-asm-master/`** — the Slippi ASM source (moved from the
   repo root 2026-07-24). This is the mod the project builds on top of.
   **`Common/Common.s` is the authority for Slippi function addresses and scene
   constants** (e.g. `PadRead = 0x8034DA00`, the `getMinorMajor` macro, scene
   IDs); `Online/Online.s` for the ODB layout and online scene IDs;
   `Online/Core/TriggerSendInput.asm` and neighbors for online-hook internals.
   Vendored; do not modify.
3. **`vendor/gecko-master/`** — the `gecko` Go tool source (compiles `.asm` →
   `.ini` gecko codes; Go source only, the Windows `.exe` assemblers were
   deleted — macOS needs devkitPPC's `powerpc-eabi-as` for this path). The
   current loop bypasses it: raw PPC hex + keystone/capstone verification (§4).
4. Historical notes and full worked disassemblies: `docs/archive/` (banner-marked;
   the old `Project_Addresses.md` was deleted 2026-07-24 for carrying the missing
   `+0x2C` indirection and a wrong scene address — this file replaces it).
