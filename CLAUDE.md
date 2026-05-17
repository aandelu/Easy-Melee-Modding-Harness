# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

A Frame-1 reaction macro for Super Smash Bros. Melee (NTSC 1.02): when Marth (P1) starts a grab, Fox (P2) executes a jump-cancelled shine on the same frame. Must eventually be Slippi-netplay-safe (only spoof the local player's inputs, only on online matches). Currently being developed and tested offline against a savestate. See `Project_Context.md` for the full history.

## Architecture

The working harness is `melee_harness.py` (`Harness` class). It is **all-dme, no libmelee** — libmelee was dropped because loading a savestate corrupts its Slippi EXI channel into permanent desync. `dolphin-memory-engine` (`dme`) handles everything: observation, scenario driving, reset, and (in some experiments) code writes.

### Two injection paths, only one works

1. **Boot-time gecko install (the supported path).** `Harness.install_gecko_c2(...)` stages C2 codes into a tmp `GameSettings/GALE01r2.ini`; Slippi's bootloader reads the INI at boot, copies each body into a code cave, and flushes the instruction cache. Must be called **before** `launch()`. This is how `verify_inject_gecko.py` works.
2. **dme runtime code-cave injection (`Harness.inject()`, `finalize_payload()`).** Confirmed non-functional on Slippi Dolphin — the emulated CPU's instruction fetch never observes dme writes to code memory, even in pure interpreter mode (see `diag_inject_no_savestate.py`). The code is kept for reference but is not a viable path.

### Reset model

There is no programmatic savestate API. The user manually loads savestate slot 2 once per session (Dolphin GUI: Emulation > Load State > Slot 2). `seed_snapshot()` waits for an in-game state, then snapshots **all of MEM1** (24 MB). `restore_snapshot()` writes the full MEM1 back each iteration — that single write reverts game state, code patches, and the frame counter together. Writing the full 24 MB occasionally detaches dme; `restore_snapshot()` re-hooks defensively.

### Process-name + Slippi quirks (macOS)

- `dme.hook()` scans for a process literally named `Dolphin`, but Slippi's executable is `Slippi Dolphin`. The harness launches via a hardlink named `Dolphin` placed next to the real executable (`DOLPHIN_HARDLINK` in `melee_harness.py`). If `pgrep -x Dolphin` finds stale processes, `dme` may attach to the wrong one — close them first.
- The harness builds a **tmp Dolphin user dir** per launch (symlinks every subdir of the real Slippi user dir except `GameSettings` and `Config`), then writes overrides:
  - `GameSettings/GALE01r2.ini` — vendored from `./GALE01r2.ini`; replaces Slippi's default gecko list with a minimal one. Without it, savestate loads trigger an IntCPU "Unknown instruction" dialog.
  - `Config/Dolphin.ini` — copied with `Interface.UsePanicHandlers=False`, which auto-dismisses the IntCPU panic dialog (stale codehandler branches into restored heap).
- macOS SIP **must be disabled** for `dme` to use `task_for_pid` against Dolphin. Without that, every read/write fails regardless of entitlements.

### Memory map notes (important)

- `0x80453130` is the P1 **GObj** pointer, not Player Data directly. Player Data is at `*(GObj + 0x2C)`. All the Player-Data-relative offsets (`OFF_ACTION_STATE=0x10`, `OFF_BUTTONS=0x65C`, etc.) require this double-indirection. `Project_Addresses.md` does not call out the `+0x2C` step; `Entity_Data_Offsets.csv` does. Use `Harness.player_data_ptr(port)`.
- Two frame counters in `Global_Addresses.csv`: `0x80479D60` (primary, may reset between scenes) and `0x804D7420` (power-on count, never resets). `_pick_frame_counter()` auto-selects whichever is advancing.
- Default code cave: `0x803FA3E8` (debug-menu tables region, `0x1F04` bytes, sourced from `Free_Memory.csv`). Safe to clobber.
- Hook used for per-frame triggers in current experiments: `0x803775C0` (pad-process loop, vanilla `lbz r0, 2(r25)` / `0x88190002`). Fires per pad per frame regardless of action state — preferable to action-state-conditional hooks like `0x800CB60C` (jumpsquat only).
- Netplay-safety pattern: scene check via `0x80489D30` (compare to `0x208` for Slippi online), local-port check via `r13 - 0x49E4`. Disassembled in detail in `Gecko_Code_Analysis.md` (Flash Red on Failed L-Cancel).

### Authoritative reference: the address sheet folder

`SSBM memory address sheet/` contains the full SSBM data sheet exported as CSVs (`Global_Addresses.csv`, `Entity_Data_Offsets.csv`, `Char_Data_Offsets.csv`, `Function_Addresses.csv`, `Free_Memory.csv`, `Action_State_Reference.csv`, `ID_Lists.csv`, etc.). **Search here first** for any address, offset, action-state ID, or free-memory region before assuming something is undocumented or needs runtime discovery — it is more complete than the curated `Project_Addresses.md`.

## Common commands

The repo is plain Python scripts, no build system, no test runner. Most scripts are diagnostic/verification one-shots that launch Dolphin themselves.

```bash
# Stage-1 verification: launch, hook dme, seed snapshot, prove restore works.
python3 verify_savestate.py

# Verify the boot-time gecko-install path (no savestate needed).
python3 verify_inject_gecko.py

# Diagnostic: prove dme runtime code-injection does NOT work on Slippi Dolphin.
python3 diag_inject_no_savestate.py

# Legacy smoke test for libmelee + dme sharing the process (kept for reference).
python3 smoke_test.py

# If Dolphin gets wedged after a failed run:
pkill -9 -x Dolphin
```

There is no test framework — `verify_*.py` and `diag_*.py` are the closest thing. Each prints `[PASS]` / `[FAIL]` and exits non-zero on failure.

Hard-coded paths to be aware of (in `melee_harness.py` and `smoke_test.py`): `DOLPHIN_HARDLINK`, `ISO_PATH`, `USER_DIR`, `GAME_SETTINGS_INI`. Update these if the machine layout changes.

## Key files

- `melee_harness.py` — the harness. Read its module docstring + `Harness` class first.
- `Project_Context.md` — running narrative of what's been tried and why each previous approach failed (C0 codes, C2 hooks, dolphin-memory-engine pre-SIP-disable, named pipes).
- `Project_Addresses.md` — curated quick-reference for the most-used addresses. Treat as a starting point, not the source of truth.
- `Gecko_Code_Analysis.md` — disassembly of three reference codes including the Slippi netplay-safety check pattern.
- `Spot_Dodge_Macro.md` — earlier hardware-PADStatus-hook approach (Gecko-only); useful as worked example of the `0x803775C0` hook.
- `GALE01r2.ini` — vendored minimal GameSettings INI used as base for tmp-user-dir overrides.
- `gecko-master/`, `slippi-ssbm-asm-master/` — vendored third-party references (PowerPC assemblers, Slippi codes). Do not modify.

## Conventions in this repo

- Hex literals everywhere: `0x...` for both addresses and PPC instruction words.
- PPC instructions are stored as **big-endian natural ints** in Python lists (e.g. `0x3D80803F` for `lis r12, 0x803F`), then written via `dme.write_word`.
- When writing gecko codes, `gecko_c2_lines()` in `melee_harness.py` formats them as Dolphin GameSettings INI lines (`C2{hook:6X} {n_lines:8X}` header + word pairs).
- The `_log()` helper in `melee_harness.py` prefixes timestamps relative to harness start.
