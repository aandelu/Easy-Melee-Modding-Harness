# STATUS — the Easy Melee state board

**Last updated: 2026-07-25** (verify suite 7/7 PASS — harness fully operational).

This is the single source of truth for *what's shipped, what's pending, and what to work on next*.
When anything ships or changes state, **edit this file** — nothing else needs to track status.

## Deliverables

| Macro | Offline | Online | Shipped artifact | Generator | Verify/test | Open items |
| --- | --- | --- | --- | --- | --- | --- |
| **JC-shine** (Fox auto jump-cancel shine when Marth grabs) | ✅ SHIPPED | ❌ **NOT netplay-safe as-is** (found 2026-07-24: consumer-side `0x803775B8` hook, no scene gate — would desync; the old "netplay-safe" label was wrong) | `candidate_d_standalone_v2.py` (paste into Slippi user INI) | (hand-built, see file) | `verify_d_standalone_v2.py`, live play `play_d2.py` | port to producer-side hooks (`docs/macros/jc_shine.md`) |
| **Auto L-cancel** | ✅ SHIPPED (`auto_lcancel/`) | ✅ SHIPPED (analog-L pulse) | `online_auto_lcancel.gecko.txt`; **also folded into the wavedash gecko** | `make_online_analog_lcancel_gecko.py` | `auto_lcancel/` suite | none |
| **Cactuar dash** (auto dash-back reversal) | ✅ validated | ⚠️ validated via runtime injection (65/65, no desync) — **shipped C2 gecko silently fails** in real Slippi (user-code append-space limit) | `online_cactuar_dash.raw.gecko.txt` (the identified fix, never user-tested) | `make_cactuar_dash_gecko.py` | (dev scripts deleted; regenerate from generator) | ship+test the raw/merged form; delay-2 validation; ~1/49 TurnRun slip |
| **Up-bound wavedash** | ✅ SHIPPED | ✅ SHIPPED, frame-perfect at delay 1 (includes the L-cancel in its cave) | `online_wavedash.gecko.txt` | `make_wavedash_gecko.py` | `play_wavedash_offline.py`, `play_wavedash_monitor.py`, `attach_observe_wavedash.py` | user delay-2 test (expect Fox ~1f late — the floor); real-stick up-check; does *holding* up auto-repeat? |
| **ASDI floorhug + tech + SDI + TDI** (auto ASDI-down during hitlag; auto-tech; SDI down-drag; trajectory DI) | ✅ ASDI + **tech layer VALIDATED offline** (10/10 tumble hits teched, gate v1.5); ✅ **SDI pattern VALIDATED offline** (winner = X-flip V raw (±24,−76) at **−5.7 units/frame**; → REFERENCE §2.8); ✅ **TDI mechanic VALIDATED offline** (2026-07-25: perpendicular-down, **+15.0…16.7° rotation** vs 0.0° control, 9 ON / 13 OFF hits across 3 runs; DI is read on the `hitlag == 2` frame → REFERENCE §2.9) — all four layers run together in one cave; not yet a shipped artifact | ✅ **ASDI + SDI + tech press VALIDATED live** (2026-07-25, delay 1, 28 hits, `asdi_online_full.py`: **13 of 18 tech-situation hits teched** with nobody on the local pad, so every press is the macro's; `asdi=342 sdi=342 tech=118`; no desync seen). Two producer hooks: sticks `0x8034E680`, digital R `0x8034E2AC`. TDI is ported but **OFF by default** — at delay 1 it would take SDI's only frame | — | — | `asdi_online_full.py` (online full stack), `asdi_tech_offline.py`, `asdi_sdi_offline.py`, `asdi_tdi_offline.py`, `asdi_online_test.py`, `asdi_probe_offline.py`, `observe_hitlag.py`, `bisect_asdi.py` | **TDI outcome A/B** — the rotation is proven, the *benefit* is not: it did NOT convert vertical launches into techs (90°→75° is still upward), so measure tech-rate on mid-angle launches. Then the **offstage/stage-relative guard** (covers TDI too: the downward perpendicular has two ends and `sign(kb_x)` is stage-blind); the 5 online tech misses were all late-landing DownBound arcs → the parked "re-press R as a tumble landing approaches" idea; **fold everything into the wavedash cave** (`0x8034E680` is taken) (`docs/macros/asdi_floorhug.md`) |
| **Windows netplay peer** (autonomous 2nd machine) | — | ✅ SHIPPED | `peer.py` + `peer/` | — | `verify_peer.py` | desync detection is still eyeball-only. (Flaky retry path FIXED 2026-07-25: `enter_online` now relaunches the peer's Slippi per retry — F1 into a mid-search Dolphin crash-looped it; fix validated live, miss→restart→connect on attempt 2.) |

### ⚠️ Coexistence rule (deploy-time, critical)

**Never enable the standalone L-cancel gecko and the wavedash gecko together** — both hook `0x8034E680`.
The wavedash gecko already contains the auto-L-cancel. Ship one or the other, not both.

**The ASDI floorhug is a third claimant on `0x8034E680`** (c-stick byte `5(r4)` + stick
bytes `2/3(r4)`) **and a second claimant on `0x8034E2AC`** (digital R). Both must be
**folded into the wavedash caves** like the L-cancel was — never shipped as separate
geckos. The tech press uses **R**, not L, precisely to avoid the L contention: a digital
L would fight the wavedash airdodge, and the analog-L path needs `≥ 0xAA` while the
auto-L-cancel deliberately writes `0x80` to stay below it.

## Environment: RESTORED 2026-07-24

The 2026-07-02 Slippi update had broken the environment; all fixed 2026-07-24:
hardlink recreated (`ln "Slippi Dolphin" Dolphin` — redo after every Slippi update),
Mac rebooted (cleared the unkillable `Dolphin --version` corpse), savestates 2 & 4
re-created under the current build.

**Verify suite 2026-07-25: 7/7 PASS** — savestate, inject_gecko, meta_flush, bp, scenario,
peer (online match reached with zero physical Windows interaction), d_standalone_v2 (3/3 JC-shines).
The harness is fully operational, offline and online.

## Next-work queue
1. Wavedash pending validations (user + peer): delay-2, real-stick up-check, hold-repeat.
2. Cactuar dash: ship the raw/merged gecko through the *real* user install path and validate.
3. JC-shine online port: rebuild on the producer-side hooks (the shipped version is offline-only in truth — see the table).

## Backlog (ideas with known value, not started)

- **Desync oracle**: use Slippi's per-frame desync checksums (`vendor/slippi-ssbm-asm-master`, StartEngineLoop.asm) as an automated pass/fail signal instead of eyeballing — pairs with the peer.
- **Install-path sentinel**: a standing test that installs a gecko exactly as a user would (Slippi INI, append-space limits) and verifies a sentinel fires — would have caught the cactuar ship failure and the v1 L-cancel controller bug.
- **Macro engine vision** (2026-05-20): machine-readable memory map + a macro dispatcher/stdlib meta-gecko so agents compose macros from primitives instead of hand-writing PPC.
- D.1 formal closure: the 2026-05-15 mystery is *probably* the C2-codehandler-eats-last-word bug (fixed in `a5469d2`); never proven.

## History

Narrative history, superseded plans, and session logs live in `docs/archive/` (each file carries a HISTORICAL banner). The full pre-cleanup tree is recoverable from git history (cleanup commits, 2026-07-24) and `~/Desktop/melee-archive-2026-07-24.tar.gz`.
