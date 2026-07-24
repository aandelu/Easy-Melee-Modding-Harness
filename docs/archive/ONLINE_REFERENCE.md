> **HISTORICAL (archived 2026-07-24).** Superseded by docs/REFERENCE.md (which started from this file). Do not trust drifted copies of facts here.

# Online Macro — Quick Reference

Cheat sheet for [`ONLINE_MACRO_GUIDE.md`](ONLINE_MACRO_GUIDE.md). NTSC 1.02
(GALE01). All values verified this session unless noted "(from Slippi source)".

## Key code addresses

| Address | What | Notes |
| --- | --- | --- |
| `0x8034E2AC` | **Producer-side DIGITAL-button injection hook** (inside `PAD_Read`, "Altimor's slot") | Displaced original = `rlwinm r0,r0,0x10,0x12,0x1f` = **`0x540084BE`**. `oris r0,BIT` sets a button bit. Used by the historical digital-Z macro. |
| `0x8034E2B0` | instruction after the hook | branch-back target for the cave |
| `0x8034E680` | **Producer-side ANALOG-L injection hook** (the SHIPPED v4 macro) | PAD_Read finalizes the analog L byte `6(r4)` at `0x8034E67C` (per-port calibration) and the report-builder `blr`s at `0x8034E69C`. Hook here, write `6(r4)`. Displaced = `lbz r0,7(r3)` = **`0x88030007`** (keep `r3`=calib ptr, `r4`=PADStatus). Branch-back `0x8034E684`. |
| `0x8034E244` | analog→digital trigger conversion | `lbz r0,6(r4); cmplwi 0xAA; if ≥ set digital L bit 0x40`. So **analog `< 0xAA` sets NO digital bit** (→ can't airdodge). |
| `0x8008E498` | airborne trigger/airdodge check | reads the **digital** L/R timer `0x680` (not analog/Z) → light analog L never triggers it. |
| `0x8034DA00` | `PAD_Read` (GC SDK) | (from Slippi `Common.s`) |
| `0x803775C0` | **meta-flush hook** | vanilla `lbz r0,2(r25)` = **`0x88190002`** |
| `0x803775B8` | `HSD_PadRead` (consumer-side) | vanilla `lhz r0,0(r25)` = `0xA0190000`. **DESYNCS online — do not inject here.** |
| `0x80376A20` | `SkipNewInputFetchOnRollback` | Slippi-owned (from source) |
| `0x80376A24` | `ApplyInGameDelay` | Slippi-owned |
| `0x80376A28` | `TriggerSendInput` (EXI scrape + delay-buffer substitution) | Slippi-owned; the producer/consumer boundary |

## Key data addresses / RAM

| Address | What |
| --- | --- |
| `0x80453130` | **P1 GObj pointer.** Player Data = `*(GObj + 0x2C)`. Per-port stride **`0xE90`** (P2 = `0x80453FC0`). |
| Player Data `+0x10` | action state (low 16 bits) |
| Player Data `+0x25FF` | **`LCancelStatus`** (u8): 0=none, 1=success, 2=fail. **Direct per-landing L-cancel observable** — use this instead of landing-state duration. (from Slippi `Recording.s`) |
| Player Data `+0x650` | processed analog trigger (what the engine/L-cancel reads). The shipped v4 macro pulses a light analog L to this end (via the PADStatus `6(r4)` byte). |
| Player Data `+0x65C` | processed buttons (post-conversion) |
| Player Data `+0x195C` | **Hitlag counter** (FLOAT, counts down to 0 each frame; `!= 0` ⇒ in hitlag). During hitlag the Action State Frame Counter `0x894` FREEZES — why the action-frame-anchored digital cadence missed on hits, and why the analog macro pulses on the **global** frame counter instead. (`0x2219 & 0x04` is an alt hitlag flag.) |
| Player Data `+0x894` | **Action State Frame Counter** (FLOAT, resets to **1.0** on each new action state). The rollback-safe per-aerial anchor for the cadence (the BUG-1 fix — see handoff). NOTE it's a float; decode to int with integer ops (no FPU): `n = (0x800000 \| (bits & 0x7FFFFF)) >> (150 - exponent)`. (`+0x3E8` is the *Sub* Action State Frame Counter, also a float — not this one.) Confirmed in `Char_Data_Offsets.csv`. |
| Player Data `+0x680` | **L/R timer** ("frames since L/R pressed"). Tracks **L/R only — NOT Z**, so it's a misleading observable when injecting Z (stays 255 while a Z-cancel works). Use `LCancelStatus`/landing duration instead. |
| `0x80479D30` | **scene controller.** `getMinorMajor = ((w<<8)|(w>>24)) & 0xFFFF`. |
| `0x80479D60` | global frame counter (resets between scenes) |
| `0x804D7420` | power-on frame counter (never resets) |
| `r13 - 0x49E4` | **ODB (Online Data Buffer) pointer** (`OFST_R13_ODB_ADDR`, from source) |

### ODB offsets (from Slippi `Online/Online.s`)
| Offset | Field |
| --- | --- |
| `+0` | `ODB_LOCAL_PLAYER_INDEX` (u8) |
| `+1` | `ODB_ONLINE_PLAYER_INDEX` (u8) |
| `+2` | `ODB_INPUT_SOURCE_INDEX` (u8) — index TriggerSendInput uses to grab local input |
| `+3` | `ODB_FRAME` (u32) |
| ... | `ODB_DELAY_FRAMES` (the online input delay; relevant to the L-cancel timing bug) |

**Local player's GObj port at runtime: `port = *(*(r13 - 0x49E4) + 0)`**
(`ODB_LOCAL_PLAYER_INDEX`, offset **+0**). Use this for the `0x80453130 +
port*0xE90` GObj lookup so the macro gates on whichever player is local.
- Verified: `r13 = 0x804DB6A0`, ODB ptr e.g. `0x80BEDB40`. One session `+0 = 0`
  (P1), another `+0 = 1` (P2) — the local port DOES vary by host/guest, so a
  hardcoded port is wrong; use this lookup.
- **Do NOT use `+2` (`ODB_INPUT_SOURCE_INDEX`)** for the GObj port — it's the local
  input's *stack slot* (was 0 even when the local player was P2), not the player's
  controller port. (The injection at `0x8034E2AC` itself controls the local player;
  read+inject must be the same player, which `+0` gives.)

## L-cancel mechanics (IMPORTANT)

- **The game has NO input buffer.** A button held (down two frames in a row)
  registers as a press only ONCE. The L-cancel re-triggers only on a fresh
  **rising edge** (0→1). So to keep cancelling you must **PULSE** the trigger —
  release between presses. Holding = one edge = fails after the first frame.
- **Cadences that work:** every-other-frame (`1,0,1,0` — each press is an edge) or
  the offline canonical 1-press/6-release. "Timed every 7 frames" keeps the timer
  ≤6; pressing more often is fine *as long as there's a release between presses*.
- **L-cancel trigger = L, R, or Z.** The SHIPPED v4 macro pulses a **light ANALOG L**
  (value `0x80`, byte `6(r4)` at hook `0x8034E680`), every other frame. It still
  needs the rising edge (held analog does NOT cancel — tested), but a value `< 0xAA`
  sets no digital bit and presses no Z, so it **cannot airdodge or re-nair** — which
  is why analog L beats digital Z (the prior approach). Confirmed online: 15/15
  landing-nairs and 10/10 hit-aerials L-cancelled, 0 misfires.
- **Observable = `LCancelStatus` (Player Data `+0x25FF`: 1=success, 2=fail)** or
  landing-state (0x46-0x4A) duration (Fox NAIR 15f→~7f). NOT `0x680` (ignores Z and,
  for light analog, isn't the path either).
- **Hitlag:** pulse on the **global** frame counter parity (`0x80479D60 & 1`), NOT the
  Action State Frame Counter (`0x894`) — the latter freezes during hitlag, stalling
  the cadence and missing the cancel when an aerial connects. Global parity keeps
  ticking through hitlag.
- **Airdodge caveat (digital only):** a rising-edge digital L/R/Z in an airborne
  *non-attack* state (FallAerial when an aerial ends in air) airdodges/re-nairs. This
  is why the macro uses analog `< 0xAA` (immune) instead of digital Z.

## Scene IDs (`getMinorMajor` value)

| Value | Scene |
| --- | --- |
| `0x0208` | **ONLINE in-game** (target) |
| `0x0008` | online CSS (connected, match not started) |
| `0x0408` | online (results/other) |
| `0x0202` | offline VS in-game |

## 16-bit button bits (PADStatus `[r4+0]`, and what `oris r0,r0,BIT` sets)

| Button | Bit |
| --- | --- |
| A | `0x0100` |
| B | `0x0200` |
| X | `0x0400` |
| Y | `0x0800` |
| Start | `0x1000` |
| L (digital) | `0x0040` |
| R (digital) | `0x0020` |
| Z | `0x0010` |
| D-pad U/D/R/L | `0x0008 / 0x0004 / 0x0002 / 0x0001` |

(At `0x8034E2AC`, `oris r0,r0,BIT` puts BIT in r0's high 16; the displaced
`rlwinm` rotates it into `[r4+0]`. Sticks/triggers are written separately by
PAD_Read *after* the button word, so you can't set them via `oris` at
`0x8034E2AC` — buttons only.)

## SDK PAD report layout (the `[r4+...]` PADStatus PAD_Read builds = the `[r25+...]`
## struct HSD_PadRead consumes — same byte offsets)
| Off | Field | Notes |
| --- | --- | --- |
| `+0x0` | u16 **buttons** | 16-bit bits above; `oris r0,BIT` at `0x8034E2AC`, or OR into `0(r4)`/`0(r25)` |
| `+0x2` | s8 **stick X** | centered (PAD_Read subtracts `0x80`), then calibrated |
| `+0x3` | s8 **stick Y** | |
| `+0x4` | s8 **c-stick X** | |
| `+0x5` | s8 **c-stick Y** | |
| `+0x6` | u8 **analog L trigger** | **inject here for analog L** (`0x80`); `< 0xAA` ⇒ no digital bit |
| `+0x7` | u8 **analog R trigger** | |
| `+0x8` | u8 analog A | rarely used |
| `+0x9` | u8 analog B | rarely used |
| `+0xA` | status/err | |

**Analog-L injection (RESOLVED, shipped):** write `6(r4)=0x80` at hook `0x8034E680`
(PAD_Read finalizes `6(r4)` at `0x8034E67C`; builder returns `0x8034E69C`). We do
**not** want the digital bit — a value `< 0xAA` cancels via the analog trigger and
can't airdodge. (The earlier "may miss the digital path" worry was moot: digital is
what we're avoiding.) The byte→processed-trigger (`0x650`) map is **non-linear**
(calibration subtracts a per-port min): observed `0x80`→`0.91`, `0xA0`→`1.0`.

## Action states used

| State | Meaning |
| --- | --- |
| `0x000E` | Wait (standing) |
| `0x0018` | KneeBend (jumpsquat) — press X again here for full hop |
| `0x0019`–`0x0022` | JumpF/JumpB/Fall/etc. (airborne, no attack) |
| `0x001D` | Fall · `0x0020` FallAerial (aerial finished in air → can airdodge here) |
| `0x0041`–`0x0045` | aerials NAIR/FAIR/BAIR/UAIR/DAIR |
| `0x0046`–`0x004A` | LandingAir N/F/B/Hi/Lw (landing lag; measure its duration) |
| `0x00B2`–`0x00B6` | Guard / shield states |
| `0x00D4`–`0x00D6` | Catch / CatchDash / CatchTurn (**grab** — Z-misfire on the ground) |
| `0x00EC` | EscapeAir (**airdodge** — digital L/R/Z misfire in airborne non-attack states) |

## PAD_Read internals (producer side; mapped via `disasm_lcancel_analog.py`)
The local controller's PADStatus is built here each frame, then scraped by Slippi
(`TriggerSendInput` `0x80376A28`) and transmitted. Two relevant routines:
- **`0x8034E220`–`0x8034E2A4`** (`blr` at end): builds the analog bytes `6/7/8/9(r4)`
  from raw SI in `r5`, does the **analog→digital conversion** (`6(r4)≥0xAA`→set button
  bit `0x40` at `0x8034E250`; `7(r4)≥0xAA`→`0x20`), then centers sticks `2..5` by
  subtracting `0x80`.
- **`0x8034E2A8`–`0x8034E69C`** (`blr` at end): the **PADStatus builder our hooks live
  in**. `0x8034E2AC` sets `0(r4)` buttons from `r5` (the digital-injection slot); a
  controller-type/scene value at `*(r13-0x5A60)` (read at E2D0) branches how
  `4..9(r4)` are filled; then a **per-port calibration** pass (E4B4–E698) using the
  table at **`0x804A89B0 + port*0xC`** clamps/offsets each axis. **Analog L `6(r4)` is
  finalized at `0x8034E67C`**, R `7(r4)` at `0x8034E698` → hook `0x8034E680` to inject
  analog L after calibration. Online this routine runs for the **local** controller
  only (remote inputs arrive via EXI), so a hook here edits only the local/transmitted
  input — no per-port gate needed (gate on the local player's *state* via the ODB).
- The **airborne trigger / airdodge check** is at **`0x8008E498`** (the function
  `auto_lcancel/notes.md` mislabels as "the L-cancel check"). It only fires for action
  states `0x19`–`0x26` and `0xEC`, and reads the **digital** L/R timer `0x680` vs the
  window — NOT analog or Z timers. This is *why* digital L/R airdodges and a light
  analog L (which never touches `0x680`) does not. The actual **landing** L-cancel
  detection is a different routine (it sets `LCancelStatus` `0x25FF` on the landing
  frame) — not yet disassembled; use `0x25FF` as the observable.

## Hitlag + the L-cancel buffer (game mechanic)
- **Hitlag** (hit "freeze") runs while `Hitlag counter 0x195C != 0`. During it the
  **Action State Frame Counter `0x894` FREEZES**, but the global frame counter and the
  "frames since pressed" timers keep advancing — so a cadence anchored to `0x894`
  stalls and the cancel window expires (the digital-v3 hitlag miss).
- **L-cancel buffering during hitlag** (user-confirmed Melee rule): a trigger/Z input
  on ANY hitlag frame is re-applied through the rest of hitlag and stays active ~6
  frames after it ends. So a press landing anywhere in hitlag still cancels the
  post-hitlag landing. The shipped analog macro doesn't rely on this — its global-parity
  pulse simply keeps firing through hitlag — but it explains why even a single
  during-hitlag press works.

## C2 gecko packaging (footgun)
The C2 codehandler **overwrites the body's LAST word with its branch-back** — it
does not append. So the body must end with a throwaway word; never let your
displaced original (or any needed instruction) be last, or it's eaten (symptom:
inputs garbled — "A dead, stick → DPAD"). `gecko_c2_lines` (fixed) always appends
a `0x00000000` branch-slot (+ a nop to keep the count even). The dme path
(`finalize_payload`) appends a real branch as the last word, so it's immune.
Verify a built C2's cave with `verify_codehandler_displaced.py`.

## Meta-flush control plane (scratch in debug region)

| Address | Field |
| --- | --- |
| `0x803FA440` | `FLUSH_REQUEST` — write `0xDEADBEEF` to arm; gecko clears to 0 when done |
| `0x803FA444` | `FLUSH_START` (inclusive) |
| `0x803FA448` | `FLUSH_END` (exclusive) |
| `0x803FA600` | **recommended cave address** (clear of the control plane above) |

> **Do not place a cave overlapping `0x803FA440-0x803FA44C`** — `flush_range`
> writes there and will corrupt the cave → crash. `DEFAULT_CAVE` (`0x803FA3E8`)
> is too close for caves bigger than ~22 words.

## macOS virtual keycodes (for `melee_harness._send_key`)
F1 = 122, F2 = 120, F4 = **118**, Return/Enter = **36**, Left-Shift = 56.

## Online entry sequence
`F4` (load slot 4 = direct-connect menu w/ opponent pre-typed) → wait 3s →
`Enter` (search/connect) → wait ~15s → in-game (`0x0208`). Confirm by majority
vote on `0x80479D30`. If it stays `0x0008`, the other machine isn't in a match.

## Meta-flush gecko code (paste into Slippi Manager → Add Gecko Code)

Bake this into the slot-4 savestate (Guide §3) to enable runtime dme patching
online. Hooks `0x803775C0`; no-op at rest; only acts when armed via the control
plane. (Regenerate with `melee_harness.gecko_c2_lines(iw.META_FLUSH_HOOK,
iw.META_FLUSH_LOGIC, iw.META_FLUSH_ORIG, 'x')`.)

```
$Meta-Flush Iteration Primitive [dev]
C23775C0 0000000F
3D80803F 618CA440
800C0000 3D60DEAD
616BBEEF 7C005800
40820058 816C0004
814C0008 556B0034
394A001F 554A0034
7D695B78 7C095040
40800010 7C0048AC
39290020 4BFFFFF0
7C0004AC 7D695B78
7C095040 40800010
7C004FAC 39290020
4BFFFFF0 4C00012C
38000000 900C0000
88190002 00000000
```

## Reference: Altimor's "Swap X/Z — Netplay Safe" (the proof-of-concept)
The canonical netplay-safe input edit. Hooks `0x8034E2AC`; 3 `rlwimi` swap the X
(`0x0400`) and Z (`0x0010`) bits in the local input before the EXI scrape.
```
C234E2AC 00000002
5000843E 5000B56A
500056F6 00000000
```
