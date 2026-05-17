# Agentic Gamecube Modding (designed for SSBM v1.02 NTSC)

Just describe the mod you want. Your agent will now be able to handle the diassembly, memory writes, and even testing. Standard output is a gecko code.

## Where to start

| You are… | Read this |
| --- | --- |
| A new agent / collaborator | [`CLAUDE.md`](CLAUDE.md) — project goals, architecture in one page, gotchas |
| Hooking up the harness for the first time | [`HARNESS.md`](HARNESS.md) §9 (first-time setup checklist) |
| Using the debugger to answer "what does the game do at PC X" | [`WORKFLOW.md`](WORKFLOW.md) |
| Looking up an address, offset, or action-state ID | [`SSBM memory address sheet/`](SSBM%20memory%20address%20sheet/) (CSVs) first, then [`docs/Project_Addresses.md`](docs/Project_Addresses.md) |
| Reading prior worked gecko examples | [`docs/Gecko_Code_Analysis.md`](docs/Gecko_Code_Analysis.md), [`docs/Spot_Dodge_Macro.md`](docs/Spot_Dodge_Macro.md) |
| Curious why a given design choice was made | [`docs/Project_Context.md`](docs/Project_Context.md) (failed approaches history) |

## Quick commands

```bash
# 1. Verify the environment is healthy (launches Dolphin, ~12s)
python3 verify_savestate.py

# 2. Verify boot-time gecko install path works (~25s)
python3 verify_inject_gecko.py

# 3. Verify the runtime code-patch primitive works (~25s)
python3 verify_meta_flush.py

# 4. Verify software breakpoints work (~25s)
python3 verify_bp.py

# Live play: be Marth on P1, the gecko auto-JC-shines Fox on P2
python3 play_d2.py
```

All scripts exit 0 on `[PASS]`, non-zero on `[FAIL]`.

## What's shipped

- `candidate_d_standalone_v2.py` — the **production gecko code** for the JC-shine. Self-contained — paste the assembled bytes into your Slippi `GameSettings/GALE01r2.ini` to use offline or online.
- `candidate_d2.py` + `play_d2.py` — the same logic packaged for **live play through the harness** (Dolphin window is open, you control P1 with a real pad, the harness drives Fox).
- [`auto_lcancel/`](auto_lcancel/) — second macro: **automatic L-cancel for Fox**. Installs at runtime via meta-flush + a PadRead hook; presses L exactly once per 7-frame cycle while in any aerial-attack state, so every landing during an aerial L-cancels. 20-trial verify (`auto_lcancel/test_fox_aerials.py`) covers all 5 aerials × both hop heights with no airdodge edge cases. Build session log: [`docs/sessions/2026-05-17.md`](docs/sessions/2026-05-17.md).

## Repo layout

```
README.md, CLAUDE.md, HARNESS.md, WORKFLOW.md       entry points
melee_harness.py                                    launch / dme / savestate / snapshot
scenario.py                                         in-game trigger + observation
instr_writer.py                                     meta-flush gecko (Phase 1)
bp.py                                               software breakpoints (Phase 2)
candidate_d_standalone_v2.py                        shipped JC-shine macro
candidate_d2.py + play_d2.py                        JC-shine live-play wrapper
auto_lcancel/                                       auto-L-cancel macro + tests (2nd macro)
verify_*.py                                         smoke tests, exit 0 on PASS
diag_*.py                                           runtime probes / cave dumps
GALE01r2.ini                                        base GameSettings INI vendored from libmelee
docs/                                               reference + history docs
  sessions/                                         dated session logs
SSBM memory address sheet/                          authoritative address CSVs
gecko-master/, slippi-ssbm-asm-master/              vendored third-party (do not modify)
dme_experiment/                                     pure-dme parallel exploration (see its README)
old&unused/                                         archived iteration history (gitignored)
```

## Platform requirements

- macOS with **SIP disabled** (required for `task_for_pid`; dme can't read Dolphin's memory otherwise)
- Accessibility permission granted to the terminal (required for synthetic F2 keystrokes to load savestates)
- Slippi Launcher installed; a `Dolphin` hardlink alongside the real `Slippi Dolphin` binary (so `dme.hook()`'s name-based process search finds it). See `HARNESS.md` §9.
- Python 3.13 with `dolphin-memory-engine`, `pyobjc` (Quartz/AppKit), and `capstone` (for verifying encoded gecko bodies)
