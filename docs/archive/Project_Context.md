> **HISTORICAL (archived 2026-07-24).** Pre-harness exploration history. Current architecture: HARNESS.md; current state: docs/STATUS.md.

# SSBM Frame-1 Macro Project: Context & Progress Summary

> **Note:** This document captures the **pre-harness exploration** and the failed approaches that led to the current architecture. For the actual implementation and how to use the harness, see **[`HARNESS.md`](./HARNESS.md)**. The "Next Step: The SIP-Disabled Plan" at the bottom of this doc has since been executed and superseded — SIP is disabled, `dolphin-memory-engine` works, but the live-Python-loop architecture envisioned there turned out to be infeasible for code injection (Dolphin's CPU emulator doesn't observe dme writes to instruction memory). We instead inject via Slippi's bootloader at boot time and use dme for data only.

## Objective
Create a macro that allows Fox (Port 2) to react to Marth's (Port 1) grab with a Frame-1 input (originally Spot Dodge, now testing Jump/Shine). The macro must execute on the exact frame the grab is initiated to jump-cancel the shine properly. It needs to eventually be Netplay-safe. We are currently working on getting it to work offline, then we will work on getting it to work with slippi netplay. Once that occurs, the local player will be the spot dodging fox, and the online player will be marth. However we are making the code to work independent of the character of the online player.  

**Why the Frame-1 Requirement?**
To successfully execute a jump-cancelled shine in response to a grab, the timing is extremely strict. On Frame 1, Marth initiates the grab. We must input 'Jump' on this *exact same frame*. Factoring in the 2-frame built-in online delay, our first jumpsquat frame appears on Frame 4. By Frame 7, Fox is airborne and can finally input the 'Shine' (Down + B). Because of this tight sequence, the macro cannot afford even a single frame of delay; it must react instantly. (Marth's grab comes out frame 7))

---

## 1. What We've Tried & What Hasn't Worked

### Approach A: Gecko Codes (PowerPC Assembly Injection)
We attempted several Gecko code injections to spoof Port 2's inputs when Port 1 initiates a grab.
*   **C0 Code (Global Frame Hook):** Failed. `C0` executes at the start/end of the global frame boundary. By the time we detect Marth's action state and spoof the inputs, Fox's inputs have already been processed for that frame, resulting in a Frame-2 reaction (1 frame too late).
*   **C2 Code at `0x8006AD10` (Main Character Loop):** Failed. Characters update sequentially, but inputs are gathered *before* the action states are assigned. Still 1 frame too late.
*   **C2 Code at `0x803775C0` (`HSD_PadRead` - Hardware Controller Polling):** 
    *   *Attempt 1 (Global Floats):* Overwriting global float arrays (`0x804C1FAC`) corrupted the horizontal stick X-axis because the pointer offset calculations inside the engine were disrupted.
    *   *Attempt 2 (Raw Hardware Buffer):* Hooked into the raw `PADStatus` buffer to write raw hex values (e.g., `-127` for Y-axis) before the engine parses them into floats. Fixed the joystick corruption, but the spot dodge still failed to trigger properly. We hypothesized testing a simpler input (Jump -> Y button `0x0800`).

### Approach B: Direct Memory Access (Python)
We attempted to use Python to act as an external debugger to read/write memory live.
*   **`dolphin-memory-engine`:** Failed. macOS System Integrity Protection (SIP) hard-blocks `task_for_pid` system calls, completely preventing Python from reading Dolphin's memory, even with `sudo` and custom code-signing entitlements (`get-task-allow`, `debugger`).
*   **Dolphin Named Pipes (`GCPadNew.ini`):** Attempted to pipe inputs into Dolphin via an OS-level FIFO pipe (`bot_input`). Failed due to macOS file descriptor/blocking errors.

---

## 2. What Actually Works

*   **Dolphin MemoryWatcher:** We successfully bypassed SIP's read restriction by using Dolphin's native `MemoryWatcher` feature. By writing addresses to `~/Library/Application Support/Dolphin/MemoryWatcher/Locations.txt`, Dolphin pushes memory values (like Frame Count, Action States, and Controller Structs) out to a Unix Domain Socket.
    *   *Status:* Highly effective for debugging and live-reading, but it is **read-only**. We cannot write spoofed inputs back through this socket.
*   **Data Parsing:** We successfully parsed the SSBM Memory Address spreadsheet and understand the memory layout for Player Entity structures, Action States (`0x10`), and Controller Arrays (`0x804C1FAC`).

---

## 3. Directory Breakdown (Files & Tools)

### Documentation & References
*   **`Gecko_Code_Analysis.md`:** Disassembly and analysis of existing Netplay-safe codes (Swap X/Z, UnclePunch Short Hop, Flash Red). Details how to use Slippi's global pointers to identify online vs. offline states.
*   **`Project_Addresses.md`:** A quick-reference sheet of the crucial memory addresses (P1/P2 Entity Pointers, Action State offsets, Controller addresses).
*   **`Spot_Dodge_Macro.md`:** Contains the iterations of our C0 and C2 Gecko codes, complete with PowerPC assembly source and comments.
*   **`SSBM memory address sheet/` (Directory):** Contains all the tabs of the Master SSBM Memory Spreadsheet extracted as highly searchable `.csv` files.

### Python Scripts
*   **`extract_sheets.py`:** Extracted the `.xlsx` memory sheet into individual `.csv` files.
*   **`disas*.py` / `read_8006ad10.py`:** Uses the `capstone` library to disassemble raw PowerPC hex into readable assembly.
*   **`manual_asm*.py` / `manual_pad.py` / `manual_jump.py`:** Custom Python scripts used to compile our assembly instructions into Hex and calculate the correct branch offsets for our `C2` Gecko codes.
*   **`memory_bridge.py`:** An HTTP server designed to wrap `dolphin-memory-engine` (failed due to SIP).
*   **`test_mw.py` / `memory_watcher.py` / `dump_pad.py`:** Scripts that successfully connect to Dolphin's Unix Domain Socket to live-stream memory values directly from the emulator.

### Libraries & Tools Used
*   `capstone`: For disassembling PowerPC code.
*   `dolphin-memory-engine`: Python API for interacting with Dolphin's memory (currently blocked by SIP).
*   `grep`, `awk`, `cat`: For searching the codebase and CSVs.

---

## 4. Next Step: The SIP-Disabled Plan

You are rebooting into macOS Recovery Mode to completely disable System Integrity Protection (SIP).

Once SIP is disabled:
1.  **Direct Read/Write Access:** The `dolphin-memory-engine` Python library will function perfectly. We will have unrestricted, live, 60-FPS read and write access to the emulator's memory space.
2.  **The New Macro Architecture:** We no longer need fragile assembly injections or Gecko codes. We can write a clean, robust Python loop that:
    *   Polls Marth's Controller Input array (or Action State) every frame.
    *   If a grab input/state is detected, Python instantly overwrites Fox's Controller Input memory buffer to trigger the Jump/Shine sequence before the game engine processes the frame.
    *   This allows us to handle the complex frame timings (Jump -> Wait 3 frames -> Shine) programmatically without wrestling with PowerPC registers.