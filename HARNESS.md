# Frame-1 Macro Harness — Architecture & Usage

Closed-loop development environment for the Frame-1 gecko macro project (Fox auto-shines on Marth's grab). The harness lets us iterate **autonomously** — install a candidate gecko code, reset game state, drive the trigger, observe the reaction, classify, repeat — with no human interaction per cycle.

This document is the authoritative description of the **current** architecture. `Project_Context.md` captures the earlier exploration that led here.

---

## 1. Goal

Develop a Slippi-netplay-safe gecko code that makes Fox (P2) react to Marth's (P1) grab on the **exact frame** the grab is initiated. The harness exists so we can test candidates without manually reloading savestates, reading memory, or eyeballing frame counters.

## 2. End-to-end timing

```
Launch Dolphin                     ~5 s
F2 auto-load savestate slot 2      ~5 s
Per-trial reset + scenario         ~1 s
```

About **11 s** from `python verify_scenario.py` to first classified trial. Subsequent trials within the same Dolphin session are ~1 s each (restore + drive + observe).

## 3. Architecture

```
                ┌──────────────────────────────────────────────┐
                │  Slippi Dolphin (process name: "Dolphin")    │
                │  - launched via subprocess + hardlink        │
                │  - GameSettings/GALE01r2.ini contains:       │
                │     * vendored libmelee codes                │
                │     * harness-staged C2 candidates           │
                │  - Slippi bootloader installs codes at boot, │
                │    with proper icache flush                  │
                │  - emulated CPU runs game; codes fire        │
                │    in MEM1                                   │
                └──────────────────────────────────────────────┘
                                  │  ▲
                  data writes /   │  │  data reads
                  triggers        │  │  (action state, ptrs)
                                  ▼  │
                ┌──────────────────────────────────────────────┐
                │  dolphin-memory-engine (dme)                 │
                │  - task_for_pid → mach_vm read/write         │
                │  - hooks by process-name "Dolphin"           │
                └──────────────────────────────────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────────────────┐
                │  Harness (Python)                            │
                │  - subprocess launches Dolphin               │
                │  - synthetic F2 keystroke → savestate load   │
                │  - MEM1 snapshot/restore = reset mechanism   │
                │  - scenario.py drives trigger + observes     │
                └──────────────────────────────────────────────┘
```

**Key decisions** and the reasoning behind each:

| Decision                                  | Why                                                                                                                                                    |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| All-dme (no libmelee)                     | Savestate load corrupts libmelee's Slippi EXI channel → `console.step()` desync. dme has no host-side stateful channel, survives MEM1 reset.           |
| Boot-time gecko install (not dme patches) | Dolphin's CPU emulator does **not** observe dme writes to instruction memory (even pure interpreter). Slippi's bootloader patches with icache flush.   |
| Synthetic F2 to load savestate            | Macros that load via the GUI menu need a click each iteration; F2 hotkey can be sent via CGEvent (`kCGHIDEventTap`) once Dolphin's hotkey device is `Quartz/0/Keyboard & Mouse`. |
| MEM1 snapshot/restore for reset           | Loading Dolphin's own savestate via F2 each iteration works but is slower (~5 s). After the **first** F2-load we snapshot all of MEM1 via dme; subsequent resets are just a 24 MB `dme.write_bytes`. |
| Vendored `GALE01r2.ini` override          | Slippi Dolphin's default GALE01 gecko codes panic during savestate load with `IntCPU: Unknown instruction 00000007 at PC=80c833a4` (stale codehandler branch into restored heap). The vendored INI (from libmelee, minimal contents) suppresses the bad codes. |
| `UsePanicHandlers=False` in Dolphin.ini   | Cosmetic backstop: even after the GameSettings override, certain savestate loads still produce a benign panic dialog. We auto-dismiss by config.       |
| `Dolphin` hardlink                        | `dme.hook()` literally scans for a process named "Dolphin"; Slippi's binary is named "Slippi Dolphin". We launch via a hardlink that gives the process the right `p_comm`. |

## 4. File inventory

### Source
| File                       | Purpose                                                                                                              |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `melee_harness.py`         | `Harness` class. Launches Dolphin, hooks dme, F2-loads savestate, snapshots/restores MEM1, installs gecko C2 codes, exposes dme reads/writes through GObj→Player Data indirection. |
| `scenario.py`              | Trigger + observation. `force_action_state(...)`, `run_grab_trial(...)`, `classify_trial(...)`.                       |
| `GALE01r2.ini`             | Vendored from libmelee (`{extra_codes}` substituted empty). Copied into the temp user dir as `GameSettings/GALE01r2.ini`. Replaces Slippi's default codes. |

### Verify scripts (kept, all passing)
| File                        | Validates                                                                                                  |
| --------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `verify_savestate.py`       | Stage 1: launch + dme hook + autonomous F2 savestate load + MEM1 snapshot/restore + frame counter + entity observation. |
| `verify_inject_gecko.py`    | Stage 3: gecko-C2 install via the bootloader actually patches the hook (counter advances ~100/s).          |
| `verify_scenario.py`        | Stage 4: full iteration loop. Trigger lands, Fox stays neutral without a macro (baseline).                 |

### Archived in `old&unused/`
Everything from the pre-harness exploration era (read_*/manual_asm*/disas*/extract_*/observe_*/memory_watcher/memory_bridge/test_*/jump_p2/live_inject/inject_gecko/melee_bot/etc.) plus this-session diagnostics that aren't part of the iteration loop. Notable inhabitants:

| File                            | Why archived                                                                                        |
| ------------------------------- | --------------------------------------------------------------------------------------------------- |
| `diag_interpreter.py`           | Measured Dolphin's CPU speed in interpreter mode (CPUCore=0). Result: ~5 fps, too slow.             |
| `diag_inject_no_savestate.py`   | Proved dme writes to instruction memory are NOT observed by the emulated CPU even under CPUCore=0.   |
| `verify_inject.py`              | Old dme-runtime-inject test; counter never advanced because Dolphin doesn't see dme code patches.   |
| `diag_savestate.py`, `diag_manual_f2.py`, `diag_isolate.py`, `diag_hook.py` | Debugging artifacts from the libmelee era + failed CGEvent F2 first attempts. |
| `smoke_test.py`                 | Single-controller libmelee smoke test from before the pivot.                                        |
| `memory_bridge.py`, `memory_watcher.py`, `run_mw.py`, `dump_mw.py` | Pre-harness "Dolphin MemoryWatcher" approach (read-only via UDS); replaced by dme. |
| `manual_asm*.py`, `disas*.py`, `read_*.py`, `inject_gecko.py`, `live_inject.py`, `melee_bot.py` | One-off scripts from the libmelee/lldb era. |
| `SSBM_Gecko Codes - SuperCombo Wiki.html` + `_files/` | Saved wiki page; superseded by the curated `Gecko_Code_Analysis.md`. |

### Reference docs (pre-existing)
| File                            | What it has                                                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `Project_Context.md`            | Pre-harness exploration history. Read for backstory; not authoritative for current architecture.                                            |
| `Project_Addresses.md`          | Curated memory map (P1 entity ptr, action state offsets, character IDs). Slightly **incomplete** — does not call out the GObj→Player Data indirection. |
| `Gecko_Code_Analysis.md`        | Disassembly of relevant Slippi-safe codes (Swap X/Z, UnclePunch X+Y, Flash Red L-Cancel). Useful templates.                                |
| `Spot_Dodge_Macro.md`           | Test macro spec: hooks `0x803775C0`, keys on Port 1 pressing Z.                                                                            |
| `SSBM memory address sheet/`    | **Authoritative** memory map. CSVs: `Global_Addresses`, `Entity_Data_Offsets`, `Char_Data_Offsets`, `Function_Addresses`, `Free_Memory`, `Action_State_Reference`, `ID_Lists`, etc. Check here first before assuming something is undocumented. |
| `slippi-ssbm-asm-master/`       | The official Slippi gecko codeset (Bootloader, Online, Recording, Playback, External, Common). Build via `gecko` Go tool. Convention reference for any new `.asm` we author. |
| `gecko-master/`                 | The `gecko` Go tool source. Compiles `.asm` files → `.ini` gecko codes.                                                                    |

## 5. Workflow — iterating on a candidate

```python
from melee_harness import Harness
from scenario import run_grab_trial, classify_trial, WAIT

h = Harness()

# Stage the candidate macro BEFORE launch.
# Slippi's bootloader installs it at boot with proper icache flush.
h.install_gecko_c2(
    name="fox-shine-on-marth-grab-v1",
    hook_addr=0x...,             # pick from `slippi-ssbm-asm-master/...` taken-hook map
    logic_words=[0x..., ...],    # PPC instruction words (raw hex, big-endian natural ints)
    displaced_orig=0x...,        # the instruction that vanilla v1.02 has at hook_addr
)

h.launch()
h.hook_dme()
h.seed_snapshot()                # auto-loads slot 2 via F2, then snapshots MEM1

for i in range(N_TRIALS):
    trial = run_grab_trial(h, observe_frames=12)
    result = classify_trial(trial, baseline_p2_state=WAIT)
    print(f"trial {i}: reacted={result['reacted']} "
          f"latency={result['latency_frames']} "
          f"reaction_state=0x{result.get('reaction_state', 0):04X}")
h.close()
```

`classify_trial` returns:
- `reacted: bool` — did P2 leave the baseline state?
- `latency_frames: int | None` — frames from trigger to reaction (0 = same frame = Frame-1)
- `reaction_state: int` — what action state P2 transitioned to (use this to identify shine empirically — we don't hardcode Fox's shine action state ID)
- `records: list` — the full per-frame (frame, p1_action, p2_action) stream

### Authoring candidate instruction words

For now we hand-emit PPC hex (the harness ships `gecko_c2_lines(...)` which formats the C2 INI bytes). The longer-term path is to author `.s` files in the `slippi-ssbm-asm-master/` conventions and compile them via the `gecko-master/` Go tool — that produces the same `.ini` lines, just with macros, includes, and proper symbol resolution.

Key convention notes for hand-rolling:
- Volatile registers per PPC EABI: `r0`, `r3`–`r12`. Safe to clobber as scratch.
- Non-volatile: `r13`–`r31`. Must be saved+restored.
- `r13` is the small-data-area pointer; **never** clobber.
- At a hook, the **displaced original** instruction will execute AFTER your logic — if it clobbers `r0` (e.g. `lbz r0, 2(r25)` at `0x803775C0`), you can use `r0` freely too.
- `finalize_payload(logic, hook, cave, orig)` builds the dme-runtime-inject form. For Path A (boot-time install), call `install_gecko_c2(name, hook, logic, displaced)` directly — it handles the gecko C2 layout.

## 6. Memory map (the load-bearing bits)

All addresses are for **SSBM v1.02 NTSC** (`GALE01.iso`). See the `SSBM memory address sheet/` CSVs for the full map.

### Player data — `Project_Addresses.md` is incomplete here
```
0x80453130            P1 GObj pointer            (+0xE90 stride per port)
0x80453130 + (port-1)*0xE90 = port's GObj ptr addr
*(GObj+0x00)..0x38     GObj struct (Entity_Data_Offsets.csv: Next/Prev ptrs, render fn, etc.)
*(GObj+0x2C)           → Player Data pointer    ← REQUIRED indirection
*(Player Data+0x04)    character id (word)       (Marth=0x12, Fox=0x01)
*(Player Data+0x0C)    port id (byte)            (0..3, 0-indexed)
*(Player Data+0x10)    action state id (word)
*(Player Data+0x65C)   processed button input register (UnclePunch macro reads this)
```

Use `Harness.player_data_ptr(port)`, `Harness.action_state(port)`, `Harness.char_id(port)`, `Harness.port_id(port)`.

### Action states (selected)
```
0x000E  Wait                            (baseline resting state)
0x00D4  Catch        (standing grab startup)
0x00D5  CatchDash    (dash grab)
0x00D6  CatchTurn    (pivot grab)
0x0155+ Character-specific states       (Fox shine is in here; identify empirically)
```

### Frame counters
```
0x80479D60  Global frame timer          (may not tick during transitions)
0x804D7420  Global Power-on Count       (+1 every frame, never resets)
```

Harness calibrates one of these at `seed_snapshot()` time (`_pick_frame_counter`). Reverts on `restore_snapshot()`.

### Controller / pad
```
0x804C1FAC  Controller 1 Digital Data   (stride 0x44 for P2-P4)
            Bit layout: xxxx xxxx UDLR UDLR xxxS YXBA xLRZ UDRL
0x803775C0  Pad Process Loop hook       (vanilla instruction: lbz r0, 2(r25) = 0x88190002)
            Spot_Dodge_Macro hooks here. r25 = raw PADStatus ptr at this point.
0x80376a20-0x80376a28  Slippi TriggerSendInput — TAKEN by Slippi
```

The **raw PADStatus hardware buffer** base (what `r25` points at) is not in the address sheet; resolve from disassembly if a future macro needs to write directly to it.

### Free memory (code caves)
```
0x803FA3E8 .. 0x803FC2EC   0x1F04 bytes  Debug-menu tables (safe to clobber)
0x803FC420 .. 0x803FDC1C   0x17FC bytes  More debug-menu tables
0x8022887c (0xB0), 0x8032c848 (0x38), 0x8032dcb0 (0x10C), 0x8032ed8c (0x104),
0x80393a5c (0x1B4)                   Unused code function regions
0x804D36A0 .. 0x804D3700   0x60 bytes   Develop-mode color table
```

`DEFAULT_CAVE = 0x803FA3E8` in `melee_harness.py`. The boot-time gecko codehandler allocates its OWN cave (we don't write into `DEFAULT_CAVE` for Path A); the `DEFAULT_CAVE` constant is left as a scratch slot for sentinels/counters during verification.

### Slippi-taken hooks (do not collide)

Bootloader / Common:
```
0x80375380  Bootloader main          (EXI codeset load, heap setup)
0x8015ff60  AddHeap
0x80346314  EXISpoof
0x801a4cb4  AllocSceneBuffer
0x8016d294  IncrementFrameIndex
0x80068eec  InitPlayerData
0x801c154c  InitStageData
```

Online / per-frame:
```
0x801a4de4  StartEngineLoop          (rollback, savestate capture, sound)
0x801a5014  updateFunction branch    (sound + rollback frame handling)
0x80376a20..0x80376a28  TriggerSendInput  (per-frame EXI input send/recv)
0x8016e748  InitOnlinePlay           (allocates ODB/buffers at game start)
0x8016d26c  PauseCounter             (VSModeThink loop)
```

Recording:
```
0x8006b0e0  SendGamePreFrame
0x8006da34  SendGamePostFrame
0x8016e74c  SendGameInfo
0x8016d884  SendGameEnd
```

For a complete inventory grep `slippi-ssbm-asm-master/**/*.asm` for `# Address:` headers.

## 7. Gotchas — hard-won facts to not relearn

1. **dme.hook() requires process name "Dolphin"** — Slippi's binary is "Slippi Dolphin". We launch via a hardlink at `/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Dolphin` to get the right `p_comm`.
2. **`dme.hook()` must run on the main thread.** Wrapping in a daemon thread silently breaks attachment (returns but `is_hooked()` stays False).
3. **Don't pre-open the controller FIFO read ends.** Empirically that breaks dme hooking even though it makes libmelee's `connect()` return instantly. (libmelee era artifact — irrelevant in current architecture but documented in case it crops up.)
4. **dme writes to instruction memory are NOT seen by the emulated CPU**, even with `Core.CPUCore = 0` (pure interpreter). Use `Harness.install_gecko_c2(...)` instead. `Harness.inject()` is dead code; kept only as documentation of what doesn't work.
5. **Loading a Dolphin savestate corrupts libmelee's Slippi EXI channel** (`EXI SLIPPI: Invalid command byte: 0x3A` → permanent desync). Hence libmelee was dropped entirely.
6. **Slippi's default GALE01 gecko codes panic on savestate load** with `IntCPU: Unknown instruction 00000007 at PC=80c833a4 last_PC=80001f18` (gecko codehandler branches into restored runtime heap). The vendored `GALE01r2.ini` override + `UsePanicHandlers=False` together silence this.
7. **Dolphin Hotkey device must be `Quartz/0/Keyboard & Mouse`** in Hotkeys.ini for synthetic F2 to land. The user fixed this once; it persists in their real `USER_DIR/Config/Hotkeys.ini` and our `shutil.copytree` carries it into each tmp dir.
8. **F2 fires too early to load a savestate** if sent right after `launch()`. We must wait for `POWERON_COUNT (0x804D7420)` to start ticking before sending F2 (Slippi Dolphin doesn't accept hotkeys until Melee is past initial boot).
9. **Player data is at `*(GObj+0x2C)`, NOT directly at `0x80453130`'s pointee.** `0x80453130`'s pointee is a `GObj` struct (`Entity_Data_Offsets.csv`); offsets like `0x10`, `0x04`, `0x65C` are inside Player Data which is one indirection deeper. `Project_Addresses.md` glosses over this; the harness's `player_data_ptr(port)` helper does the indirection.
10. **`Project_Addresses.md` is a curated subset** of the full address sheet in `SSBM memory address sheet/`. When chasing an SSBM fact, **always** check the address sheet CSVs first — they have things `Project_Addresses.md` omits (e.g., the frame counter, the GObj layout, the free-memory list).
11. **macOS-specific:** SIP must be disabled (for `task_for_pid`). Accessibility permission must be granted to the Terminal/Python (for CGEvent F2). `AXIsProcessTrusted()` confirms.
12. **macOS has no `timeout` cmd.** Use Python `_deadline` (SIGALRM-based) for hard wall-clock deadlines. SIGALRM does **not** interrupt blocking FIFO `open()` on the main thread, though, so libmelee's `connect()` couldn't be timed out that way.
13. **Don't pipe Python output through `grep`** during long runs — line buffering hides progress. Use `python3 -u … > logfile` instead.

## 8. Limitations / open work

- **Single-savestate scenario only.** The harness seeds from `GALE01.s02`. Multi-state scenarios would need additional savestates and slot routing.
- **Trigger via action-state poke is an abstraction leak.** A macro that watches button presses (e.g., the `Spot_Dodge` test macro keying on Z) won't fire under the current `force_action_state` trigger. If we need to test such a macro, add a small persistent **input-driver gecko** that ORs Z into Port 1's PADStatus when a dme-controlled scratch flag is set. Architecture sketch:
  ```
  Hook: a per-pad-per-frame instruction in the pad-process loop
  Logic: lwz <flag>; if nonzero, OR 0x10 into Port 1 PADStatus[0]; else skip
  dme drives a 1-byte flag in scratch RAM to "press Z" for one frame
  ```
- **Fox's shine action state ID is not yet known.** Identify empirically by installing a working candidate, observing Fox's transitions in `classify_trial(...)`, and reading the `reaction_state` field. Add to scenario.py once known (e.g. `FOX_SHINE_GROUND = 0x...`).
- **The harness restarts Dolphin per session, not per trial.** Multiple trials within one session is fine (the snapshot+restore loop). To test a *different candidate macro*, kill the harness and re-launch — gecko codes are baked into GameSettings at boot time and not hot-swappable.
- **Final gecko-code shipping** still requires `powerpc-eabi-as` for proper assembly compilation via the `gecko-master/` Go tool. The repo's `.exe` assemblers are Windows-only; macOS users need to source a `powerpc-eabi-as` (e.g., from devkitPPC) when ready to compile the final macro. The iteration loop bypasses this by emitting raw PPC hex.

## 9. Run order — first time setup checklist

```
# One-time per machine
1. macOS SIP disabled               (Recovery Mode → csrutil disable)
2. Slippi Launcher installed; Slippi Dolphin exists at
     /Users/<you>/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app
3. Hardlink:
     cd "/Users/<you>/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS"
     ln "Slippi Dolphin" Dolphin
   (verify: `stat -f '%i' "Slippi Dolphin" Dolphin` returns the same inode)
4. Accessibility permission granted to the terminal you run from
     (System Settings → Privacy & Security → Accessibility)
5. Dolphin's Hotkey "Device" set to `Quartz/0/Keyboard & Mouse`
   (Launch Slippi Dolphin once → Controllers → Hotkeys → Device dropdown)
6. Savestate slot 2 (GALE01.s02) present in
     ~/Library/Application Support/com.project-slippi.dolphin/netplay/User/StateSaves/
   with the Marth vs Fox scenario set up.
7. Python deps: `dolphin-memory-engine`, `pyobjc` (Quartz/AppKit).
   libmelee is *not* required at runtime; some files reference it for
   convenience (e.g. vendor of GALE01r2.ini was via `import melee`).
```

Then:

```
$ python3 verify_savestate.py       # ~11 s, should print [PASS]
$ python3 verify_inject_gecko.py    # ~25 s, should print [PASS]
$ python3 verify_scenario.py        # ~12 s, should print [PASS]
```

If all three pass, the harness is ready to iterate on candidate macros.
