---
name: run-melee-harness
description: Launch, verify, and drive the SSBM (Melee) gecko-macro harness. Use when asked to run/launch/start/smoke-test/verify the harness, bring up or launch Slippi Dolphin, drive/play a macro offline, check the dme bring-up, or develop a Melee macro. macOS-ONLY; drives the running game through dolphin-memory-engine (dme) memory, not the screen.
---

This is a **macOS-only** closed-loop harness that launches **Slippi Dolphin** running **Super Smash Bros. Melee (NTSC 1.02)** and drives/observes it through **`dolphin-memory-engine` (dme)** — reading/writing the emulated GameCube's MEM1 (action states, entity pointers, the frame counter) and installing gecko code caves at runtime. There is **no screen-scrape / Playwright path**: dme memory *is* the handle an agent uses to see and poke the running game. The agent entry point is the driver **`.claude/skills/run-melee-harness/smoke.sh`** (prereqs + canonical bring-up). All paths below are relative to the repo root.

> **Why no Linux / no clean-container path:** dme uses macOS `task_for_pid` (needs SIP disabled); inputs are synthesized via macOS CGEvents (needs Accessibility); it needs a licensed Melee ISO and a Slippi Dolphin `.app` bundle, with machine-specific paths hard-coded in `melee_harness.py` (`DOLPHIN_HARDLINK`, `ISO_PATH`, `USER_DIR`). It cannot run on a fresh non-macOS machine. Verified on the configured macOS dev machine this session.

## Prerequisites (machine state — verify, don't assume)
All four must hold. Check them (the driver does this too):
```bash
csrutil status                                    # must say: disabled
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -c "import dolphin_memory_engine, keystone, capstone; print('deps OK')"
# Dolphin hardlink (dme scans for a process literally named "Dolphin"; Slippi's binary is "Slippi Dolphin"):
stat -f '%i %N' "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Slippi Dolphin" \
                "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Dolphin"   # inodes must match
```
Also required (not script-checkable): **Accessibility** granted to the terminal/Python (for synthetic F2/F4/Enter), the **Melee ISO** at `melee_harness.ISO_PATH`, and savestate **slot 2** present (`StateSaves/GALE01.s02` = Marth P1 / Fox P2 in-game on a flat stage). First-time machine setup is in **`HARNESS.md` §9** and **`docs/ONLINE_MACRO_GUIDE.md` §2**.

## Setup (one-time; already done on this machine)
- **Python deps:** `pip install dolphin-memory-engine keystone-engine capstone` (verified importable above).
- **`Dolphin` hardlink** (recreate if `stat` shows a mismatch — **a Slippi update wipes it**):
  ```bash
  cd "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/" && ln "Slippi Dolphin" Dolphin
  ```
- **`DYLD_LIBRARY_PATH=/opt/homebrew/lib`** must prefix any run that imports `keystone` (macro builders do; the harness alone does not — the driver exports it anyway).

## Run (agent path) — START HERE
One command verifies prereqs, kills any stale Dolphin, then runs the canonical bring-up (launch → dme-hook → auto-load slot 2 → observe Marth/Fox state → snapshot+restore MEM1):
```bash
bash .claude/skills/run-melee-harness/smoke.sh
```
Expected tail (~15s, exit 0):
```
  port 1: entity_ptr=0x80C7D260 char_id=0x12 action_state=0x000E port_id=0   # Marth
  port 2: entity_ptr=0x80CC9340 char_id=0x01 action_state=0x000E port_id=1   # Fox
[PASS] direct launch + dme hook, scenario observable via dme, frame counter live, restore_snapshot reverts MEM1.
```
`[PASS]` = harness alive. The per-stage verifiers extend this: `verify_inject_gecko.py` (boot gecko install), `verify_meta_flush.py` (runtime code patch), `verify_bp.py` (software breakpoints), `verify_d_standalone_v2.py` (shipped macro reproduces). Each prints `[PASS]`/`[FAIL]` and exits non-zero on failure.

## Drive a macro (offline dev/play)
`play_wavedash_offline.py` is the current worked example — it installs a macro and **hands you the controls** (leaves Dolphin running; play with a keyboard/pad). The launch+drive shape is the same for any macro: kill stale → `Harness().launch()` → `hook_dme()` → seed slot 2 → install cave via meta-flush → drive/observe via dme.
```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 play_wavedash_offline.py   # installs wavedash macro, then play (W=up=wavedash)
```
To programmatically drive + observe (the agent path for macro dev), do everything in **one process** (see any `offline_*.py` / `play_wavedash_monitor.py`): launch + hook + `seed_snapshot()` + `iw.write_instrs`/`patch_branch` to install a cave + read state each frame with `h.read_word` / `h.player_data_ptr(port)` while `h.wait_frames(1)` single-steps. **dme cannot re-attach from a fresh process** — to monitor a running session, build the monitor into the launching script.

## Run (human path)
There is no separate "human launch" — the harness *is* how you launch. To just play offline, run `play_wavedash_offline.py` (above) and click the Dolphin window. Online play uses `play_d2.py` and the `online_*.py` scripts (read `docs/ONLINE_MACRO_GUIDE.md` first — online has different, desync-sensitive rules).

## Gotchas (battle scars — see also CLAUDE.md, HARNESS.md)
- **dme hooks by process name "Dolphin"** — the harness launches a hardlink so `p_comm == "Dolphin"`. A user's own **"Slippi Dolphin"** (the Launcher) coexists and is ignored by `dme.hook()`, BUT a *stale* hardlink "Dolphin" mis-attaches → always `pkill -9 -x Dolphin` and poll `pgrep -x Dolphin` empty before launching (the driver does this). `pkill` returns instantly but the process takes seconds to die.
- **dme re-attach from a new process gives no-hook / torn reads.** Launch + hook + observe in ONE process. (Cost a detour this session; `play_wavedash_observe.py` is the failed re-attach version, `play_wavedash_monitor.py` the in-process fix.)
- **No screenshots:** macOS Screen Recording perm is not granted, so `screencapture` of the Dolphin window fails ("could not create image from window"). This is expected — observe via dme reads (action states, positions, frame counter), which is the right handle for this harness anyway.
- **Runtime patches don't survive `seed_snapshot()` / savestate load** (both reload MEM1). Install caves AFTER seeding; iterate by in-game cycling, not reloading slot 2.
- **A Slippi update wipes the `Dolphin` hardlink** and can write a duplicate `isopaths` in `Dolphin.ini` (handled via `configparser(strict=False)`). Re-`stat` the hardlink if `dme.hook()` starts failing.
- **Loading a savestate writes slot 1** (the harness's gecko-persist round-trip uses slot 1 as scratch). Don't keep anything important there.
- **macro-build vs harness:** scripts importing `keystone` need `DYLD_LIBRARY_PATH=/opt/homebrew/lib`; **always capstone/keystone-verify a cave's branches before launching Dolphin** (hand/auto-encoded branch offsets are the #1 silent-failure source).

## Troubleshooting (errors actually hit)
- `dme.hook() failed after retries` / all reads raise `Could not read/write memory` → a stale Dolphin, or SIP re-enabled. `pkill -9 -x Dolphin`, confirm `csrutil status` = disabled, retry.
- Bring-up hangs at "Seeding…" / `P1 entity never became valid` → synthetic F2 isn't reaching Dolphin: Accessibility not granted, or Hotkey device isn't `Quartz/0/Keyboard & Mouse`. Grant Accessibility; check Dolphin Hotkeys.
- `keystone` import error → missing `DYLD_LIBRARY_PATH=/opt/homebrew/lib`.
- IntCPU "Unknown instruction" panic on savestate load → the vendored `GALE01r2.ini` / `UsePanicHandlers=False` overrides aren't applied; the harness sets both in its tmp user dir, so this means a launch path bypassed `Harness.launch()`.
