# New Macro — Handoff / Dev Jumpstart

**The generic starting point for building ANY new macro in this repo.** Read this
once, top to bottom, before writing code. It distills how the project actually
works and how the **offline** and **online** dev/test cycles run — the hard-won
parts that aren't obvious from the source. The L-cancel macro is the worked
example throughout; its own handoff (`L_CANCEL_HANDOFF.md`) is the case study.
(Written 2026-05-22 after shipping the online auto-L-cancel.)

---

## 1. YOUR MACRO — fill this in first
Before anything, pin these down (they decide which dev cycle + hook you use):
- **Goal:** what input(s) to inject, on what trigger, for which character/port.
- **Online or offline?** Offline-only is much easier (savestate loop, breakpoints).
  Online (netplay) has extra rules — see §7. If it must work in real netplay, it's
  online.
- **Inputs needed:** digital buttons only? sticks / analog triggers? (decides the
  hook — §4.)
- **Observable:** the game-state field that tells you it worked (e.g. the L-cancel
  used `LCancelStatus` `0x25FF`). Find yours in the address sheet (§9) first.

Write the answers down. Everything below branches on them.

---

## 2. Read these first (in order)
1. **`CLAUDE.md`** — architecture + the memory-map / injection / input gotchas. The
   single most important file.
2. **This file.**
3. **`WORKFLOW.md`** — using the debugger to discover something new (addresses,
   behavior). `HARNESS.md` §9 if the harness isn't set up on this machine yet.
4. **If your macro runs ONLINE:** `docs/ONLINE_MACRO_GUIDE.md` +
   `docs/ONLINE_REFERENCE.md` (cover-to-cover) — netplay has different rules and the
   reference has every address/offset/struct you'll need.
5. **`docs/L_CANCEL_HANDOFF.md`** — a complete worked example (offline → online,
   digital → analog) to copy the patterns from.
6. **`SSBM memory address sheet/*.csv`** — AUTHORITATIVE. Search here FIRST for any
   address, offset, action-state ID, or free-memory region. `docs/Project_Addresses.md`
   is a curated subset, not the source of truth.

---

## 3. How the project works (one screen)
- **All `dme`, no libmelee.** `dolphin-memory-engine` does observation + writes;
  libmelee was dropped (loading a savestate corrupts its Slippi EXI channel →
  permanent desync). The `Harness` class (`melee_harness.py`) is the entry point —
  read its module docstring + the class first.
- **Three ways to get your PPC code into the game** (full detail in CLAUDE.md):
  1. **Boot-time gecko** (`Harness.install_gecko_c2`, must be called *before*
     `launch()`): Slippi's bootloader copies C2 bodies into a code cave at boot.
     This is how a finished macro ships **offline**.
  2. **Runtime `dme` + meta-flush** (`instr_writer.write_instrs` / `patch_branch`):
     install ONE meta-flush gecko at boot, then `dme`-write fresh PPC anywhere in
     MEM1 and have it take effect in ~1 frame (the gecko does the `dcbf`/`icbi`/
     `isync`). **This is the iteration workhorse** — change code without rebooting.
  3. **Raw `dme` write to instruction memory** — *non-functional* on Slippi Dolphin
     (the emulated CPU never sees it without an explicit cache flush). Don't.
- **Software breakpoints** (`bp.py`): overwrite an instruction with a branch to a
  handler that snapshots registers and spins; `dme` reads/edits and continues.
  **Offline/dev only — freezes the game, NOT netplay-safe.** Great for discovery.
- **Reset model:** there is no savestate API. The user (or synthetic F2) loads
  **slot 2** once; `seed_snapshot()` snapshots all of MEM1; `restore_snapshot()`
  writes it back to revert. **Runtime patches are wiped by `seed_snapshot()` /
  savestate loads** — install them AFTER seeding.

---

## 4. Where to inject inputs (pick by environment + input type)
The golden rule: **modify the controller pad data, not player-data/action-state
fields.** Direct writes to game state desync online; only pad edits propagate
cleanly. (`scenario.force_action_state` animates a state but applies **no physics**
— fine for self-contained triggers, useless for real motion. And `dme` writes to
the processed controller region `0x804C1FAC` lose the race with Dolphin's input
pipeline — don't.)

| Environment | Input type | Hook | How |
| --- | --- | --- | --- |
| **OFFLINE** | anything | `0x803775B8` (`HSD_PadRead`, consumer) | `r24`=0-idx port, `r25`=pad struct. OR buttons into `0(r25)`, `stb` analog/stick into `2..7(r25)`. Displaced `lhz r0,0(r25)` = `0xA0190000`. |
| **ONLINE** | digital buttons | `0x8034E2AC` (producer, in `PAD_Read`) | `oris r0,r0,BIT` sets a button bit. Displaced `rlwinm` = `0x540084BE`. |
| **ONLINE** | analog trigger / stick | `0x8034E680` (producer, after calibration) | `stb val,6(r4)`=analog L, `7`=R, `2..5`=stick/c-stick. Displaced `lbz r0,7(r3)` = `0x88030007`. |

- **Pad struct byte layout** (same offsets at `r25` offline and `r4` online):
  `0`=u16 buttons (A=0x100 B=0x200 X=0x400 Y=0x800 Start=0x1000 Z=0x10 R=0x20 L=0x40,
  D-pad U/D/R/L=0x8/0x4/0x2/0x1), `2`=stickX `3`=stickY `4`=cStickX `5`=cStickY
  `6`=analog L `7`=analog R `8`=analogA `9`=analogB (signed bytes).
- **ONLINE = producer-side only.** Editing inputs *after* Slippi serializes them
  (consumer side, e.g. `0x803775B8`) **DESYNCS**. The producer points above are
  upstream of `TriggerSendInput`'s EXI scrape (`0x80376A28`), so the peer receives
  your edit and both clients simulate identically. (See `ONLINE_REFERENCE.md` →
  "PAD_Read internals" for the full map + why `0x8034E680` is the analog point.)
- **The game has NO input buffer:** a held button registers as ONE press. To act on
  multiple frames you must **PULSE** — release between presses (gate on a frame
  parity / counter). Holding = one rising edge. (True for analog triggers too.)

---

## 5. Gate on the right player (and don't crash on garbage)
- **Player Data ptr:** `pdata = *( *(0x80453130 + port*0xE90) + 0x2C )`. Action state
  = `*(pdata + 0x10) & 0xFFFF`. Use `Harness.player_data_ptr(port)` from Python; in
  a cave, do the double-indirection by hand.
- **ONLINE local player** (host can be P1 or P2): `port = *( *(r13 - 0x49E4) + 0 )`
  (ODB_LOCAL_PLAYER_INDEX, offset **+0**, NOT +2). Resolve this in the cave so the
  macro works as either port.
- **MEM1-check every pointer before dereferencing** (`srwi tmp,ptr,24; cmplwi
  tmp,0x80; bne bail`). During scene transitions / rollback you'll read garbage; an
  unchecked deref crashes Dolphin.
- **PPC `r0`-as-`rA` trap:** in `addi/addis/lis/load/store`, an `rA` field of 0 means
  literal 0, not register r0. Use r3..r12 (or r5..r9) as base regs, never r0.

---

## 6. The OFFLINE dev cycle (do this first when possible)
Most mechanics (the trigger, the action, the observable) reproduce offline — only
the 2-frame netplay delay and netplay-safety are online-only. Develop here; it's
faster and you can use breakpoints.

```python
kill_stale_dolphins()                 # pkill -9 -x Dolphin; poll pgrep until empty
h = Harness()
iw.install_meta_flush(h)              # BEFORE launch (boot gecko)
h.launch(); h.hook_dme(); h._wait_for_cpu_alive(timeout_s=60)
iw.wait_for_meta_flush_alive(h, timeout_s=30)
h.seed_snapshot(timeout_s=60)        # loads slot 2 + snapshots MEM1 (wipes prior patches)
pd = h.player_data_ptr(TARGET_PORT)
# init any scratch flags, THEN install the cave (must be AFTER seed_snapshot):
payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)   # [logic][displaced][branch]
iw.write_instrs(h, CAVE, payload); iw.patch_branch(h, HOOK, CAVE)
# observe via h.read_word / read_bytes; iterate by rewriting the cave in-game.
```
- **Self-drive scaffold** to exercise a character without a human: at the PadRead
  hook, read the action state and inject inputs to cycle it (e.g. Wait `0x0E`→X,
  KneeBend `0x18`→X for a full hop, JumpF/Fall `0x19..0x22`→A for a nair). The
  `*_selfdrive` / `auto_lcancel/lcancel_rig.py` scripts are templates.
- **Iterate by in-game cycling, not reloading** slot 2 (a reload wipes the cave).
  Re-`write_instrs` to change logic; the meta-flush makes it live in ~1 frame.
- **A/B without recompiling:** toggle a single **code** instruction (e.g.
  `oris`↔`nop`, `stb`↔`nop`) via `write_instrs` to compare on/off in one session.
- **Caves:** small caves can sit at `DEFAULT_CAVE` (`0x803FA3E8`, but the meta-flush
  body lives in its first ~120 bytes — start at `+0x200`). Anything bigger or shared
  with online: use `0x803FA600`. **Never overlap the control plane
  `0x803FA440-0x803FA44C`** (flush_range writes there → corrupts the cave → crash).

---

## 7. The ONLINE (netplay) dev cycle
Only if your macro must run in real netplay. **Needs the user** for two things, so
ask up front:
1. Their **second machine in an ACTIVE in-game match** (a stage, playing) — not
   just connected. If it's only at the online CSS you'll land at scene `0x0008`
   instead of in-game `0x0208` and the run aborts.
2. The **slot-4 savestate baked with meta-flush** (one-time, `ONLINE_MACRO_GUIDE.md`
   §3): the harness enters online by F4-loading slot 4, which **wipes boot geckos**,
   so meta-flush (and ultimately your finished macro) must be *baked into the
   savestate* to survive.

```python
kill_stale_dolphins()
h = Harness()                         # do NOT install_meta_flush — it's in the SS
h.launch(); h.hook_dme(); h._wait_for_cpu_alive(60)
mh._focus_pid(pid); mh._send_key(118); time.sleep(3)    # F4 (load slot 4)
mh._focus_pid(pid); mh._send_key(36);  time.sleep(15)   # Enter (search/connect)
# confirm in-game by MAJORITY vote (reads tear during rollback):
#   scene = ((w<<8)|(w>>24)) & 0xFFFF on 0x80479D30 ; want 0x0208 (0x0008 = CSS)
# confirm meta-flush present: read 0x803775C0, expect a branch (0x48xxxxxx)
iw.write_instrs(h, 0x803FA600, payload); iw.patch_branch(h, PRODUCER_HOOK, 0x803FA600)
# observe with THROTTLED, detach-tolerant reads (sleep ~0.012; re-hook on failure)
```
Hard rules (each cost the last agent real time):
- **One `dme` process, start to finish.** Re-attaching `dme` in a *new* process
  gives torn garbage. Do launch + entry + patch + observe in one script.
- **Throttle + majority-vote every read.** Rollback rewrites MEM1; a single read can
  be torn. Re-`dme.hook()` on read/write failure (heavy polling detaches it).
- **Producer-side edits only** (§4), **pulse not hold** (§4), **toggle a CODE
  instruction, not a data flag** (data in `0x803FAxxx` isn't reliably preserved
  across rollback; code is).
- **Cadence keyed to the GLOBAL frame counter** (`0x80479D60`) if you need a pulse
  rhythm — it keeps ticking through hitlag. The action-state frame counter `0x894`
  FREEZES during hitlag. (This bit the L-cancel; see its handoff.)
- **The user's screen is the only ground truth for desync.** Your side can look fine
  and still be desynced (producer-side edits *shouldn't* desync — but confirm). Ask
  after every run.
- **Develop the logic offline first** (§6); only bring the netplay-specific parts
  online.

---

## 8. Build + validate discipline (non-negotiable)
- **Assemble with keystone, then capstone-verify the words before flushing.**
  Hand/auto-encoded branch offsets are the #1 silent-failure source. Disassemble the
  full payload and eyeball that branches land in-cave and the displaced original is
  present.
- **`finalize_payload(logic, hook, cave, displaced)`** appends `[displaced][real
  branch]` for the dme path. **`gecko_c2_lines(hook, logic, displaced, name)`** packs
  a C2 gecko — and the **C2 codehandler OVERWRITES the body's last word** with its
  branch-back, so `gecko_c2_lines` reserves a throwaway `0x00000000` slot. Never
  hand-roll a C2 that ends on a needed instruction. Verify a C2 cave with
  `verify_codehandler_displaced.py`.
- **Preserve the right registers per hook.** At `0x8034E2AC`: r0,r4,r5,r13. At
  `0x8034E680`: r3 (calib ptr),r4,r13. Save/restore (stack frame) everything else
  you touch; the displaced original reloads its own dest.
- **Floats:** several useful fields are floats (`0x894` action frame, `0x195C`
  hitlag). No FPU precedent in repo caves — decode an integer-valued float with
  integer ops: `n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exponent)`.
- **Observable choice:** prefer a direct game-state flag (like `LCancelStatus
  0x25FF`) over a derived measurement; **trust empirical reads over CSV descriptions**
  for any field you can watch (some CSV labels are wrong/misleading for this build).

---

## 9. Settled gotchas — DON'T rediscover these
- `0x80453130` is the P1 **GObj** ptr; real Player Data is `*(GObj+0x2C)`. Stride
  `0xE90` per port.
- Two frame counters: `0x80479D60` (primary, resets per scene) and `0x804D7420`
  (power-on, never resets). Scene id at `0x80479D30` via `getMinorMajor`.
- ONLINE consumer hook `0x803775B8` DESYNCS; producer hooks `0x8034E2AC` (digital) /
  `0x8034E680` (analog) are safe. `0x803775C0` is the meta-flush hook (taken).
- No input buffer → PULSE. Held analog also doesn't repeat-trigger (needs the edge).
- Airdodge/grab misfires come from **digital** L/R/Z rising edges in airborne states;
  a light **analog** trigger (`< 0xAA`, the digital-conversion threshold at
  `0x8034E244`) injects no digital bit → can't airdodge. (Why the L-cancel switched
  to analog L.)
- `pkill -x Dolphin` returns before the process dies; use `-9` and poll `pgrep`.
- macOS: SIP must be disabled (for `task_for_pid`), Accessibility granted (synthetic
  keys), and a hardlink named `Dolphin` next to `Slippi Dolphin` (recreated if the
  app updates). Keys: F2=120, F4=118, Enter=36. keystone needs
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib`.

---

## 10. Ship it
- **Offline macro:** package the cave as a boot C2 (`gecko_c2_lines`) and either
  install via `Harness.install_gecko_c2` (harness) or paste into the user's Slippi
  user dir (standalone). Provide a `verify_*.py` that prints `[PASS]`/`[FAIL]`.
- **Online macro:** generate the gecko (`gecko_c2_lines`), have the user add it in
  Slippi Manager and **re-bake slot 4** (enter a match normally, save state to slot
  4) so it survives F4 entry. See the L-cancel gecko header for the exact steps.
- Leave a short header in the gecko file (what/why/validated) and a regenerator
  script, like `make_online_analog_lcancel_gecko.py`.

---

## 11. Files / tools to reuse
| File | What |
| --- | --- |
| `melee_harness.py` | `Harness`, launch/hook/seed/snapshot, `gecko_c2_lines`, `finalize_payload`, `_send_key`/`_focus_pid`. |
| `instr_writer.py` | meta-flush + `install_meta_flush` / `write_instrs` / `patch_branch` / `flush_range` / `wait_for_meta_flush_alive`. The iteration primitive. |
| `bp.py` | software breakpoints (offline discovery). |
| `scenario.py` | action-state constants + `force_action_state` (animation only). |
| `disasm_*.py` | read-only disassembly probes — copy one to map any code region (`disasm_lcancel_analog.py` is the most complete example). |
| `*_selfdrive.py`, `auto_lcancel/lcancel_rig.py` | self-drive + A/B test rig templates (offline and online). |
| `make_online_analog_lcancel_gecko.py` | shippable-gecko generator template (build + capstone-verify + print). |
| `verify_codehandler_displaced.py` | proves a C2 cave kept its displaced word. |
| `docs/ONLINE_MACRO_GUIDE.md` / `ONLINE_REFERENCE.md` | online rules + every address/offset/struct. |
| `docs/L_CANCEL_HANDOFF.md` | full worked example. |
| `SSBM memory address sheet/*.csv` | authoritative address/offset/state sheet. |
