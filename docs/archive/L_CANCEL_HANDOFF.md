> **HISTORICAL (archived 2026-07-24).** Superseded by docs/macros/lcancel.md (current state) + WORKFLOW.md (dev loop). Kept for the v1–v3 debugging narrative.

# L-Cancel Macro — Handoff / Next-Agent Jumpstart

**Read this first, then `ONLINE_MACRO_GUIDE.md` + `ONLINE_REFERENCE.md`.** This file
is the *current task state, diagnosis, and next steps* so you don't re-derive what's
already known or chase the wrong thing. (Written 2026-05-22 by the agent that built
the producer-side online injection pipeline + shipped the v2 L-cancel gecko.)

## TL;DR of where things stand
- **The netplay-safe online injection pipeline is DONE and proven.** Producer-side,
  inside PAD_Read, edits applied before TriggerSendInput's EXI scrape. No desync.
- **SHIPPED v4 = pulsed ANALOG L (`online_auto_lcancel.gecko.txt`).** This is the
  current, validated macro and it fixes ALL THREE bugs at once. Pulses a LIGHT
  analog L trigger (value `0x80`, below the `0xAA` digital threshold) every other
  frame during the local player's aerials, injected producer-side at **`0x8034E680`**
  (where PAD_Read has finalized the analog L byte `6(r4)`). Results:
  - Offline (slot 2): 15f → ~7f, LCancelStatus success, 0 misfire (values 0x50-0xA9).
  - Online self-drive: **14.8f → 7.1f, LCancelStatus 15/15, no desync**.
  - Online hitlag (peer walked into nairs): **10/10 hit-aerials L-cancelled, 0
    misfire**.
- **WHY analog L beats the old digital Z** (see "HISTORY" below): a light analog L
  `< 0xAA` sets NO digital button bit and presses NO Z, so it physically **cannot
  airdodge or re-nair** (fixes trailing spill / BUG 2 by construction), and the
  pulse is keyed to the **global** frame counter parity which keeps ticking through
  **hitlag** (fixes the hitlag miss that the action-frame-anchored digital v3 had,
  because `0x894` freezes during hitlag). HELD analog does NOT L-cancel — you must
  pulse (rising edge), but pulsing is misfire-free here.
- **Open/next:** none blocking. Air-ending-aerial spill is solved by construction
  (couldn't be exercised by the self-drive, which always lands its nairs) — confirm
  in real free play if desired.
- **Coexistence with the wavedash (2026-06-05):** the analog-L hook `0x8034E680` is
  the **same instruction the up-bound wavedash's stick code hooks**, so the two
  cannot run as separate gecko codes — the second branch installed clobbers the
  first. To run BOTH, the L-cancel pulse is **folded into the wavedash stick cave**
  (`make_wavedash_gecko.py`, `INCLUDE_LCANCEL=True`): one `0x8034E680` cave does the
  wavedash stick angle in KneeBend (bytes 2/3) AND this analog-L pulse in aerials
  `0x41..0x45` (byte 6) — disjoint, no conflict. If you ship the wavedash, do **not**
  also enable the standalone `C234E680` here. Standalone `online_auto_lcancel.gecko.txt`
  stays for L-cancel-only setups (when the wavedash isn't installed). Validated live
  (one match): wavedashes + L-cancel 19/0 both firing.

## DO NOT repeat these (already settled — see the docs)
- The producer-side hook is `0x8034E2AC`; `0x803775B8` is consumer-side and
  desyncs. Don't re-investigate the pipeline.
- The game has **no input buffer** → you must PULSE (release between presses); a
  held button registers once. (Guide §9.)
- C2 codehandler **overwrites the body's last word** with its branch → never end a
  C2 body on a needed instruction; `gecko_c2_lines` is fixed to add a branch slot.
- Local player port varies (P1/P2) → resolve via ODB **`*(*(r13-0x49E4)+0)`**
  (`ODB_LOCAL_PLAYER_INDEX`, offset **+0**, NOT +2).
- dme: one process only (re-attach is unreliable), throttle + majority-vote reads.
- Big C2s won't install in the harness's offline codehandler (cave too small);
  validate logic via the dme path, packaging via `verify_codehandler_displaced.py`.

## BUG 1 + BUG 2 (HISTORICAL — the digital-Z diagnosis; analog L superseded this)
> The current macro is **analog L** (see "ANALOG L — SHIPPED" below), which fixes
> both bugs by construction. This section documents the original digital-Z root-cause
> analysis that led there; keep it for context, don't re-implement it.

The digital macro pressed Z when `(in aerial 0x41-0x45) AND (GLOBAL_frame % 7 == 0)`,
using the global frame counter `0x80479D60`. That global, unanchored cadence causes
**both** reported bugs:
- **"Slow uptake / aerials near the ground don't L-cancel":** the first Z press is
  on the next global `%7==0` frame — 0–6 frames into the aerial, random phase. A
  short/late nair can end before any `%7==0` frame falls inside it → no Z → no
  cancel. (Not the 2-frame delay.)
- **"Keeps pressing Z after the aerial ends":** the action-state read at the hook is
  the *previous* frame's (1-frame lag) + the 2-frame netplay delay, so trailing Z
  presses land after the aerial → can re-trigger an aerial/airdodge.

**THE FIX (IMPLEMENTED, v3 2026-05-22):** anchor the cadence to the **in-game
action-state frame counter** so Z fires on the first aerial frame and every 7
after. A scratch counter is NOT rollback-safe online (rollback rewinds game state
but not your scratch byte → desync), but the in-game counter is part of game state
→ rewound by rollback → deterministic on both clients.
- **The offset (FOUND + CONFIRMED): Player Data `+0x894` = "Action State Frame
  Counter"** (`Char_Data_Offsets.csv` line 394). It's a **FLOAT** that **resets to
  1.0** (not 0) on each new action state. (`+0x3E8` is the *Sub* Action State Frame
  Counter — not this one.)
- **Because it resets to 1 (and PadRead lags state by 1 frame, so the first
  detectable aerial frame reads action_frame = 1), the gate is
  `(action_frame - 1) % 7 == 0`, NOT `action_frame % 7 == 0`.** The `-1` makes the
  first press land on the first aerial frame (n ∈ {1, 8, 15, …}); `% 7 == 0` would
  press first on frame 7 and REINTRODUCE the slow-uptake bug. (This corrects the
  formula in the original handoff.)
- **No FPU needed.** The field is a float, but it always holds an integer-valued
  `n.0`, so the cave decodes it with pure integer ops:
  `n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exponent)`. (No FPU precedent in
  this repo's caves; the integer decode avoids a stack frame + f0 save/restore.)
  Validated in Python for n=1..2000 and capstone-verified.
- **Where it landed:** `make_online_lcancel_gecko.py` (→ regenerated
  `online_auto_lcancel.gecko.txt`) and `online_lcancel_selfdrive.py`. An offline
  validation rig is `offline_lcancel_anchor.py`.
- **For BUG 2 trailing-spill:** NOT separately guarded yet. Anchoring keeps trailing
  presses to ≤1 per aerial (only if the last read-aerial frame is ≡1 mod 7), and the
  offline cycle-7 macro showed **0 airdodges over 20 trials**, so it may not be
  needed. Confirm with the online self-drive run (it now reports grab/airdodge
  misfires + LCancelStatus). If spill shows up: stop the pulse in the last ~2-3
  frames of the aerial (needs per-aerial duration data) or move to analog L.

## ANALOG L — SHIPPED (v4, 2026-05-22). This is the current macro.
Analog L turned out to be the *simpler and complete* fix — it supersedes the digital
Z work above (which is kept as HISTORY). Settled findings (all tested, not assumed):
- **Injection point: hook `0x8034E680`, write the analog L byte `6(r4)`.** PAD_Read
  finalizes `6(r4)` at `0x8034E67C` (per-port calibration) and the report-builder
  `blr`s at `0x8034E69C`; `0x8034E680` is right after the analog byte is final and
  well upstream of TriggerSendInput's EXI scrape → producer-side / netplay-safe. The
  prior agent's `0x8034E4B4` guess and the "`≥0xAA→digital` is before our hook → may
  not cancel" worry were both moot: we do NOT want the digital bit (it airdodges) —
  a pure analog value cancels via the analog trigger timer, confirmed empirically.
- **Value `0x80`** (any `0x50`–`0xA9` works; stay `< 0xAA` so no digital bit). Below
  `0xAA`, PAD_Read's `0x8034E244` conversion never sets the digital L bit, and the
  airdodge trigger-check reads the digital L/R timer `0x680` → a light analog L
  **cannot airdodge**, and it presses no Z → **cannot re-nair**. Trailing spill
  solved by construction.
- **Must PULSE (every other frame), not hold** — held analog does NOT L-cancel
  (needs a rising edge). Pulse keyed to the **global** frame counter parity
  (`frame & 1`), which keeps ticking through **hitlag** (the action-state counter
  `0x894` freezes during hitlag — that was the digital v3 hitlag miss). Pulsing is
  safe here because analog L can't misfire.
- **Where it landed:** `make_online_analog_lcancel_gecko.py` →
  `online_auto_lcancel.gecko.txt` (v4). Tests: `offline_analog_lcancel.py`,
  `online_analog_selfdrive.py`, `online_analog_hitlag.py`. The L-cancel mechanic was
  mapped with `disasm_lcancel_analog.py`.

## HISTORY: the digital-Z line (v1–v3, superseded by analog L above)
The digital path worked but had two bugs analog L avoids; kept for context.
- v1/v2: pulsed digital **Z** at `0x8034E2AC`, global-frame `% 7` cadence. v2 fixed
  the C2-codehandler "last word eaten" bug (guide §6).
- v3 fixed BUG 1 (slow uptake) by anchoring the cadence to the **Action State Frame
  Counter** (Player Data `+0x894`, a FLOAT resetting to **1.0**; decode to int with
  `n = (0x800000 | (bits & 0x7FFFFF)) >> (150-exp)`, gate `(n-1)%7==0` — NOT `n%7`).
  Confirmed online 15.1f→6.7f. Files: `make_online_lcancel_gecko.py`,
  `online_lcancel_selfdrive.py`, `offline_lcancel_anchor.py`.
- But v3 had the **hitlag miss**: `0x894` freezes during hitlag, so the cadence
  stalled and the Z timer expired (`online_hitlag_diag.py` showed it + an override).
  And digital Z/L could airdodge/re-nair (trailing spill). **Analog L fixes both**,
  so the digital macro is retired.

## Better observable than landing-duration (NOW WIRED IN)
Use **`LCancelStatus` = PlayerData `+0x25FF`** (0=none, 1=success, 2=fail) — a direct
per-landing success/fail flag. Much cleaner than measuring landing-state duration.
`online_lcancel_selfdrive.py` and `offline_lcancel_anchor.py` now sample it on each
landing-aerial entry and report success/fail counts in the verdict.

## Workflow (condensed — full version in ONLINE_MACRO_GUIDE.md §3-§4)
- Online testing needs the user: their other machine in an active match, and the
  slot-4 savestate baked with **meta-flush** (for dme iteration) — ask them.
- Dev loop, ONE process: `Harness()` → `launch/hook_dme/_wait_for_cpu_alive` →
  F4 (key 118) +3s → Enter (key 36) +15s → confirm scene `0x0208` (majority vote on
  `0x80479D30`) → `iw.write_instrs`/`patch_branch` your cave at `0x803FA600` hooking
  `0x8034E2AC` → observe (throttled reads). Leave Dolphin running.
- **Develop offline first where possible** (the cadence/anchor logic and the
  L-cancel mechanic both reproduce offline via `auto_lcancel/`-style self-drive at
  the `0x803775B8` hook); only the 2-frame delay and netplay-safety are online-only.
- Always keystone-build + capstone-verify before flushing.
- The self-drive test (`online_lcancel_selfdrive.py`) is your rig: it drives
  jump→nair→land and A/B-tests Z-on vs off. Add `LCancelStatus` sampling and an
  `action_frame`-anchored cadence to it. Use a **full hop** (short hops auto-cancel
  ~7f and hide the effect).

## Files
**Current (analog L, v4 — the shipped macro):**
- `online_auto_lcancel.gecko.txt` — **shipped gecko (v4 = pulsed analog L @ 0x8034E680).**
- `make_online_analog_lcancel_gecko.py` — regenerates the shipped gecko.
- `online_analog_selfdrive.py` — online self-drive A/B test (analog off vs on).
- `online_analog_hitlag.py` — online hitlag confirmation (peer walks into the nairs).
- `offline_analog_lcancel.py` — OFFLINE analog-L value sweep (slot 2; proved pulsed
  analog L L-cancels and held does not, and the value range).
- `disasm_lcancel_analog.py` — read-only disasm that found the `6(r4)` finalize point
  (`0x8034E67C`/`blr 0x8034E69C`) and the `0xAA`/`0x680` digital-airdodge logic.

**Historical (digital Z, v1–v3 — superseded):**
- `make_online_lcancel_gecko.py`, `online_lcancel_selfdrive.py`,
  `offline_lcancel_anchor.py`, `online_hitlag_diag.py` — digital-Z generator, rigs,
  and the hitlag diagnosis that motivated the move to analog L.
- `auto_lcancel/` — the original offline digital macro.
- `docs/ONLINE_MACRO_GUIDE.md`, `docs/ONLINE_REFERENCE.md` — the deep docs.
