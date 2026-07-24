# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`slippi-ssbm-asm` is the upstream Project Slippi repo of PowerPC ASM injections that get compiled into Gecko code packs for Super Smash Bros. Melee NTSC 1.02 (GALE01) and NTSC-J (GALJ01). It produces three categories of output: Dolphin netplay/playback `.ini` files, console `.gct`/`.bin` files for Nintendont, and a bootloader `.gct`.

The parent directory (`/Users/andrewashman/Desktop/melee/`) treats this folder as **vendored reference material** — its `CLAUDE.md` says "Do not modify" `slippi-ssbm-asm-master/`. Default to read-only use unless the user explicitly asks for changes here.

## Build

There is no test suite. CI just runs `make ini list` (see `.github/workflows/build_check.yml`).

Prerequisite: the [`gecko`](https://github.com/JLaferri/gecko/releases) binary must be on `PATH`. On Linux/macOS, build it from source with `go build`.

```bash
make            # build all .ini files (Netplay, Playback, Online) + console targets
make ini        # just the .ini targets
make list       # write per-config injection lists into Output/InjectionLists/
make clean      # remove generated .ini files

# Without local toolchain:
docker run --volume=${PWD}:/work --workdir=/work nikhilnarayana/devkitpro-slippi make

# Platform-specific entry points (equivalent to make all, used historically):
./build.sh                    # macOS/Linux
build.bat                     # Windows
./build-netplay.sh            # just the Netplay .ini
./build-playback.sh           # just the Playback .ini
```

`gecko build -defsym STG_EXIIndex=N` selects EXI slot at build time: `1` = SlotB (production), `0` = SlotA (debug). The Makefile passes `1` for netplay/playback and produces both port variants for console (`g_core.bin` for port B, `g_core_porta.bin` for port A).

## Architecture

### Build pipeline

1. Top-level **`*.json` files** are gecko build configs. Each one declares output file(s) and a list of `codes`, where each code has a `build` array pulling in `binary` files (prebuilt `.bin` from `Binary/` or `External/`) and `injectFolder` / `inject` entries pointing at folders/files of `.asm` source.
2. The `gecko` tool assembles every `.asm`, concatenates with raw `.bin` blobs, and emits either a Dolphin `[Gecko]`-section `.ini` (with the `outputFiles[].header` lines prepended) or a `.gct` / `.bin` blob.
3. Each `.asm` injection starts with a comment `# Address: 0xXXXXXXXX` — that's the PPC instruction the gecko C2 code patches over. The assembler reads it from the comment.

The four "shape" configs are `bootloader.json`, `console_core.json`, `netplay.json`, `playback.json`. The many `console_*.json` files are individual optional code packs for Nintendont users.

### Source layout

- **`Common/Common.s`** — the central include. *Read this first when navigating any `.asm` file.* Defines:
  - Stack/calling-convention macros: `backup` / `restore` (paired — saves LR + non-volatiles + reserves stack scratch), `backupall` / `restoreall`, `branchl` (call), `branch` (tail call), `load` / `loadwz` / `loadbz` (32-bit immediate load).
  - Game-state helpers: `getMinorMajor`, `getMajorId`, `loadGlobalFrame`, `bp` (breakpoint), `logf` (Slippi EXI log), `oslogf` (OSReport log).
  - Hundreds of `.set` constants for NTSC 1.02 function addresses (`HSD_*`, `GObj_*`, `JObj_*`, `Text_*`, `EXI*`, `Camera_*`, `PlayerBlock_*`, `OSReport`, `memcpy`, …), Slippi EXI command IDs (`CONST_SlippiCmd*`), scene IDs (`SCENE_VERSUS_IN_GAME` = `0x0202`, etc.), and r13-relative offsets (`primaryDataBuffer`, `bufferOffset`, `frameIndex`, `OFST_R13_SB_ADDR`, …).
  - Locally-injected static functions live at fixed code-cave addresses (`FN_EXITransferBuffer`, `FN_GetIsFollower`, `FN_ProcessGecko`, `FN_CaptureSavestate`, `FN_LoadSavestate`, …) — these are defined in `Common/`, `Online/Static/`, and the bootloader, then referenced symbolically everywhere else.
  - Header-guarded with `.ifndef HEADER_COMMON` / `.set HEADER_COMMON, 1` / `.endif`. Same idiom in `Online.s`, `Recording.s`, `Playback.s` — don't break the guard or you'll get duplicate-symbol errors.

- **Module headers** (`Online/Online.s`, `Recording/Recording.s`, `Playback/Playback.s`) — module-local constants and r13 offsets. ASM files in those modules `.include` both `Common/Common.s` *and* their module header.

- **`Bootloader/`** — early boot, runs before everything else. Hooks `0x803444e0`, requests the codeset over EXI from Slippi.exe, allocates heap space, processes Gecko codes via `FN_ProcessGecko` (defined in `Common/Gecko/ProcessCodeList.asm`). `EXISpoof.asm` neutralizes the GameCube EXI sanity check at `0x80346314`.

- **`Common/`** — shared injections used by multiple codesets (recording, online, playback all pull from here). Includes `EXITransferBuffer/` (the EXI bridge to Slippi.exe), `Gecko/` (runtime code-list processor), `IncrementFrameIndex.asm`, `GetIsFollower.asm`, `GetCommonMinorID/`, `Initialize Player Data/`, `Initialize Stage Data/`, `FastForward/`, `Preload Stadium Transformations/`, etc.

- **`Online/`** — rollback netplay implementation. `Core/` has the rollback engine hooks (`StartEngineLoop`, `LoopEngineForRollback`, `ForceEngineOnRollback`, `SkipNewInputFetchOnRollback`, `TriggerSendInput`, `LGLExceededGameEnd`, etc.). `Static/` defines fixed-address helpers like `LoadMatchState`, `SaveState`, `LoadState`. `Menus/` has the CSS/SSS/in-game/results-screen UI patches. `Slippi Online Scene/` has `boot.asm` and `main.asm` for the new online scene.

- **`Recording/`** — replay capture. Hooks pre/post-frame and game-start/end to stream events over EXI to Slippi.exe (which writes the `.slp` file). All commands use the `CONST_SlippiCmd*` IDs from `Common.s`.

- **`Playback/`** — replay playback engine.

- **`Debugging/`** — `AdditionalCrashInfo/`, `CreateFrequentAlarm.asm`. Optional debug aids.

- **`External/`** — large folder of vendored individual community codes (`UCF 0.84/`, `Boot to CSS/`, `Widescreen/`, `Lag Reduction/`, `Stage Striking/`, `PortPriority/`, `FlashRedFailedLCancel/`, etc.). These come from other authors; don't modify. Pulled in by individual `console_*.json` configs.

- **`Binary/`** — prebuilt `.bin` blobs (e.g. `FasterMeleeSettings/`, `LagReduction/`, `m-ex.bin`). Concatenated as-is into outputs.

- **`Output/`** — generated artifacts. `Output/Netplay/GALE01r2.ini` and `Output/Playback/GALE01r2.ini` are the files Slippi Dolphin actually consumes (drop into Dolphin's `Sys/GameSettings/`). `Output/Console/g_*.bin` and `g_*.txt` go into a Nintendont SD setup. `Output/Bootloader/bootloader.gct` is the bootloader blob. `Output/InjectionLists/` is filled by `make list`.

### Conventions when writing or modifying `.asm`

- Every `.asm` file is **one C2 injection at one address**. The address goes in a leading `# Address: 0xXXXXXXXX` comment — the `gecko` tool parses it.
- Always `.include "Common/Common.s"` (and the module header if applicable). Don't redefine constants from `Common.s`; add new ones to `Common.s` or the module header.
- Pair `backup` with `restore` and remember the original instruction at the hooked address must be re-executed — typically by the original line being copied into the injection (see how `Bootloader/main.asm` opens with `branchl r12,0x803444e0`, the very call it replaced).
- Hex literals everywhere (`0x...`). PPC instructions are big-endian.
- Use the existing helper macros (`branchl`, `load`, `getMinorMajor`, `loadGlobalFrame`) instead of open-coding `lis`/`ori` pairs.
- `STG_EXIIndex` is **set at build time** via `-defsym`, not in source.

### Macro pitfalls in `Common/Common.s`

- `backup` reserves `BKP_DEFAULT_FREE_SPACE_SIZE` (0xA8) bytes of stack scratch by default and saves r19–r31 (`BKP_DEFAULT_REG=12` non-volatiles). Pass non-default `free_space`, `num_freg`, `num_reg` if you need more — `restore` must be called with **the same arguments** or stack will desync.
- `branchl` clobbers the register passed as `reg` (and ctr, lr). Conventionally `r12`.
- `loadGlobalFrame reg` loads from `0x80479D60` (primary frame counter, resets between scenes). Use `0x804D7420` directly if you need a counter that survives scene changes.
