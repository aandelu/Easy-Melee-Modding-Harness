> **HISTORICAL (archived 2026-07-24).** Dissolved into docs/REFERENCE.md (facts) + WORKFLOW.md (process). Kept for the L-cancel case study.

# Online (Netplay) Macro Guide

How to build and test a **netplay-safe** Melee macro that runs in a live Slippi
online match without desyncing — using this repo's dme/meta-flush harness.

> **Audience:** an agent (or human) picking this up cold. Read this top-to-bottom
> once. The companion file [`ONLINE_REFERENCE.md`](ONLINE_REFERENCE.md) is the
> address/offset/script cheat sheet — keep it open while you work.

Everything here was validated 2026-05-21 against a live online match vs the
user's second Slippi account. Where a claim was proven by a specific script,
the script is named.

---

## 0. Current status (what works, what doesn't)

**PROVEN online, no desync, reproducible:**
- Producer-side **input injection** at `0x8034E2AC` (inside `PAD_Read`) works
  online. Setting a button bit there lands in the transmitted input. (`online_orL_test.py` — the local character shielded when we OR'd the L bit.)
- The full **savestate → meta-flush → dme code-patch** pipeline works online and
  patches **persist across rollbacks**. (`online_codepatch_persist.py`.)
- A **self-driving cave** (jump → nair → land loop) runs online without crashing.
  (`online_lcancel_selfdrive.py`.)
- **The L-cancel works online and is shipped (v4 = pulsed ANALOG L).** During the
  local player's aerials it pulses a light analog L (`0x80`, every other frame)
  injected producer-side at `0x8034E680`. Cuts Fox NAIR landing **14.8f → 7.1f**,
  LCancelStatus 15/15, **plus 10/10 on aerials that CONNECT (hitlag)**, no desync,
  no grab/airdodge misfires, dynamic P1/P2 local-port. Deployable gecko:
  [`online_auto_lcancel.gecko.txt`](../online_auto_lcancel.gecko.txt). See [§9](#9-case-study-the-l-cancel-solved).
- (Historical: a pulsed-**Z** version at `0x8034E2AC` worked but missed on hitlag and
  could airdodge/re-nair on air-ending aerials; analog L fixed both — see §9.)

**The whole pipeline is done and a complete, misfire-free L-cancel is shipped** —
netplay-safe code injection + input editing online, end-to-end, including hitlag and
air-ending aerials.

---

## 1. Mental model: why online is different from offline

Offline, the harness loads a savestate, you patch MEM1, you single-step frames.
Online you can do **none** of that safely. Three things drive everything:

1. **Slippi is deterministic lockstep with rollback.** Both clients simulate the
   same inputs. Anything that makes your client's simulation diverge from the
   peer's = **desync**. The only safe way to change inputs is **producer-side**:
   edit your *local* controller input *before* Slippi serializes and transmits
   it. Then the peer receives your edited input and both simulate identically.
   Editing inputs *after* Slippi has materialized them (consumer-side) desyncs.

2. **The online input pipeline (NTSC 1.02):**
   ```
   SI hardware
     → PAD_Read (0x8034DA00 region)        ← raw local controller read.
        └ 0x8034E2AC converts raw SI → PADStatus[r4+0] (16-bit buttons).
          *** THIS is the producer-side injection point (Altimor's slot). ***
     → HSD_PadRenewRawStatus (0x80376A20-A28)
        └ TriggerSendInput (0x80376A28): reads the local PADStatus, ships it over
          EXI to the peer, then OVERWRITES the local + remote pad slots from the
          delay buffer / network. (Slippi-owned; do NOT hook here.)
     → engine simulation (incl. HSD_PadRead 0x803775B8) reads the now-substituted
       pad data.  *** Editing here = consumer-side = DESYNC. ***
   ```
   Inject at `0x8034E2AC` (upstream of the EXI scrape). Never at `0x803775B8`.

3. **Loading a savestate (F4) wipes any gecko not present when the savestate was
   captured.** The harness *enters online by loading slot 4* (a savestate of the
   Slippi direct-connect menu with the opponent's name pre-typed; Enter searches
   and connects). So **boot-installed geckos are wiped the moment you go online.**
   The fix is to **bake the gecko you need into the slot-4 savestate** (see §3).
   Anything baked in is present at the rollback baseline and survives the match.

---

## 2. Prerequisites (one-time machine setup)

- **macOS SIP disabled** (`csrutil status` → disabled) so `dme` can `task_for_pid`.
- **Accessibility** granted to the terminal/Python (synthetic F-keys/Enter).
- **`Dolphin` hardlink** next to the real executable so `dme.hook()` (which scans
  for a process literally named `Dolphin`) finds it:
  ```bash
  cd "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/"
  ln "Slippi Dolphin" Dolphin
  stat -f '%i %N' "Slippi Dolphin" Dolphin   # inodes must match
  ```
  **This gets wiped whenever the .app is rebuilt/updated — recreate it if
  `dme.hook()` starts failing.**
- **keystone/capstone** importable; on this machine keystone needs
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. Prefix every run with it.
- **ISO / paths**: hard-coded in `melee_harness.py` (`DOLPHIN_HARDLINK`,
  `ISO_PATH`, `USER_DIR`, `GAME_SETTINGS_INI`).
- **The other machine** must be queued/ready to accept the direct-connect, and in
  an **active in-game match**, or F4+Enter lands at the online CSS (`0x0008`)
  instead of in-game (`0x0208`). The user manages this.

**Online play works through the harness launch** even though the harness writes a
minimal user `GameSettings/GALE01r2.ini`: Dolphin layers it on top of the app
bundle's `Sys/GameSettings/GALE01r2.ini` (the full `$Required: Slippi Online`
codeset), so netplay still functions.

---

## 3. The required slot-4 savestate (do this before any online dev)

> Two different bakes: bake **meta-flush** into slot 4 for **dev iteration** (this
> section); bake the **finished macro** (e.g. `online_auto_lcancel.gecko.txt`) into
> slot 4 to **ship for real play** (§9). Same procedure, different gecko.

To iterate online over dme you need the **meta-flush gecko baked into slot 4**.
This is a manual user step (once per meta-flush version):

1. In Slippi Manager → *Add Gecko Code*, paste the meta-flush code (generate it
   with the snippet in [`ONLINE_REFERENCE.md` §Meta-flush code](ONLINE_REFERENCE.md#meta-flush-gecko-code), or from `instr_writer.META_FLUSH_LOGIC` via `melee_harness.gecko_c2_lines`).
2. **Enter an online match the normal way (matchmaking/direct — NOT via F4)** so
   the gecko is live and un-wiped.
3. **Save state to slot 4** (overwrite the direct-connect-entry savestate).

Now `F4` restores a savestate that *contains* meta-flush → it's present online and
survives rollbacks → you can `write_instrs`/`patch_branch` at runtime online.

Validated by `online_savestate_validate.py` (gecko baked in slot 4 shows up online
as a BRANCH while a boot-installed one shows VANILLA) and
`online_metaflush_validate.py` (meta-flush responds online, no desync).

---

## 4. The online dev loop

All in **one** Python process (re-attaching dme in a fresh process is
unreliable — see §6). Pattern (see any `online_*.py`):

```python
kill_stale_dolphins()            # pkill -9 -x Dolphin; wait until gone
h = Harness()                    # do NOT install_meta_flush — it's in the SS now
h.launch(); h.hook_dme(); h._wait_for_cpu_alive()
# enter online:
mh._focus_pid(pid); mh._send_key(118)   # F4  (load slot 4)
time.sleep(3.0)
mh._focus_pid(pid); mh._send_key(36)    # Enter (search/connect)
time.sleep(15.0)
# confirm in-game by MAJORITY vote (reads can tear during rollback):
#   scene = ((w<<8)|(w>>24)) & 0xFFFF  on word @ 0x80479D30 ; want 0x0208
# confirm meta-flush present: read 0x803775C0, expect a branch (0x48xxxxxx)
# install your cave + hook:
iw.write_instrs(h, CAVE, payload)        # CAVE = 0x803FA600 (see §6)
iw.patch_branch(h, HOOK, CAVE)           # HOOK = 0x8034E2AC
# observe via throttled, detach-tolerant reads (sleep ~0.012, re-hook on fail)
# leave Dolphin running; do NOT call h.close() if you want to keep the session
```

Then ask the user to confirm "no desync on your screen" — that's the only
ground truth for the peer's side (your side can look fine and still be desynced,
though producer-side edits shouldn't desync).

---

## 5. Building a macro: the recipe

A producer-side macro is a **C2-style cave** branched in at `0x8034E2AC`. The
cave runs every local poll. Structure:

```
cave (at 0x803FA600):
  stwu r1,-0x20(r1); stw r6..r9        # save the scratch regs you use
  ── gates: read the LOCAL player's state, decide what to inject ──
  # local player GObj port (works as P1 or P2): read the ODB:
  #   odb  = *(r13 - 0x49E4)
  #   port = *(odb + 0)                 # ODB_LOCAL_PLAYER_INDEX  (NOT +2! see §9)
  #   gobj = *(0x80453130 + port*0xE90) ; pdata = *(gobj + 0x2C)
  #   state = *(pdata + 0x10) & 0xFFFF  ; MEM1-check (top byte 0x80) every ptr!
  oris r0, r0, <button bit>            # inject: Z=0x10, L=0x40, X=0x400, A=0x100 (r0 hi16)
  lwz r6..r9; addi r1,r1,0x20           # restore
  ── the displaced original + branch-back are appended by the INSTALL path: ──
  rlwinm r0,r0,0x10,0x12,0x1f           # displaced original (0x540084BE) — runs once
  b 0x8034E2B0                          # back to the instruction after the hook
```

Key facts that make this work:
- At `0x8034E2AC`, `r0` = raw SI word with the **button bits in the high 16**.
  The displaced `rlwinm` rotates them down and masks (`& 0x3FFF`) into
  `PADStatus[r4+0]`. So **`oris r0,r0,BIT` sets button BIT** in the output. (The
  16-bit button word: A=0x100 B=0x200 X=0x400 Y=0x800 Z=0x10 R=0x20 L=0x40 Start=0x1000.)
- **You must PULSE, not hold** (the game has no input buffer — §9). To press a
  button on multiple frames, release it between presses (gate the `oris` on e.g.
  `frame % 7 == 0`); holding it every frame registers only one press. (True for the
  analog trigger too — held analog does NOT L-cancel; it needs the rising edge.)
- **To inject a STICK or ANALOG TRIGGER (not a button), hook `0x8034E680` instead**
  and `stb` the value into the PADStatus byte (`6(r4)`=analog L, `7(r4)`=analog R,
  `2..5(r4)`=stick/c-stick). `0x8034E2AC` is too early (PAD_Read rebuilds those bytes
  after it); `0x8034E680` is after the per-port calibration finalizes them, before the
  builder returns (`0x8034E69C`). Displaced there = `lbz r0,7(r3)` (`0x88030007`);
  preserve `r3` (calib ptr) and `r4`. This is how the shipped analog-L macro works —
  and an analog trigger `< 0xAA` sets no digital bit, so it can't airdodge/re-nair.
- **Preserve `r0`, `r4`, `r5`, `r13`** at `0x8034E2AC` (at `0x8034E680` preserve
  `r3`, `r4`, `r13`). Save/restore everything else you touch.
- **Gate by the local player's state** so you only inject when intended.
- For a runtime **toggle**, patch a **code** instruction (e.g. `oris`↔`nop`), not a
  data flag — data in `0x803FAxxx` is not reliably preserved across rollback (§6).
- **Install path appends the displaced + branch differently** (both keep the
  displaced — but only after the §6 fix): the dme path uses `finalize_payload`
  (`[logic][displaced][real branch]`); the shipped C2 gecko uses `gecko_c2_lines`
  (`[logic][displaced][nop][0x0 branch-slot]` — the codehandler overwrites the slot).
  Do NOT hand-roll a C2 that ends in your displaced (§6).
- Build with **keystone**, then **capstone-verify the words before flushing**
  (hand/auto-encoded branches are the #1 silent-failure source).

To **ship** a macro for real play (not self-driven), it's just the L-cancel-style
gate + inject (the player controls the character). To ship for online without the
harness, bake the final cave's gecko into the user's Slippi Manager + savestate,
exactly like meta-flush in §3.

---

## 6. Gotchas (each cost real time)

- **C2 codehandler overwrites the body's LAST word with its branch-back** (it does
  NOT append). So a C2 body must end with a throwaway word; if your displaced
  original (or any real instruction) is last, it gets eaten. `gecko_c2_lines` now
  always reserves a trailing `0x00000000` branch-slot (+ a nop to stay even) — it
  previously only padded for ODD word counts, so EVEN-count codes silently lost
  their displaced. Symptom when this bit the L-cancel: the button-extraction
  `rlwinm` never ran, so `sth` stored raw analog bytes as buttons → "A dead, stick
  → DPAD". (Idempotent displaced loads like meta-flush's `lbz` hid it; verify a C2
  cave with `verify_codehandler_displaced.py`.) **The dme path (`finalize_payload`)
  is unaffected** — it appends a real branch as the last word itself.
- **Cave placement.** The cave must NOT overlap the meta-flush control plane
  `0x803FA440-0x803FA44C`. `DEFAULT_CAVE` (`0x803FA3E8`) is only 0x58 bytes below
  it — a >22-word cave runs into it, and `flush_range` then corrupts your cave →
  **crash**. **Use `CAVE = 0x803FA600`.** (Small caves at DEFAULT_CAVE work, which
  is why the no-op/OR-L tests didn't crash but the big self-drive cave did.)
- **dme detaches under heavy polling.** Throttle reads (`time.sleep(~0.012)`),
  and re-`dme.hook()` on failure. Symptom: reads/writes start raising
  `Could not read/write memory`.
- **Re-attaching dme in a *new process* gives garbage/torn reads** (saw scene
  `0x143F`, code addrs reading `0`). Do launch + entry + observe + patch **in one
  process**. An early "match ended" conclusion was actually a bad re-attach read.
- **Majority-vote every read during a match.** Rollback rewrites MEM1; a single
  read can be torn. Read N times, take the mode.
- **Data flags in `0x803FAxxx` aren't reliable online** — toggle a **code**
  instruction instead (code persists across rollback; data may be rolled back).
- **The harness's boot codehandler cave is small** — a large C2 (the ~50-word
  L-cancel) installed via `install_gecko_c2` does NOT appear at the hook offline
  (`0x8034E2AC` stays vanilla); small ones (Altimor-sized) do. The user's *full*
  Slippi codeset has a bigger cave, so the big gecko installs there. Practical
  effect: you can't end-to-end test a big shipped C2 in the harness's minimal
  setup — validate the **logic** via the dme path (`finalize_payload` + meta-flush,
  which uses its own cave at `0x803FAxxx`), and validate the **C2 packaging**
  separately with a small probe (`verify_codehandler_displaced.py`).
- **Testing L-cancel: use a FULL hop, not a short hop.** A short-hop nair lands in
  the aerial's **auto-cancel** window (~7f) regardless of L/Z, masking the effect.
  Full hop lands in the real landing-lag window → 15f baseline → 7f cancelled. (The
  self-drive presses X in both Wait and KneeBend to force a full hop.)
- **NOT online-safe:** `seed_snapshot` / `restore_snapshot` (write MEM1),
  `bp.py` breakpoints (freeze the game), boot-installed geckos (wiped by F4).
- **Connection sometimes lands at online CSS (`0x0008`)** instead of in-game
  (`0x0208`). Not a savestate problem — the other machine needs to be in an
  active match. Relaunching is fine (each F4+Enter is a fresh connect).

---

## 7. Quick verification you're set up

```bash
# 1. hardlink present + inodes match (recreate per §2 if not)
stat -f '%i %N' "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Slippi Dolphin" \
                "/Users/andrewashman/Library/Application Support/Slippi Launcher/netplay/Slippi Dolphin.app/Contents/MacOS/Dolphin"
# 2. keystone import
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 -c "import keystone,capstone;print('ok')"
# 3. confirm slot 4 is baked with meta-flush.
#    The other machine (Windows peer) NO LONGER needs to be driven by hand:
#    Harness.enter_online(peer=Peer()) launches Melee + F1 + Enter on it over SSH.
#    One-time Windows setup: peer/SETUP_WINDOWS.md. End-to-end smoke test:
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 verify_peer.py
# 4. read-only online bring-up (auto-drives the peer if reachable, else manual):
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 online_step1_bringup.py
```

> **Autonomous peer (no walking to the Windows box).** The Mac side always
> automated its own F4/Enter; the Windows peer's launch + F1 (load slot-1
> direct-connect savestate) + Enter is now triggered remotely by `peer.Peer`
> over SSH (it `schtasks /run`s a pre-registered *interactive* Scheduled Task —
> SSH-spawned processes can't deliver keystrokes to the game, Session 0). The
> harness self-confirms via its own scene reaching `0x0208`, so the trigger is
> fire-and-forget; **you remain the desync verifier** (glance at the Windows
> screen at the ARM marker). If the peer is unreachable, `enter_online` falls
> back to the legacy manual flow. Setup + troubleshooting: `peer/SETUP_WINDOWS.md`.

---

## 8. The scripts (chronological — each is a runnable experiment)

Run with `DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 <script>`. All leave
Dolphin running for the next step and print what they observed.

| Script | What it establishes |
| --- | --- |
| `online_step1_bringup.py` | Read-only online bring-up; meta-flush dormant; confirms scene `0x0208`, frame counter, player pointers. |
| `online_test.py` | Consolidated meta-flush ping test (superseded by the validate scripts). |
| `online_probe_hooks.py` | Read-only census of which hooks are live (shows re-attach garbage problem). |
| `online_beforeafter.py` | Single-process menu-vs-online hook census; shows boot geckos revert online. |
| `online_survival_8034.py` | Proves a harness-installed gecko at `0x8034E2AC` ALSO reverts online (it's the F4 wipe, not the address). |
| `online_savestate_validate.py` | **Key:** Altimor baked into slot 4 survives F4 (BRANCH online) while boot meta-flush reverts — proves the savestate mechanism. |
| `online_metaflush_validate.py` | **Key:** meta-flush baked into slot 4 works online (responds, no desync); debug-region scratch survives rollback. |
| `online_codepatch_persist.py` | **Key:** a runtime code patch at `0x8034E2AC` persists across rollback → dme iteration online is viable. |
| `disasm_altimor_slot.py` | Disassembles the `0x8034E2AC` function (read at the menu) — how the displaced `rlwinm` builds the button word. |
| `online_orL_test.py` | **Key:** producer-side `oris L` at `0x8034E2AC` works online (local char shields); local player = port 1; no desync. |
| `online_lcancel_selfdrive.py` | **Working online L-cancel** (self-drive scaffold): pulsed Z (1/6) during aerials → Fox NAIR 14.9f → 7.5f, no desync, no misfires; dynamic P1/P2 local-port (§9). |
| `make_online_lcancel_gecko.py` | Generates + capstone-verifies the **shippable** gate+pulse-only gecko → `online_auto_lcancel.gecko.txt`. |
| `verify_codehandler_displaced.py` | Probe: installs a small C2 and disassembles the codehandler's cave — proves the codehandler overwrites the body's last word (the §6 displaced bug). |

---

## 9. Case study: the L-cancel (solved)

The offline auto-L-cancel (`auto_lcancel/`) presses L on a 1-press/6-release
cadence during aerials. Porting it online took two non-obvious fixes:

**Fix 1 — pulse, don't hold (the game has NO input buffer).** A button "pressed"
two frames in a row registers only ONCE; the L-cancel re-triggers only on a fresh
**rising edge** (0→1). Holding the button (e.g. `oris L` every aerial frame) gives
exactly one rising edge, so the cancel window lapses — this was the long-standing
bug (`0x680` stuck at 255). The cave must **release between presses**. Pressing
**every other frame** works (`1,0,1,0` — each press follows a release → rising
edge); the offline canonical cadence is 1-press/6-release.

**Fix 2 — use Z (digital), and stop trusting `0x680`.** L on a GameCube has both a
digital and an analog press; injecting digital L was unreliable. **Z (`0x0010`) is
purely digital and triggers the L-cancel.** Crucially, **`Char+0x680` tracks L/R,
not Z**, so it stays maxed (255) even when a Z-cancel succeeds — it is a
*misleading* observable here. The real, unambiguous observable is **landing-state
(0x46-0x4A) duration: 15f no-cancel → 7f cancelled.**

**The winning cave** (producer-side at `0x8034E2AC`, cave at `0x803FA600`):
```
read LOCAL player's action state (via ODB, see §5)
in aerial (state 0x41-0x45):
    n = (int) action_frame              # Player Data + 0x894 (float, resets to 1.0)
    if (n - 1) % 7 == 0:  oris r0,r0,0x0010   # Z, one rising edge per 7 aerial frames
    else:                 (release)
```
Result online (v1/v2, global-frame cadence): Fox NAIR landing **14.9f → 7.5f**. The
`oris Z` is toggled nop↔oris to A/B-test baseline vs cancel in one session.

**v3 (2026-05-22) changed the cadence anchor** from the global frame counter
(`0x80479D60`, `frame % 7`) to the **per-aerial Action State Frame Counter**
(Player Data `+0x894`, `(n-1) % 7`). The global cadence is rollback-safe but has a
random 0-6 frame phase relative to the aerial, so short / late / near-ground
aerials could end before any press frame fell inside them and never L-cancelled
("slow uptake", BUG 1). Anchoring to `+0x894` makes the first press land on the
first aerial frame. `+0x894` is a float; the cave integer-decodes it without FPU
(`n = (0x800000 | (bits & 0x7FFFFF)) >> (150 - exp)`). See `docs/L_CANCEL_HANDOFF.md`.

Why both anchors are rollback-safe: `0x8034E2AC` is inside `PAD_Read`, and Slippi's
`SkipNewInputFetchOnRollback` skips the `PAD_Read` call during rollback
re-simulation — so the hook fires **once per real frame**, not per replayed frame.
`+0x894` is additionally rollback-safe because it's game state (rewound by
rollback), unlike a scratch counter.

The jump→nair self-drive (Wait/KneeBend→X, JumpF/Fall→A) is **test scaffold only**
(true for both the digital and analog versions). The macro itself is the gate + pulse
alone — in real play the human controls the character; the macro just adds the pulse
during aerials. (The v3 digital macro pulsed Z; the **shipped** v4 pulses analog L —
see below.)

### v4 — pulsed ANALOG L (the SHIPPED macro; supersedes the digital Z line above)

Two bugs surfaced in the digital-Z v3 that analog L fixes by construction:
1. **Hitlag miss** — when an aerial CONNECTS, hitlag freezes the Action State Frame
   Counter (`0x894`), so the v3 `(n-1)%7` cadence stalled and the Z timer ran past
   the window → missed cancel (`online_hitlag_diag.py` reproduced 1/8 and an
   override fixed it).
2. **Trailing spill** — a digital Z/L rising edge in an air-ending aerial
   (FallAerial) re-nairs (Z = A) or airdodges.

**The fix:** pulse a **light ANALOG L** (value `0x80`, every other frame) during
aerials, injected producer-side at **`0x8034E680`** (write the analog byte `6(r4)`,
which PAD_Read has just finalized at `0x8034E67C`; the builder `blr`s at `0x8034E69C`,
all upstream of the EXI scrape). Why this is complete:
- A value `< 0xAA` sets **no digital button bit** (PAD_Read converts analog≥`0xAA`→
  digital L at `0x8034E244`) and presses **no Z**, and the airdodge trigger-check
  reads the digital L/R timer `0x680` → light analog L **cannot airdodge or re-nair**.
  Trailing spill gone by construction.
- The pulse uses the **global** frame parity (`0x80479D60 & 1`), which keeps ticking
  through hitlag → cancels on connecting aerials too. No `0x894` anchor needed.
- **Must pulse** (held analog does NOT cancel — needs a rising edge; tested), but
  pulsing every other frame is safe since analog L can't misfire.

```
read LOCAL player's action state (via ODB, see §5)
in aerial (state 0x41-0x45):
    if (global_frame & 1) == 0:  stb 0x80, 6(r4)   # light analog L; else release
```
Result online: Fox NAIR **14.8f → 7.1f**, LCancelStatus **15/15** landing-nairs and
**10/10 hit-aerials (hitlag)**, **0 misfires**, no desync.

**SHIPPED:** [`online_auto_lcancel.gecko.txt`](../online_auto_lcancel.gecko.txt) —
generated + capstone-verified by
[`make_online_analog_lcancel_gecko.py`](../make_online_analog_lcancel_gecko.py).
Gate+pulse only (no self-drive), dynamic local-port via the ODB (`+0`). Tests:
`offline_analog_lcancel.py` (value sweep), `online_analog_selfdrive.py` (A/B),
`online_analog_hitlag.py` (hitlag, peer walks in); mechanic mapped by
`disasm_lcancel_analog.py`. Deploy by baking into the slot-4 savestate (§3) — steps
in the gecko header.

---

## 10. See also
- [`ONLINE_REFERENCE.md`](ONLINE_REFERENCE.md) — addresses, offsets, button bits,
  action states, scene IDs, the meta-flush code text.
- `auto_lcancel/` — the offline L-cancel (proven mechanic; the logic to port).
- `instr_writer.py` — meta-flush + `write_instrs`/`patch_branch`/`flush_range`.
- `melee_harness.py` — `Harness`, `gecko_c2_lines`, `finalize_payload`, key-send.
- `slippi-ssbm-asm-master/` — Slippi's ASM source (e.g.
  `Online/Core/TriggerSendInput.asm`, `Common/Common.s`). Vendored; do not modify.
