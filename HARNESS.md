# Harness — Architecture & Usage

Closed-loop development environment for the Easy Melee gecko macros. The harness lets agents iterate **autonomously** — install a candidate gecko code, reset game state, drive the trigger, observe the reaction, classify, repeat — with no human interaction per cycle.

This document covers the harness architecture and API. It was written in mid-May 2026 and is accurate for the harness itself, but its file inventories predate the 2026-07-24 cleanup — trust `CLAUDE.md`'s routing table, `docs/STATUS.md` for project state, and `docs/REFERENCE.md` for memory-map facts. For the dev workflow built on top, see `WORKFLOW.md`. Pre-harness history: `docs/archive/Project_Context.md`.

---

## 1. Goal

Let agents develop Easy Melee's gecko macros autonomously: test candidate PPC code without manually reloading savestates, reading memory, or eyeballing frame counters. (The harness was originally built for the frame-1 JC-shine — Fox reacts to Marth's grab on the exact frame — and grew into the general macro dev environment; per-macro state lives in `docs/STATUS.md`.)

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
                │     * meta-flush gecko (always, if           │
                │       install_meta_flush was called)         │
                │     * BP handlers (written at runtime via    │
                │       meta-flush)                            │
                │  - Slippi bootloader installs codes at boot, │
                │    with proper icache flush                  │
                │  - emulated CPU runs game; codes fire        │
                │    in MEM1                                   │
                └──────────────────────────────────────────────┘
                                  │  ▲
                  data writes /   │  │  data reads
                  triggers        │  │  (action state, ptrs,
                  BP cont flag    │  │   BP hit flag, regs)
                                  ▼  │
                ┌──────────────────────────────────────────────┐
                │  dolphin-memory-engine (dme)                 │
                │  - task_for_pid → mach_vm read/write         │
                │  - hooks by process-name "Dolphin"           │
                └──────────────────────────────────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────────────────┐
                │  Harness layers (Python)                     │
                │                                              │
                │  bp.py            Phase 2 -- software BPs    │
                │      set_breakpoint, wait_for_hit,           │
                │      read/write_snapshot, continue_          │
                │                                              │
                │  instr_writer.py  Phase 1 -- meta-flush:     │
                │      write_instrs / patch_branch /           │
                │      flush_range. Runtime instruction-       │
                │      memory writes that the CPU observes.    │
                │                                              │
                │  scenario.py      In-game trigger /          │
                │                   observation helpers        │
                │                                              │
                │  melee_harness.py Launch, dme hook, F2       │
                │                   savestate, MEM1 snapshot/  │
                │                   restore, gecko staging     │
                └──────────────────────────────────────────────┘
```

**Key decisions** and the reasoning behind each:

| Decision                                  | Why                                                                                                                                                    |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| All-dme (no libmelee)                     | Savestate load corrupts libmelee's Slippi EXI channel → `console.step()` desync. dme has no host-side stateful channel, survives MEM1 reset.           |
| Boot-time gecko install (the shipped path) | Dolphin's CPU emulator does **not** observe raw dme writes to instruction memory (even pure interpreter). Slippi's bootloader patches with icache flush.   |
| Runtime patches via meta-flush gecko       | The "raw dme doesn't reach the CPU" problem is the *icache flush* part, not dme. One boot-time gecko (`instr_writer.META_FLUSH_LOGIC`) issues `dcbf`/`sync`/`icbi`/`isync` over a dme-controlled range on demand. After that, `write_instrs` patches any MEM1 location and the CPU sees it within ~1 frame. Demo: `verify_meta_flush.py`. Caveat: patches don't survive `restore_snapshot()`. |
| Software breakpoints via overwrite-and-spin | Built on `instr_writer`: overwrite target with branch to a per-slot handler in scratch RAM. Handler snapshots r0..r31 + LR/CTR/CR, signals hit, spins on continue. dme reads/edits the snapshot, releases the spin. Handler restores and runs the displaced original. Demo: `verify_bp.py`. **Not netplay-safe** — the spin halts the entire PPC core. |
| Synthetic F2 to load savestate            | Macros that load via the GUI menu need a click each iteration; F2 hotkey can be sent via CGEvent (`kCGHIDEventTap`) once Dolphin's hotkey device is `Quartz/0/Keyboard & Mouse`. |
| MEM1 snapshot/restore for reset           | Loading Dolphin's own savestate via F2 each iteration works but is slower (~5 s). After the **first** F2-load we snapshot all of MEM1 via dme; subsequent resets are just a 24 MB `dme.write_bytes`. |
| Vendored `GALE01r2.ini` override          | Slippi Dolphin's default GALE01 gecko codes panic during savestate load with `IntCPU: Unknown instruction 00000007 at PC=80c833a4` (stale codehandler branch into restored heap). The vendored INI (from libmelee, minimal contents) suppresses the bad codes. |
| `UsePanicHandlers=False` in Dolphin.ini   | Cosmetic backstop: even after the GameSettings override, certain savestate loads still produce a benign panic dialog. We auto-dismiss by config.       |
| `Dolphin` hardlink                        | `dme.hook()` literally scans for a process named "Dolphin"; Slippi's binary is named "Slippi Dolphin". We launch via a hardlink that gives the process the right `p_comm`. |

## 4. File inventory

### Source
| File                            | Purpose                                                                                                              |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `melee_harness.py`              | `Harness` class. Launches Dolphin, hooks dme, F2-loads savestate, snapshots/restores MEM1, installs boot-time gecko C2 codes, exposes dme reads/writes through GObj→Player Data indirection. |
| `scenario.py`                   | In-game trigger + observation helpers (`force_action_state(...)`, scratch addresses, action-state constants, `record_window`). |
| `instr_writer.py`               | Phase 1 — meta-flush gecko. `install_meta_flush`, `wait_for_meta_flush_alive`, `flush_range`, `write_instrs`, `patch_branch`. |
| `bp.py`                         | Phase 2 — software breakpoints. `set_breakpoint`, `wait_for_hit`, `wait_for_condition`, `read_snapshot`, `write_snapshot`, `continue_`, `step`, `remove_breakpoint`. |
| `candidate_d_standalone_v2.py`  | **The shipped offline JC-shine.** Self-contained C2; paste into a Slippi user dir. **Offline-only — not netplay-safe** (consumer-side hook, no scene gate; see `docs/STATUS.md`). |
| `candidate_d2.py`               | Same logic packaged as a harness-installable gecko (used by `play_d2.py`).                                            |
| `play_d2.py`                    | Live-play driver: boot Dolphin with meta-flush + candidate_d2, you control P1, Fox auto-JC-shines on grab.            |
| `GALE01r2.ini`                  | Vendored from libmelee (`{extra_codes}` substituted empty). Copied into the temp user dir as `GameSettings/GALE01r2.ini`. Replaces Slippi's default codes. |

### Verify scripts (kept, all passing)
| File                            | Validates                                                                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `verify_savestate.py`           | Stage 1: launch + dme hook + autonomous F2 savestate load + MEM1 snapshot/restore + frame counter + entity observation. |
| `verify_inject_gecko.py`        | Stage 2: boot-time gecko-C2 install actually patches the hook (counter advances ~100/s).                   |
| `verify_meta_flush.py`          | Phase 1: dme-installed patch + meta-flush takes effect at runtime (counter advances at boot-time-equivalent rate). |
| `verify_bp.py`                  | Phase 2: BP install → hit → snapshot → continue → remove lifecycle on hook `0x803775B8`.                    |
| `verify_bp_cond.py`             | Phase 2.1: conditional BPs (`wait_for_condition` predicate skips spurious hits).                            |
| `verify_bp_step.py`             | Phase 2.2: single-step. Also documents the "step across an existing gecko hook" hazard.                     |
| `verify_d_standalone_v2.py`     | In-match smoke test: the shipped macro produces canonical JC-shine on a Marth-grabs-Fox savestate trial.    |
| `verify_v2_with_keystone.py`    | Bit-for-bit diff between hand-encoded LOGIC and a keystone-assembled label-only PPC source. Catches hand-counted-offset bugs before launch. |
| `verify_scenario.py`            | Stage 3: full iteration loop. Trigger lands, baseline behavior (no macro) reproduces.                       |
| `verify_d2.py`                  | In-match smoke test: harness-installed `candidate_d2` produces canonical JC-shine across N trials.          |

### Historical scripts (deleted 2026-07-24; recover from git history / the Desktop archive tarball)
The `diag_*` runtime probes (cave dumps, meta-flush verification, codehandler placement), the
one-off `offline_*`/`online_*` experiment scripts, and the entire pre-harness + candidate
iteration history (the old `old&unused/` graveyard, now only in `melee-archive-2026-07-24.tar.gz`).
The load-bearing finding from that era — raw dme writes to instruction memory are NOT observed
by the emulated CPU, which motivated the meta-flush design — is recorded in `docs/REFERENCE.md`.

### Reference docs
| File                                | What it has                                                                                                                                |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `README.md`                         | Top-level entry point for humans + new agents.                                                                                              |
| `CLAUDE.md`                         | Auto-loaded by Claude Code. Architecture + gotchas in one page.                                                                             |
| `WORKFLOW.md`                       | The debugger-driven macro-development workflow (BP + meta-flush usage patterns).                                                            |
| `docs/STATUS.md`                    | **The state board** — what's shipped/pending. Start every session here.                                                                     |
| `docs/REFERENCE.md`                 | Every stable fact (memory map, hooks, injection rules, PPC traps, dme rules) stated once.                                                    |
| `docs/macros/`                      | Per-macro design + open items (jc_shine, lcancel, cactuar_dash, wavedash).                                                                  |
| `docs/archive/`                     | HISTORICAL: session logs, superseded plans/handoffs, old disassembly notes.                                                                 |
| `SSBM memory address sheet/`        | **Authoritative** memory map. CSVs: `Global_Addresses`, `Entity_Data_Offsets`, `Char_Data_Offsets`, `Function_Addresses`, `Free_Memory`, `Action_State_Reference`, `ID_Lists`, etc. Check here first before assuming something is undocumented. |
| `vendor/slippi-ssbm-asm-master/`    | The official Slippi gecko codeset (Bootloader, Online, Recording, Playback, External, Common) — the mod we build on. Convention + address authority (`Common/Common.s`). |
| `vendor/gecko-master/`              | The `gecko` Go tool source. Compiles `.asm` files → `.ini` gecko codes.                                                                    |
| `dme_experiment/`                   | Parallel exploration: reproduce findings via pure-dme writes (no gecko codes). See its `README.md` and `FINDINGS.md`.                       |

## 5. Workflow — iterating on a candidate

```python
from melee_harness import Harness
from scenario import run_grab_trial, classify_trial, WAIT

h = Harness()

# Stage the candidate macro BEFORE launch.
# Slippi's bootloader installs it at boot with proper icache flush.
h.install_gecko_c2(
    name="fox-shine-on-marth-grab-v1",
    hook_addr=0x...,             # pick from `vendor/slippi-ssbm-asm-master/...` taken-hook map
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

For now we hand-emit PPC hex (the harness ships `gecko_c2_lines(...)` which formats the C2 INI bytes). The longer-term path is to author `.s` files in the `vendor/slippi-ssbm-asm-master/` conventions and compile them via the `vendor/gecko-master/` Go tool — that produces the same `.ini` lines, just with macros, includes, and proper symbol resolution.

Key convention notes for hand-rolling:
- Volatile registers per PPC EABI: `r0`, `r3`–`r12`. Safe to clobber as scratch.
- Non-volatile: `r13`–`r31`. Must be saved+restored.
- `r13` is the small-data-area pointer; **never** clobber.
- At a hook, the **displaced original** instruction will execute AFTER your logic — if it clobbers `r0` (e.g. `lbz r0, 2(r25)` at `0x803775C0`), you can use `r0` freely too.
- `finalize_payload(logic, hook, cave, orig)` builds the dme-runtime-inject form. For Path A (boot-time install), call `install_gecko_c2(name, hook, logic, displaced)` directly — it handles the gecko C2 layout.

## 6. Memory map (the load-bearing bits)

All addresses are for **SSBM v1.02 NTSC** (`GALE01.iso`). See the `SSBM memory address sheet/` CSVs for the full map.

### Player data — the required GObj indirection
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

For a complete inventory grep `vendor/slippi-ssbm-asm-master/**/*.asm` for `# Address:` headers.

## 7. Gotchas — hard-won facts to not relearn

1. **dme.hook() requires process name "Dolphin"** — Slippi's binary is "Slippi Dolphin". We launch via a hardlink at `/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Dolphin` to get the right `p_comm`.
2. **`dme.hook()` must run on the main thread.** Wrapping in a daemon thread silently breaks attachment (returns but `is_hooked()` stays False).
3. **Don't pre-open the controller FIFO read ends.** Empirically that breaks dme hooking even though it makes libmelee's `connect()` return instantly. (libmelee era artifact — irrelevant in current architecture but documented in case it crops up.)
4. **Raw dme writes to instruction memory are NOT seen by the emulated CPU**, even with `Core.CPUCore = 0` (pure interpreter). For boot-time install use `Harness.install_gecko_c2(...)`; for runtime install use `instr_writer.write_instrs(...)` (which issues `dcbf`/`icbi` via the meta-flush gecko before returning). `Harness.inject()` is dead code; kept only as documentation of what doesn't work.
5. **Loading a Dolphin savestate corrupts libmelee's Slippi EXI channel** (`EXI SLIPPI: Invalid command byte: 0x3A` → permanent desync). Hence libmelee was dropped entirely.
6. **Slippi's default GALE01 gecko codes panic on savestate load** with `IntCPU: Unknown instruction 00000007 at PC=80c833a4 last_PC=80001f18` (gecko codehandler branches into restored runtime heap). The vendored `GALE01r2.ini` override + `UsePanicHandlers=False` together silence this.
7. **Dolphin Hotkey device must be `Quartz/0/Keyboard & Mouse`** in Hotkeys.ini for synthetic F2 to land. The user fixed this once; it persists in their real `USER_DIR/Config/Hotkeys.ini` and our `shutil.copytree` carries it into each tmp dir.
8. **F2 fires too early to load a savestate** if sent right after `launch()`. We must wait for `POWERON_COUNT (0x804D7420)` to start ticking before sending F2 (Slippi Dolphin doesn't accept hotkeys until Melee is past initial boot).
9. **Player data is at `*(GObj+0x2C)`, NOT directly at `0x80453130`'s pointee.** `0x80453130`'s pointee is a `GObj` struct (`Entity_Data_Offsets.csv`); offsets like `0x10`, `0x04`, `0x65C` are inside Player Data which is one indirection deeper. The harness's `player_data_ptr(port)` helper does the indirection.
10. **When chasing an SSBM fact, always check the `SSBM memory address sheet/` CSVs first** — they are the upstream authority (frame counters, GObj layout, free-memory list); `docs/REFERENCE.md` is the curated project view. (The old `docs/Project_Addresses.md` was deleted 2026-07-24: it omitted the `+0x2C` indirection and carried a wrong scene address.)
11. **macOS-specific:** SIP must be disabled (for `task_for_pid`). Accessibility permission must be granted to the Terminal/Python (for CGEvent F2). `AXIsProcessTrusted()` confirms.
12. **macOS has no `timeout` cmd.** Use Python `_deadline` (SIGALRM-based) for hard wall-clock deadlines. SIGALRM does **not** interrupt blocking FIFO `open()` on the main thread, though, so libmelee's `connect()` couldn't be timed out that way.
13. **Don't pipe Python output through `grep`** during long runs — line buffering hides progress. Use `python3 -u … > logfile` instead.
14. **PPC r0-as-rA trap.** In `addi`, `addis`, `lis`, `stmw`, and any load/store with an `rA` field of 0, the encoded `0` reads as the literal value 0, **NOT** register r0. `addi r0, r0, 16` computes `16`, not `r0 + 16`. The first cut of `instr_writer.META_FLUSH_LOGIC` had exactly this bug; capstone disassembly caught it before launch. Always use r3..r12 (or any non-zero) as the base register in your handlers.
15. **`lmw rD, d(rA)` with rA in [rD..r31] is undefined.** Restore r1 with a separate `lwz r1, ...` after `lmw r2, ...` rather than relying on `lmw r0, ...` to also restore r1. See the restore sequence in `bp.build_bp_handler`.
16. **The meta-flush hook is at `0x803775C0`.** Don't reuse that hook for your own gecko/BP. The per-frame pad-read at `0x803775B8` is the obvious alternative.
17. **Runtime patches don't survive `restore_snapshot()`.** Snapshot is taken before runtime patches exist; the 24 MB write-back wipes them. Either re-install after each restore, or install before `seed_snapshot`. Boot-time geckos survive because they're in MEM1 at snapshot time.
18. **BP spin halts the entire PPC core.** Dolphin's other threads (audio, graphics, window) keep running, so the application stays responsive — but on a Slippi netplay session this would desync. The BP primitive is dev/offline only.

## 8. Limitations / open work

- **Single-savestate scenario only.** The harness seeds from `GALE01.s02`. Multi-state scenarios would need additional savestates and slot routing.
- **`Harness.attach_to_running_dolphin()` doesn't exist.** Every script kills + relaunches Dolphin. With meta-flush in place an agent could iterate ~indefinitely on one Dolphin instance — would shave ~10 s per script run. Not built yet.
- **No watchpoint primitive.** "Which instruction writes address X" requires hooking function entries known to manipulate X and narrowing from there. A search helper that scans the code segment for `stb/sth/stw` with a base register that *could* equal X would be a step up; not built yet.
- **Step-over-existing-hook hazard.** `bp.step()` follows the captured "displaced original" — if that's already a gecko branch (e.g., stepping past `0x803775C0` while meta-flush is installed), step lands in the codehandler cave rather than the vanilla successor. Documented in `verify_bp_step.py`. A smarter step that detects gecko branches and steps to the cave's return point is future work.
- **No live multi-macro swap.** Boot-time geckos are baked into `GameSettings/GALE01r2.ini`; to test a different macro you kill + relaunch. The meta-flush path *technically* lets you swap a gecko at runtime, but the harness doesn't expose that pattern yet.
- **Final gecko-code shipping** still benefits from `powerpc-eabi-as` for proper assembly compilation via `vendor/gecko-master/` (Go source only — the Windows `.exe` assemblers were deleted 2026-07-24; macOS users need devkitPPC's `powerpc-eabi-as` for that path). The current loop bypasses this by emitting raw PPC hex and verifying with `keystone-engine` (see `verify_v2_with_keystone.py`).

### Things that ARE solved (and didn't used to be)

These were in the "open" list in earlier session logs but no longer apply:

- ~~"Fox's shine action state ID is not yet known."~~ Identified: `0x0168` ground startup, `0x0169` ground loop, `0x016D` aerial startup, `0x016E` aerial loop, `0x0170` aerial fall. Live in `scenario.py`. See `docs/archive/sessions/2026-05-15.md`.
- ~~"The harness restarts Dolphin per candidate."~~ Still true per *script*, but within a session the meta-flush path lets you swap PPC bytes at runtime without reboot.
- ~~"Trigger via action-state poke is an abstraction leak."~~ Still true for some macros, but the BP primitive (`bp.py`) gives an alternate path: hook a function entry that runs when the input would be processed, snapshot, edit registers, continue.

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
6. Savestates present in
     ~/Library/Application Support/com.project-slippi.dolphin/netplay/User/StateSaves/
   slot 2 (GALE01.s02): the offline Marth-vs-Fox scenario;
   slot 4 (GALE01.s04): online-entry state with meta-flush baked (for online
   work, see WORKFLOW.md). NOTE: savestates are version-locked — every Slippi
   update invalidates them (and wipes the step-3 hardlink); redo both after updates.
7. Python deps: `dolphin-memory-engine`, `pyobjc` (Quartz/AppKit), `capstone`,
   `keystone-engine` (keystone may need `DYLD_LIBRARY_PATH=/opt/homebrew/lib`).
   libmelee is *not* required at runtime.
```

Then run the verify suite (canonical list + runtimes in `WORKFLOW.md`):

```
$ python3 verify_savestate.py       # ~11 s, should print [PASS]
$ python3 verify_inject_gecko.py    # ~25 s, should print [PASS]
$ python3 verify_scenario.py        # ~12 s, should print [PASS]
```

If all three pass, the harness is ready to iterate on candidate macros.
