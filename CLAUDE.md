# CLAUDE.md

This file is auto-loaded by Claude Code. It's the entry point for any agent working in this repo.

## Project goal

Creating a frame-1 reaction macro for Super Smash Bros. Melee (NTSC 1.02): where the online opponent starts a grab,  and Fox (the offline player) executes a jump-cancelled shine on the same frame. 



A Frame-1 reaction macro for Super Smash Bros. Melee (NTSC 1.02): when Marth (P1) starts a grab, Fox (P2) executes a jump-cancelled shine on the same frame. The shipped macro is `candidate_d_standalone_v2.py` (paste into a Slippi user dir to use offline or online). The harness exists so additional macros can be built/iterated with breakpoint-driven debugging. See `docs/Project_Context.md` for the full pre-harness history.

## First moves for a new agent

1. Skim **this file** (architecture + gotchas).
2. If you need the harness running, see [`HARNESS.md`](HARNESS.md) §9 (first-time setup) — SIP disabled, Accessibility granted, Dolphin hardlink in place.
3. If you're using the debugger to discover something new, see [`WORKFLOW.md`](WORKFLOW.md).
4. Looking up an address/offset/state ID? Search [`SSBM memory address sheet/*.csv`](SSBM%20memory%20address%20sheet/) **first** — it's authoritative; `docs/Project_Addresses.md` is a curated subset.

## Architecture in one page

The working harness is `melee_harness.py` (`Harness` class). It is **all-dme, no libmelee** — libmelee was dropped because loading a savestate corrupts its Slippi EXI channel into permanent desync. `dolphin-memory-engine` (`dme`) handles everything: observation, scenario driving, reset, and (via the layers below) instruction-memory writes.

### Three injection paths

1. **Boot-time gecko install (`Harness.install_gecko_c2`).** Stages C2 codes into a tmp `GameSettings/GALE01r2.ini`; Slippi's bootloader reads the INI at boot, copies each body into a code cave, and flushes the icache. **Must be called before `launch()`.** This is the path the shipped macro uses. Demo: `verify_inject_gecko.py`.
2. **Runtime dme+meta-flush (`instr_writer.write_instrs`).** Install ONE meta-flush gecko at boot whose only job is to `dcbf`/`icbi`/`isync` a dme-controlled range when asked. After that, the harness can dme-write new PPC instructions anywhere in MEM1 and have them take effect within ~1 frame. Demo: `verify_meta_flush.py`. **Caveat:** runtime patches do not survive `restore_snapshot()` (snapshot is taken before runtime patches exist).
3. **Raw dme write to instruction memory.** Confirmed non-functional on Slippi Dolphin — the emulated CPU's instruction fetch never observes dme writes without an explicit `dcbf`+`icbi` on the affected lines. Diag at `old&unused/diag_inject_no_savestate.py`. Avoid.

### Software breakpoints (`bp.py`)

Built on path #2. `bp.set_breakpoint(h, addr)` overwrites the instruction at `addr` with a branch to a per-slot handler that snapshots r0..r31 + LR + CTR + CR to a fixed scratch RAM region, signals a hit flag, then spins on a continue flag. dme observes the hit, reads the snapshot (and optionally edits it), then sets the continue flag — handler restores registers, runs the displaced original, branches to addr+4. Game is frozen while the handler spins (Dolphin's other threads keep running, so the window stays responsive). **Not netplay-safe**; dev/offline only. Demo: `verify_bp.py`, conditional/step extensions in `verify_bp_cond.py`/`verify_bp_step.py`.

### Reset model

There is no programmatic savestate API. The user manually loads savestate slot 2 once per session (Dolphin GUI: Emulation > Load State > Slot 2), or the harness can send synthetic F2. `seed_snapshot()` waits for an in-game state, then snapshots **all of MEM1** (24 MB). `restore_snapshot()` writes the full MEM1 back each iteration — that single write reverts game state, code patches installed before snapshot, and the frame counter together. Writing the full 24 MB occasionally detaches dme; `restore_snapshot()` re-hooks defensively.

### Process-name + Slippi quirks (macOS)

- `dme.hook()` scans for a process literally named `Dolphin`, but Slippi's executable is `Slippi Dolphin`. The harness launches via a hardlink named `Dolphin` placed next to the real executable (`DOLPHIN_HARDLINK` in `melee_harness.py`). If `pgrep -x Dolphin` finds stale processes, `dme` may attach to the wrong one — close them first.
- The harness builds a **tmp Dolphin user dir** per launch (symlinks every subdir of the real Slippi user dir except `GameSettings` and `Config`), then writes overrides:
  - `GameSettings/GALE01r2.ini` — vendored from `./GALE01r2.ini`; replaces Slippi's default gecko list with a minimal one. Without it, savestate loads trigger an IntCPU "Unknown instruction" dialog.
  - `Config/Dolphin.ini` — copied with `Interface.UsePanicHandlers=False`, which auto-dismisses the IntCPU panic dialog.
- macOS SIP **must be disabled** for `dme` to use `task_for_pid` against Dolphin. Without that, every read/write fails regardless of entitlements.

### Memory map notes (read these — they save hours)

- `0x80453130` is the P1 **GObj** pointer, not Player Data directly. Player Data is at `*(GObj + 0x2C)`. All the Player-Data-relative offsets (`OFF_ACTION_STATE=0x10`, `OFF_BUTTONS=0x65C`, etc.) require this double-indirection. `docs/Project_Addresses.md` does not call out the `+0x2C` step; `Entity_Data_Offsets.csv` does. Use `Harness.player_data_ptr(port)`.
- Two frame counters in `Global_Addresses.csv`: `0x80479D60` (primary, may reset between scenes) and `0x804D7420` (power-on count, never resets). `_pick_frame_counter()` auto-selects whichever is advancing.
- Default code cave: `0x803FA3E8` (debug-menu tables region, `0x1F04` bytes). Safe to clobber.
- **Hook `0x803775C0` is now taken by the meta-flush gecko.** Don't reuse it. The per-frame pad-read at `0x803775B8` (vanilla `lhz r0, 0(r25)` / `0xA0190000`) is free.
- Netplay-safety pattern: scene check via `0x80489D30` (compare to `0x208` for Slippi online), local-port check via `r13 - 0x49E4`. Disassembled in `docs/Gecko_Code_Analysis.md`.
- **PPC r0-as-rA trap**: in `addi`, `addis`, `lis`, `stmw`, and any load/store, an `rA` *field* value of 0 reads as the literal value 0, NOT register r0. `addi r0, r0, 16` computes `16`, not `r0 + 16`. Use r3..r12 as base registers; `instr_writer.py` and `bp.py` show the pattern.

### Authoritative reference: the address sheet

`SSBM memory address sheet/` contains the full SSBM data sheet exported as CSVs (`Global_Addresses.csv`, `Entity_Data_Offsets.csv`, `Char_Data_Offsets.csv`, `Function_Addresses.csv`, `Free_Memory.csv`, `Action_State_Reference.csv`, `ID_Lists.csv`, etc.). **Search here first** for any address, offset, action-state ID, or free-memory region before assuming something is undocumented.

## Common commands

```bash
# Verify the harness is alive on this machine (~12s).
python3 verify_savestate.py

# Verify boot-time gecko install path (~25s).
python3 verify_inject_gecko.py

# Verify runtime code-patch primitive (Phase 1, ~25s).
python3 verify_meta_flush.py

# Verify software breakpoints (Phase 2, ~25s).
python3 verify_bp.py

# Verify the shipped macro reproduces JC-shine on a savestate (~15s).
python3 verify_d_standalone_v2.py

# Live play: control Marth on P1, gecko auto-JC-shines Fox on P2.
python3 play_d2.py

# If Dolphin wedges after a failed run:
pkill -9 -x Dolphin
```

Each `verify_*.py` prints `[PASS]` / `[FAIL]` and exits non-zero on failure. There is no test framework beyond these one-shots.

Hard-coded paths to be aware of in `melee_harness.py`: `DOLPHIN_HARDLINK`, `ISO_PATH`, `USER_DIR`, `GAME_SETTINGS_INI`. Update these if the machine layout changes.

## Key files

| File | What |
| --- | --- |
| `melee_harness.py` | `Harness` class. Launch, hook dme, F2 savestate load, MEM1 snapshot/restore, gecko staging. Read its module docstring + `Harness` first. |
| `scenario.py` | In-game trigger + observation helpers (`force_action_state`, scratch addresses, action-state constants). |
| `instr_writer.py` | Meta-flush gecko + `write_instrs` / `patch_branch` / `flush_range` / `wait_for_meta_flush_alive`. Phase 1. |
| `bp.py` | Software BP primitive: `set_breakpoint`, `wait_for_hit`, `read_snapshot`, `write_snapshot`, `continue_`, `remove_breakpoint`. Phase 2. |
| `candidate_d_standalone_v2.py` | **The shipped macro.** Netplay-safe Frame-1 JC-shine. |
| `candidate_d2.py` + `play_d2.py` | Same macro packaged for live harness-driven play. |
| `verify_*.py` | Per-stage smoke tests; `[PASS]` / `[FAIL]` + exit code. |
| `diag_meta_flush.py`, `diag_cave_dump.py`, `diag_cave_layout.py` | Runtime probes — dump gecko caves, verify install location, etc. |
| `GALE01r2.ini` | Vendored minimal GameSettings INI; harness uses as base for tmp-user-dir overrides. |
| `docs/Project_Context.md` | Pre-harness exploration history. |
| `docs/Project_Addresses.md` | Curated address quick-reference. Starting point, NOT source of truth. |
| `docs/Gecko_Code_Analysis.md` | Disassembly of reference codes including netplay-safety check pattern. |
| `docs/Spot_Dodge_Macro.md` | Earlier hardware-PADStatus-hook gecko approach; useful as worked example. |
| `docs/MACRO_PLAN.md` | Original development plan (mostly executed). |
| `docs/sessions/2026-05-15.md` | Session log: Candidate B/D attempts; documents the D.1 puzzle. |
| `gecko-master/`, `slippi-ssbm-asm-master/` | Vendored PowerPC assemblers and the official Slippi codeset. Do not modify. |
| `dme_experiment/` | Pure-dme exploration (no gecko codes). See its README + FINDINGS.md. |
| `old&unused/` | Archived iteration history. Gitignored. Browse for worked examples; do not depend on for live code. |

## Conventions

- Hex literals everywhere: `0x...` for both addresses and PPC instruction words.
- PPC instructions are stored as **big-endian natural ints** in Python lists (e.g. `0x3D80803F` for `lis r12, 0x803F`), then written via `dme.write_word` or `harness.write_words`.
- When writing gecko codes, `gecko_c2_lines()` in `melee_harness.py` formats them as Dolphin GameSettings INI lines.
- Verify encoded gecko bodies with capstone (see top of `verify_meta_flush.py`) OR with keystone (see `verify_v2_with_keystone.py`) BEFORE launching Dolphin. Hand-counted branch offsets are the #1 source of "gecko silently doesn't fire" bugs.
- The `_log()` helper in `melee_harness.py` prefixes timestamps relative to harness start.
