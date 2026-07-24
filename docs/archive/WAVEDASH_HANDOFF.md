> **HISTORICAL (archived 2026-07-24).** Superseded by docs/macros/wavedash.md. The "online port (your job)" section was completed 2026-06-04/05 — the port SHIPPED.

# Wavedash Macro — Handoff / Next-Agent Jumpstart

**Read this, then `ONLINE_MACRO_GUIDE.md` + `ONLINE_REFERENCE.md` + `CACTUAR_DASH_HANDOFF.md`.**
Also read the memory files `wavedash_observe_setup.md` and `wavedash_mechanic.md`.
(Written 2026-06-04, the session that built + validated the OFFLINE macro. The
ONLINE port is the remaining work.)

## What it is
A wavedash bound to **up on the left stick**. While the local player holds up
(stickY at/near max), the macro: jumps (their tap-jump, or an injected Y when
buffering/repeating), then on the **last jumpsquat frame** overrides the stick to
a shallow down-diagonal + presses L → an **air-dodge into the ground = wavedash**
(`LandingFallSpecial 0x2B`). Direction comes from the horizontal you hold; up
alone = straight-down wavedash. Holding up **repeats** it. Works on **any
character** (jumpsquat length read per-character at runtime).

## Status
- **OFFLINE: DONE + user-confirmed "perfect."** Playable via `play_wavedash_offline.py`
  (installs on slot 2, hand controls to the user; WASD-friendly). Validated on Fox
  (jumpsquat 3) and Marth (jumpsquat 4): correct direction (incl. switching mid-jumpsquat),
  repeat, and quick-tap (release-up) all work.
- **ONLINE: SHIPPED (2026-06-04).** Gecko `online_wavedash.gecko.txt` (regen
  `make_wavedash_gecko.py`). Validated online vs live peer (delay=1, Fox): NO desync,
  frame-perfect (0 air frames). **Read [`docs/WAVEDASH_ONLINE_RESULTS.md`](WAVEDASH_ONLINE_RESULTS.md)**
  for the full online build/findings. Key deltas from the plan below:
    * **TWO producer hooks**, not one: digital L/Y at `0x8034E2AC`, stick at `0x8034E680`
      (digital can't go via `0x8034E680` — downstream of analog→digital). Each re-resolves
      the local player.
    * **Frame-perfect airdodge = `asfc == jumpsquat − 1 − delay`** (clamped ≥1). Found by
      an INSTRUMENTED in-cave sweep (perfect-vs-floaty counters + a per-frame state ring) —
      the lossy Python observer was WRONG. The user's frame-eye caught it.
    * **Delay floor:** frame-perfect needs `jumpsquat ≥ delay+2`. Fox is frame-perfect at
      delay 1, ~1 frame late at delay 2 (can't inject the airdodge before jumpsquat starts);
      Marth+ stay perfect at delay 2.
    * The up-latch ports fine online (rollback-safe in practice); the predictive buffer-jump
      was NOT needed — gating Y on grounded-actionable (released off-ground) gives natural
      repeat. Self-drive = sim-up in grounded states only (every-frame up double-jumps).
    * **PENDING:** user delay-2 real-input test; the `0x8034E2AC` real-stick up-check is the
      one piece only their controller can confirm.
  The original plan below is kept for historical context.

## The validated mechanic (don't re-derive — measured in `offline_wavedash_probe.py`)
- **Stick bytes** (signed, at the pad hook; full ≈ ±0x70, scale = byte/0x70, radial-clamped):
  airdodge angle **right = (X=0x6A, Y=0xE0)** → processed (+0.95, −0.287) [the user's exact angle];
  **left = (0x96, 0xE0)**; **straight-down = (0x00, 0x90)** → (0, −1.0). Full up = (0, 0x70).
- **Airdodge timing = the LAST jumpsquat frame**: `state==KneeBend(0x18) AND asfc==(jumpsquat−1)`.
  Fox jumpsquat 3 → asfc 2; Marth 4 → asfc 3. Inject the L+stick there → `LandingFallSpecial`
  directly (frame-perfect, no air frames). Earlier = full jump (no airdodge); later = floats up first.
- **Jumpsquat is per-character**: read **Player Data `0x148`** ("Jump startup time"). It's a
  **float** (Fox 3.0=0x40400000, Marth 4.0=0x40800000) — the cave handles int-or-float robustly
  (`if raw < 0x100: int else: float-decode`).
- **Airdodge needs DIGITAL L** (bit `0x40`): the airborne trigger check reads the digital L/R timer
  `0x680`; a light analog L (`<0xAA`) does NOT airdodge (that's exactly why the L-cancel used it).
- Wavedash landing lag (`LandingFallSpecial`) ≈ 10f. Hop height is irrelevant (the airdodge on
  frame-1-airborne overrides the jump arc), so short/full hop wavedash identically.

## The cave logic (THE reference — see `play_wavedash_offline.py` CAVE_ASM)
Per port, every frame (offline it hooks the CONSUMER pad-read `0x803775B8`; pad struct at r25:
buttons `0(r25)`, stickX `2(r25)`, stickY `3(r25)`):
```
resolve this port's Player Data (GObj 0x80453130 + r24*0xE90 -> +0x2C; MEM1-check each ptr)
state = *(PD+0x10) & 0xFFFF
WD_PEND[port], WD_DIR[port]  <- per-port scratch (see "the two fixes")

if state == KneeBend(0x18):
    if WD_PEND[port] == 0: return            # a plain (non-up) jump -> NO airdodge
    LJF = decode(*(PD+0x148)) - 1            # jumpsquat-1, per character
    if decode(*(PD+0x894)) == LJF:           # asfc == last jumpsquat frame
        buttons |= 0x40 (L)                  # airdodge
        dir = stickX read LIVE here          # <-- live, so you can switch dir during jumpsquat
        stick = right(0x6A,0xE0)/left(0x96,0xE0)/down(0,0x90) by sign(dir), deadzone 0x30
        WD_PEND[port] = 0                     # consume
    return
# not KneeBend:
if WD_PEND[port] and state not in 0x0E..0x17: WD_PEND[port] = 0   # failsafe clear (left ground w/o airdodging)
if (stickY >= UP_THRESH) and (0x0E <= state <= 0x17):   # up held + grounded-actionable
    buttons |= 0x800 (Y)                      # jump (drives 1st jump, repeat, AND landing-lag buffer)
    WD_PEND[port] = 1                         # commit the wavedash
```
- `UP_THRESH = 0x40` (catches keyboard up+direction diagonals, which read ~0x5A).
- **Grounded-actionable range `0x0E..0x17`** (Wait..RunBrake), NOT just Wait — a held horizontal
  stick lands you in WalkFast/Dash after a wavedash, and this range catches it so repeat + buffer work.
- asfc/jumpsquat float→int decode without FPU: `exp=(bits>>23)&0xFF; n=((bits&0x7FFFFF)|0x800000) >> (150-exp)`
  (copy from `make_cactuar_dash_gecko.py`).

## The two fixes that made it feel right (KEEP THESE — they cost a debugging round each)
1. **Up-latch (`WD_PEND`)**: a quick up-*tap* releases up before the airdodge frame (worse on Marth,
   whose frame is later). So **latch "wavedash pending" when the jump is injected** and fire the airdodge
   on the jumpsquat frame **regardless of whether up is still held**. Without this, Marth full-jumps on quick taps.
2. **Live direction (do NOT latch direction)**: read stickX **live on the airdodge frame**, not at jump time.
   The user wants to tap up while moving right, then switch to left before jumpsquat ends and wavedash left —
   live-read gives them the *whole jumpsquat* to switch (the max for a tight wavedash). (We tried latching
   direction; it broke direction-switching. Up = latched, direction = live.)

## The online port (your job)
Same logic, but **producer-side + delay-comp**. Reuse `make_cactuar_dash_gecko.py` wholesale (it has the
ODB resolution, the float-decode, the runtime delay read, and the C2/RAW packaging).
- **Hooks**: stick override at **`0x8034E680`** (write `2(r4)`=X, `3(r4)`=Y; displaced `lbz r0,7(r3)`=`0x88030007`,
  preserve r3/r4/r13). For the **digital L (airdodge) + Y (jump)** bits, FIRST try setting `0(r4)` buttons at
  `0x8034E680` too (it may still be the final buttons word there) — if that doesn't register, use a SECOND hook
  at **`0x8034E2AC`** (`oris r0,r0,BIT`, displaced `rlwinm`=`0x540084BE`). Two caves, each re-resolving the player.
  (`0x8034E2AC` is upstream of stick rebuild, so stick MUST be at `0x8034E680`; digital bits set at `0x8034E2AC`
  are the proven path.)
- **Local player**: online the gecko runs on the USER's machine where THEY are local → resolve via
  **`ODB_LOCAL_PLAYER_INDEX` (ODB+0x00)**, `port = *(*(r13-0x49E4)+0)`. (Contrast: the harness OBSERVATION
  machine sees the user as the PEER → `+0x01`. See `wavedash_observe_setup.md`. Don't confuse them.)
- **Delay-comp**: read `ODB_DELAY_FRAMES` (ODB **+0x21**, u8 — confirmed correct this session, reads 1 on the
  dev machine). Fire the airdodge at **`asfc == jumpsquat − 1 − delay`** so it lands on the right frame after the
  producer-side delay. (Same trick cactuar used: release at squat frame `6−delay`.)
- **The hard part — the delay-comp'd buffer/repeat jump**: the Y-inject must land its *rising edge* on the
  **first actionable frame**, which means injecting `delay` frames AHEAD (predict), and Melee has NO input
  buffer (held Y = one edge). For grounded landing-lag (LandingFallSpecial/Landing/LandingAir) you know the
  duration → inject when `asfc >= duration − delay`. For hitstun there's a frames-left counter (`0x2340`).
  Expect 1–2 online tuning passes here (the user warned this is the genuinely hard part).
- **The `WD_PEND` latch online**: it's per-port scratch. The cactuar handoff warns "data flags in 0x803FAxxx
  aren't reliable online" (rollback). BUT the producer hook fires once per REAL frame (rollback skips it), and
  the LOCAL player's state progression isn't rewound by opponent-input rollback — so the latch is *probably* OK.
  TEST it. If it misbehaves, options: toggle a code instruction instead of data, or derive intent from game state.
  (You can also ship a v1 WITHOUT the latch — requires up held through the airdodge frame — then add it.)
- **Verify the byte→stick map at `0x8034E680`** (post-calibration, like the consumer hook, so 0x6A/0xE0/0x90
  *should* transfer) before trusting it. Verify the airdodge fires from digital L there.

## Testing model (mirrors cactuar/L-cancel)
1. **Harness machine** (this one): self-drive the LOCAL player (synthetic "hold up") to validate logic +
   delay-comp + **no desync** online. The user's 2nd account is the passive peer; the user watches their screen
   for desync. Enter online with F4→Enter (needs **meta-flush baked into slot 4** for the dme dev loop — confirm
   with the user; it was baked for cactuar/L-cancel). The big cave (>22 words) installs via the dme/meta-flush
   path, NOT the harness's small boot-codehandler.
2. **The user's machine**: once desync-clean, they install the gecko in Slippi Manager (like cactuar/L-cancel)
   and test with REAL input; tune delay-comp feel from there. (The shipped gecko needs no savestate bake for
   normal play — only the dev harness did.)
- **dme can't re-attach from a fresh process** — launch + hook + observe/install in ONE process (cost a
  detour this session). To monitor a play session, build monitoring INTO the launching script
  (`play_wavedash_monitor.py` does this).

## Files
- `offline_wavedash_probe.py` — discovery: stick-byte calibration + airdodge-frame sweep ([PASS]-style prints).
- `offline_wavedash_macro.py` — first real state-gated cave, sim-driven; proved direction (posX deltas) + repeat (6 cycles/80f).
- **`play_wavedash_offline.py`** — THE current offline macro (playable, real input, character-agnostic, up-latch + live direction). **This cave is what you port online.**
- `play_wavedash_monitor.py` — play + monitor in ONE process (reuses the play cave; logs both ports on change). For diagnosing.
- `online_wavedash_observe.py` — read-only online observation (user as PEER, ODB+0x01); how the mechanic was first captured live.
- Memory: `wavedash_observe_setup.md` (setup + local-vs-peer gotcha), `wavedash_mechanic.md` (all the measured constants).

## Gotchas hit this session (see also CLAUDE.md, ONLINE guides)
- The online Python observer is LOSSY for exact frame timing (can't keep up at 60fps) — use the OFFLINE harness
  (single-step) or an instrumented cave for frame-perfect numbers.
- The IASA flag `0x2218` is NOT a general "actionable" bit (it's 0 even in Wait) — don't use it for actionability;
  use state + known duration, or the hitstun counter `0x2340`.
- Tap-jump does NOT auto-repeat (holding up = one jump); the repeat feature is the macro injecting Y each cycle.
- `kill_stale` (`pkill -9 -x Dolphin`) only kills the hardlink-named "Dolphin"; a user's own "Slippi Dolphin"
  survives and can confuse which window has the macro — tell the user to play the freshly-launched window.

## Coexistence with the online L-cancel (2026-06-05) — they SHARE hook 0x8034E680

The shipped online auto-L-cancel (`online_auto_lcancel.gecko.txt`, `C234E680`) hooks the **same** producer-side
instruction `0x8034E680` as the wavedash **stick** code (both displace `lbz r0,7(r3)` = `0x88030007`). Only one
branch can live at an address, so enabling both as separate codes kills the L-cancel (the wavedash wins).
**Fix shipped:** the L-cancel's analog-L pulse is now **folded into the wavedash stick cave** (`ASM_A` in
`make_wavedash_gecko.py`, gated by `INCLUDE_LCANCEL=True`). The single `0x8034E680` cave now does both — the
wavedash stick angle in KneeBend (writes bytes 2/3) **and** the L-cancel pulse in aerials `0x41..0x45` (writes
byte 6), disjoint states + disjoint bytes, so zero conflict. Stick cave grew 92→104 words (`063FA600 000001A0`,
ends `0x803FA7A0` < `CAVE_B` `0x803FA800`). **Do NOT also enable the standalone `C234E680` L-cancel** — it would
re-collide. Validated live (one match): 12 wavedashes + L-cancel success 19 / fail 0, both firing.
`observe_live_wavedash.py` samples `LCancelStatus` (Player Data `+0x25FF`) to confirm.
