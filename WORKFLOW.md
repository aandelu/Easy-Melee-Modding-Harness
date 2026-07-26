# Macro development workflow

The one how-to-develop doc for this repo. A macro moves through three phases:

1. **[Discover](#chapter-1--discover)** — offline, breakpoint-driven: answer the game-behavior questions the docs can't.
2. **[Iterate](#chapter-2--iterate)** — turn the answer into cave logic and tune it, offline first, then online.
3. **[Ship](#chapter-3--ship)** — package as a gecko, verify the bytes, test the real install path, deploy, record.

This doc is procedure. Addresses, offsets, struct layouts, hook tables, register-preservation rules, and PPC encoding traps live in [`docs/REFERENCE.md`](docs/REFERENCE.md) — link targets below, don't restate them from memory. Harness API details are in [`HARNESS.md`](HARNESS.md). Worked per-macro write-ups are in `docs/macros/<name>.md`.

---

## Chapter 1 — DISCOVER

Offline, savestate-driven, with software breakpoints. Everything in this chapter freezes or rewrites the game and is **dev/offline only**.

### 1.1 Bring-up expectations

First time on a machine: [`HARNESS.md`](HARNESS.md) §9 (SIP disabled, Accessibility granted, `Dolphin` hardlink next to `Slippi Dolphin`). After that, a session starts from a clean slate:

```bash
pkill -9 -x Dolphin        # then poll `pgrep -x Dolphin` until empty —
                           # plain pkill returns before the process dies
```

Health-check with the verify suite before trusting anything. Each prints `[PASS]`/`[FAIL]` and exits non-zero on failure:

```bash
# Canonical suite, in order:
python3 verify_savestate.py      # harness alive: launch, hook, snapshot (~12s)
python3 verify_inject_gecko.py   # boot-time C2 install path (~25s)
python3 verify_meta_flush.py     # runtime code-patch primitive (~25s)
python3 verify_bp.py             # software breakpoints (~25s)
python3 verify_scenario.py       # full iteration loop: trigger + baseline (~12s)
python3 verify_peer.py           # Windows netplay peer reachable (online work only)
python3 verify_d_standalone_v2.py  # shipped JC-shine still reproduces (~15s)
```

If `verify_savestate.py` and `verify_bp.py` both pass, the discovery loop below is available. Prefix any run that assembles code with `DYLD_LIBRARY_PATH=/opt/homebrew/lib` (keystone needs it on this machine).

### 1.2 Pose the question, pick a hook

Discovery starts with a concrete question:

- "What action-state ID does Fox enter on the first frame of X?"
- "Which instruction writes this byte, and who calls it?"
- "At hook X, what are r3 and r4?"
- "Does writing this field stick, or does the engine clobber it?"

Pick a **hook address** where the CPU is guaranteed to be when the answer is observable. Sources, in priority order:

1. `SSBM memory address sheet/Function_Addresses.csv` — function entry points. Best for "what happens when X runs?"
2. Per-frame hooks — see the hook table in [`docs/REFERENCE.md`](docs/REFERENCE.md). The pad-read at `0x803775B8` fires every frame per pad and is free; `0x803775C0` is **taken by the meta-flush gecko** — never hook it.
3. `vendor/slippi-ssbm-asm-master/` — every `# Address:` header there is a hook proven to work in Slippi. Search it before assuming a hook is unsafe.

Check the static sources first — the address sheet and vendored ASM are cheaper than launching Dolphin. If the question is answerable by reading data memory alone (no code patches), plain dme reads in a scratch script suffice; skip breakpoints entirely (worked examples of pure-dme exploration live in `dme_experiment/` — see its README and FINDINGS.md).

### 1.3 Set the breakpoint, drive the trigger

```python
from melee_harness import Harness
import instr_writer as iw
import scenario as sc
import bp

h = Harness()
iw.install_meta_flush(h)          # MUST be before launch()
h.launch(); h.hook_dme(); h._wait_for_cpu_alive()
iw.wait_for_meta_flush_alive(h)   # codehandler beat
h.seed_snapshot()                 # F2 loads slot 2, snapshots all of MEM1

b = bp.set_breakpoint(h, target_addr=0x80......)

# Drive whatever triggers the situation you want to inspect:
sc.force_action_state(h, port=1, state=sc.CATCH)   # e.g.

bp.wait_for_hit(b, timeout_s=5.0)  # game freezes when it fires
```

Scenario-driving rules:

- `scenario.force_action_state` animates a state but applies **no physics**. Fine for self-contained triggers (e.g. Marth's Catch); useless for real motion. To actually jump/attack a character, inject controller inputs at the pad-read hook — see the self-drive pattern in §2.1.
- You can also just play the trigger by hand with the Dolphin window focused.

### 1.4 Inspect, mutate, release

While the BP spins, the entire PPC core is halted (Dolphin's window stays responsive — its other threads keep running):

- `snap = bp.read_snapshot(b)` → dict of `r0..r31, lr, ctr, cr, pc`.
- Read any memory as usual: `h.read_word(addr)`, `h.read_bytes(addr, n)`.
- `bp.write_snapshot(b, r3=0xCAFE, lr=0x80003100)` — edit registers before resume. Use this to test "what if this branch had been taken?" without changing code.
- `bp.continue_(b)` resumes; the BP refires next pass if the hook is per-frame. `bp.remove_breakpoint(b)` restores the original instruction.

Extensions:

- **Conditional stop:** `bp.wait_for_condition(b, lambda s: s["r3"] == 0xD4)` — the handler fires every hit but Python silently continues until the predicate matches. Pattern: `verify_bp_cond.py`.
- **Single-step:** `bp.step(b)` installs a one-shot BP at the successor PC. Pattern: `verify_bp_step.py`. Hazard: stepping across an already-installed gecko hook follows the gecko's branch into the codehandler cave, not the vanilla successor — the smoke test demonstrates it.

Typical endgame: the snapshot's `lr` tells you which caller invoked the function you broke on; BP on the caller next, and repeat until you have the exact instruction/register/address the macro needs.

### 1.5 The savestate loop and the wipe rule

There is no programmatic savestate API. `seed_snapshot()` loads slot 2 (synthetic F2) and snapshots all 24 MB of MEM1; `restore_snapshot()` writes it back to revert game state, patches, and the frame counter together.

The rule that governs the whole offline workflow: **runtime patches are wiped by anything that rewrites MEM1** — `seed_snapshot()`, `restore_snapshot()`, and manual savestate loads. Boot geckos survive (the codehandler reinstalls them); `write_instrs` patches do not.

- Install runtime patches **after** `seed_snapshot()`.
- Iterate by **in-game cycling** (re-drive the trigger, rewrite the cave), not by reloading slot 2 between trials.
- If you must `restore_snapshot()`, re-install your patches afterwards.

### 1.6 Sharp edges

- BPs are **never** usable online — the spin desyncs a netplay session instantly.
- PPC encoding traps (r0-as-rA, `lmw` restrictions) and the pad-struct layout: [`docs/REFERENCE.md`](docs/REFERENCE.md). Copy register handling from `bp.py` / `instr_writer.py` rather than improvising.
- `SSBM memory address sheet/*.csv` is authoritative over any curated doc. Trust empirical reads over CSV descriptions for fields you can directly observe.
- dme writes to the processed controller region race Dolphin's input pipeline and don't propagate — inject inputs at a hook, never by writing controller data memory.

---

## Chapter 2 — ITERATE

Two loops. Do the offline one first, always — most mechanics (trigger, action, observable) reproduce offline, and it's faster. Only netplay delay and netplay-safety are online-only concerns.

### 2.1 Offline loop

Two ways to run cave code offline:

- **Boot-time gecko** (`Harness.install_gecko_c2`, before `launch()`): what a finished offline macro uses. Requires a Dolphin relaunch per change — too slow for iteration.
- **Runtime meta-flush** (`instr_writer.write_instrs` + `patch_branch`): the iteration workhorse. One meta-flush gecko installed at boot; after that, dme-write fresh PPC anywhere in MEM1 and it's live within ~1 frame.

The loop:

```python
# after the §1.3 bring-up through seed_snapshot():
payload = finalize_payload(logic, HOOK, CAVE, DISPLACED)  # [logic][displaced][branch]
iw.write_instrs(h, CAVE, payload)
iw.patch_branch(h, HOOK, CAVE)
# observe via h.read_word / read_bytes; change logic; re-write_instrs; repeat.
```

Loop discipline:

- **Assemble and verify every payload before flushing it** (§3.1 — the rule applies here too, not just at ship time).
- **When a payload misbehaves, instrument first — theorize second.** Put an in-cave counter after each gate clause (`gecko_tools.counter_bump_asm` / `read_counters`) and read which clause rejects. Twice now (wavedash, ASDI) one counter run settled what three or four observer-based theories got wrong; the counters cost ~5 words per clause.
- **Iterate in-game**, never by reloading slot 2 (the wipe rule, §1.5).
- **A/B without relaunching:** toggle a single *code* instruction (`oris`↔`nop`, `stb`↔`nop`) via `write_instrs` to compare on/off in one session.
- **Self-drive** the character when no human is on the sticks: at the pad-read hook, read the action state and inject the inputs that cycle it (Wait→jump, airborne→attack, ...). `play_wavedash_offline.py` and `play_d2.py` are runnable worked examples; `verify_d2.py` shows the observation side.
- **Cave placement matters.** Use the cave map in [`docs/REFERENCE.md`](docs/REFERENCE.md); never overlap the meta-flush control plane — a cave that grows into it gets corrupted by `flush_range` and crashes Dolphin.
- Pick the **observable** before writing logic: a direct game-state flag beats a derived measurement. Find it in the address sheet first.

### 2.2 Online loop

Read this whole subsection before the first online run — online violates most offline assumptions. Per-address facts (producer hooks, ODB fields, scene IDs, register preservation): [`docs/REFERENCE.md`](docs/REFERENCE.md).

**One-time prerequisite — the slot-4 bake.** The harness enters online by loading savestate slot 4 (a savestate of the direct-connect menu with the opponent's code pre-typed), and **a savestate load wipes every gecko not present when the state was captured**. So any gecko you need online must be *baked into* slot 4:

1. Slippi Manager → Add Gecko Code → paste the gecko (meta-flush for dev iteration; the finished macro when shipping, §3.4).
2. Enter an online match **the normal way** (matchmaking/direct — not via F4) so the gecko is live.
3. At the direct-connect menu, save state to **slot 4**.

Re-bake whenever the baked gecko changes.

**Entry.** All in **one** Python process, start to finish — re-attaching dme from a fresh process yields torn garbage reads; verify a known word (a hook branch) before writing anything, and abort if it fails.

```python
kill_stale_dolphins()
h = Harness()                    # do NOT install_meta_flush — it's baked in slot 4
h.launch(); h.hook_dme(); h._wait_for_cpu_alive()
h.enter_online(peer=Peer())      # F4 + Enter locally; auto-drives the Windows peer
```

`Harness.enter_online(peer=Peer())` (from `peer.py`) triggers the Windows peer over SSH — Slippi launch, F1 (its slot-1 direct-connect savestate), Enter — via a pre-registered interactive Scheduled Task, then retries F4+Enter locally until in-game. One-time peer setup: [`peer/SETUP_WINDOWS.md`](peer/SETUP_WINDOWS.md); smoke test: `python3 verify_peer.py`. Success is confirmed by **two signals**: the peer's `peer_status.json` (`ok:true` = the Windows side did its part) and the Mac's own scene reaching in-game (`0x0208` at the scene controller `0x80479D30`; the online CSS is `0x0008`). If the peer reports ok but the scene never advances, the problem is savestate/timing/network, not peer plumbing. Never blind-Enter at the CSS — retries must re-load the slot-4 savestate each attempt, or you can drift into an offline VS match.

**Patching.** Confirm meta-flush is live (its hook reads as a branch, `0x48xxxxxx`), then `write_instrs` + `patch_branch` exactly as offline — but only at **producer-side** hooks.

**Hard rules** (each one cost real time):

- **Producer-side input edits only.** Editing pad data after Slippi serializes it (consumer side) desyncs. The producer hooks and their preserved-register requirements are in [`docs/REFERENCE.md`](docs/REFERENCE.md).
- **Pulse, don't hold.** The game has no input buffer; a held button (or analog value) registers one rising edge. Release between presses.
- **Toggle code, not data.** Scratch data in the cave region is not reliably preserved across rollback; patched code is.
- **Cadence off the global frame counter** if you need a rhythm — the per-action frame counter freezes during hitlag.
- **Majority-vote and throttle every Python-side read.** Rollback rewrites MEM1 mid-read; read N times and take the mode, sleep ~12 ms between reads, and re-`dme.hook()` on any read/write failure (heavy polling detaches dme).
- **The user's screen is the only desync ground truth.** Producer-side edits shouldn't desync, but confirm after every run — your side can look fine while desynced.

**Instrument in the cave, not in Python.** For anything frame-precise, a Python observer polling at ~60 fps is *lossy* — it cannot reliably sample a 1-frame state, and during the wavedash port it confidently reported a 1-frame-late timing as correct, sending the tuning the wrong way. Ground truth came from instrumenting the cave itself: outcome **counters** (e.g. perfect-vs-floaty per candidate) and a **per-frame state ring buffer** written by the hook, dumped over dme after the fact. Build the in-cave instrument **before** concluding anything about frame timing. Expect producer hooks to record ~2×/frame (rollback re-simulation runs them too) and normalize accordingly. Full account: [`docs/archive/WAVEDASH_ONLINE_RESULTS.md`](docs/archive/WAVEDASH_ONLINE_RESULTS.md). Python-side observers (`attach_observe_wavedash.py`, `play_wavedash_monitor.py`) are fine for coarse "is it alive / roughly what happened" monitoring only.

---

## Chapter 3 — SHIP

### 3.1 Package as a C2 gecko

Package the proven cave with `gecko_c2_lines` (in `melee_harness.py`) — it formats Dolphin GameSettings INI / Slippi Manager lines. Write a generator script per macro (`make_*_gecko.py` — `make_wavedash_gecko.py`, `make_online_analog_lcancel_gecko.py`, and `make_cactuar_dash_gecko.py` are the templates) that builds, verifies, and prints the gecko, with a header stating what it does, the hooks, and how it was validated. For online macros, emitting a RAW (06+04) form alongside the C2 form is worthwhile — it reproduces the exact validated memory state.

**Mandatory: assemble with keystone and capstone-verify before Dolphin ever sees the bytes.** Hand-counted branch offsets are the #1 source of "gecko silently doesn't fire". New payloads go through the shared `assemble_and_verify` helper in `gecko_tools.py` (`python3 gecko_tools.py` runs its self-check; `check_c2_body` enforces the C2 last-word padding rule); the three existing `make_*_gecko.py` generators predate it and inline the equivalent keystone+capstone check — both satisfy the rule. Disassemble the full payload and eyeball that branches land in-cave and the displaced original is present.

**The C2 codehandler overwrites the body's last word** with its branch-back — it does not append. A C2 body must therefore end with a throwaway word; `gecko_c2_lines` reserves the trailing slot automatically. Never hand-roll a C2 that ends on a needed instruction (the displaced original is the classic casualty). Prove a C2 cave kept its displaced word with `verify_codehandler_displaced.py`.

### 3.2 Test the REAL install path

The harness's minimal INI staging is **not** the same environment as the user's real Slippi install, and the differences bite silently:

- User-added INI codes install into whatever codehandler append space the user's full codeset leaves — the harness path doesn't have this limit. The cactuar-dash gecko passed every harness test and then **silently no-op'd in real Slippi** for exactly this reason.
- The harness's own boot codehandler cave is small; a large C2 that won't fit simply doesn't appear at its hook, with no error.

So before calling a macro shipped: install it the way the user will (paste into Slippi Manager / the real user dir), boot the way the user will, and **confirm the hook address reads as a branch** into a cave containing your logic. Validate logic via the dme path and C2 packaging via a small probe if the full C2 can't be end-to-end tested in the harness — but the real-install check is not optional.

### 3.3 Ship offline

- Package per §3.1; install via `Harness.install_gecko_c2` (harness use) or user-dir INI paste (standalone). `candidate_d_standalone_v2.py` is the shipped-standalone template.
- Provide a `verify_*.py` that reproduces the macro on a savestate and prints `[PASS]`/`[FAIL]` (e.g. `verify_d_standalone_v2.py`).

### 3.4 Deploy online — the F4 bake

An online macro must survive the F4 slot-4 entry, so it ships by being baked into the savestate, same procedure as §2.2's prerequisite:

1. Add the final gecko in Slippi Manager (both codes, if the macro uses two hooks) and enable.
2. Enter an online match normally — not via F4 — so the codehandler installs it.
3. Save state to slot 4 at the direct-connect menu.
4. Verify: enter via F4, confirm the hook(s) read as branches, play, and have the user confirm no desync.

For real (non-harness) play the user just adds and enables the gecko — no savestate or harness needed; the bake only matters for harness-driven entry.

### 3.5 Record it

- Update [`docs/STATUS.md`](docs/STATUS.md) — every shipped macro gets a line (name, gecko file, generator, validation state, pending items).
- Write or update the macro's page at `docs/macros/<name>.md`: mechanic, hooks, timing model, what was validated and how, known limits.
- **Promote shared facts to [`docs/REFERENCE.md`](docs/REFERENCE.md).** Any offset, hook property, or harness-wide gotcha you discovered belongs there ("every stable fact, stated once") — a macro doc's gotcha list is where such facts go to die, because the next non-<name> session never opens it.
- Commit the gecko text file (`*.gecko.txt`) and its `make_*_gecko.py` generator together.

### Ship checklist

- [ ] Payload keystone-assembled + capstone-verified (`gecko_tools.assemble_and_verify`, or the equivalent inline check in the existing generators); branches land in-cave, displaced original present
- [ ] C2 body ends on a throwaway word; `verify_codehandler_displaced.py`-style check if in doubt
- [ ] Real install path tested: user-style install, hook reads as a branch
- [ ] Offline: `verify_*.py` passes. Online: slot-4 re-baked with the final gecko, F4 entry verified, user confirms no desync
- [ ] `make_*_gecko.py` generator + `*.gecko.txt` committed, header documents hooks and validation
- [ ] [`docs/STATUS.md`](docs/STATUS.md) and `docs/macros/<name>.md` updated
