# STATUS — the Easy Melee state board

**Last updated: 2026-07-24** (repo cleanup + rename to Easy-Melee-Modding-Harness).

This is the single source of truth for *what's shipped, what's pending, and what to work on next*.
When anything ships or changes state, **edit this file** — nothing else needs to track status.

## Deliverables

| Macro | Offline | Online | Shipped artifact | Generator | Verify/test | Open items |
| --- | --- | --- | --- | --- | --- | --- |
| **JC-shine** (Fox auto jump-cancel shine when Marth grabs) | ✅ SHIPPED | ❌ **NOT netplay-safe as-is** (found 2026-07-24: consumer-side `0x803775B8` hook, no scene gate — would desync; the old "netplay-safe" label was wrong) | `candidate_d_standalone_v2.py` (paste into Slippi user INI) | (hand-built, see file) | `verify_d_standalone_v2.py`, live play `play_d2.py` | port to producer-side hooks (`docs/macros/jc_shine.md`) |
| **Auto L-cancel** | ✅ SHIPPED (`auto_lcancel/`) | ✅ SHIPPED (analog-L pulse) | `online_auto_lcancel.gecko.txt`; **also folded into the wavedash gecko** | `make_online_analog_lcancel_gecko.py` | `auto_lcancel/` suite | none |
| **Cactuar dash** (auto dash-back reversal) | ✅ validated | ⚠️ validated via runtime injection (65/65, no desync) — **shipped C2 gecko silently fails** in real Slippi (user-code append-space limit) | `online_cactuar_dash.raw.gecko.txt` (the identified fix, never user-tested) | `make_cactuar_dash_gecko.py` | (dev scripts deleted; regenerate from generator) | ship+test the raw/merged form; delay-2 validation; ~1/49 TurnRun slip |
| **Up-bound wavedash** | ✅ SHIPPED | ✅ SHIPPED, frame-perfect at delay 1 (includes the L-cancel in its cave) | `online_wavedash.gecko.txt` | `make_wavedash_gecko.py` | `play_wavedash_offline.py`, `play_wavedash_monitor.py`, `attach_observe_wavedash.py` | user delay-2 test (expect Fox ~1f late — the floor); real-stick up-check; does *holding* up auto-repeat? |
| **Windows netplay peer** (autonomous 2nd machine) | — | ✅ SHIPPED | `peer.py` + `peer/` | — | `verify_peer.py` | desync detection is still eyeball-only |

### ⚠️ Coexistence rule (deploy-time, critical)

**Never enable the standalone L-cancel gecko and the wavedash gecko together** — both hook `0x8034E680`.
The wavedash gecko already contains the auto-L-cancel. Ship one or the other, not both.

## Environment restore needed (found 2026-07-24, after ~7 weeks idle)

The Slippi Launcher updated **2026-07-02**, which broke the harness environment:

1. ~~`Dolphin` hardlink wiped~~ — **recreated 2026-07-24** (`ln "Slippi Dolphin" Dolphin` in the app's MacOS/ dir; redo after every Slippi update).
2. **All savestates are stale** (`StateSaves/GALE01.s0*` are May–Jun; savestates are version-locked to the build). Re-create under the current build: slot 2 = offline Marth-vs-Fox scenario; slot 4 = online-entry state with meta-flush baked (see WORKFLOW.md "Ship" chapter).
3. **Reboot the Mac once**: an unkillable half-exited `Dolphin --version` process (kernel `UE` state) is wedged; dme attaches to that corpse instead of a live Dolphin until it's cleared.
4. Then run the health suite (canonical list in `WORKFLOW.md`): `verify_savestate.py` → `verify_inject_gecko.py` → `verify_meta_flush.py` → `verify_bp.py` → `verify_scenario.py` → `verify_peer.py`.

## Next-work queue

1. Environment restore (above), then re-run the verify suite.
2. Wavedash pending validations (user + peer): delay-2, real-stick up-check, hold-repeat.
3. Cactuar dash: ship the raw/merged gecko through the *real* user install path and validate.
4. JC-shine online port: rebuild on the producer-side hooks (the shipped version is offline-only in truth — see the table).

## Backlog (ideas with known value, not started)

- **Desync oracle**: use Slippi's per-frame desync checksums (`vendor/slippi-ssbm-asm-master`, StartEngineLoop.asm) as an automated pass/fail signal instead of eyeballing — pairs with the peer.
- **Install-path sentinel**: a standing test that installs a gecko exactly as a user would (Slippi INI, append-space limits) and verifies a sentinel fires — would have caught the cactuar ship failure and the v1 L-cancel controller bug.
- **Macro engine vision** (2026-05-20): machine-readable memory map + a macro dispatcher/stdlib meta-gecko so agents compose macros from primitives instead of hand-writing PPC.
- D.1 formal closure: the 2026-05-15 mystery is *probably* the C2-codehandler-eats-last-word bug (fixed in `a5469d2`); never proven.

## History

Narrative history, superseded plans, and session logs live in `docs/archive/` (each file carries a HISTORICAL banner). The full pre-cleanup tree is recoverable from git history (cleanup commits, 2026-07-24) and `~/Desktop/melee-archive-2026-07-24.tar.gz`.
