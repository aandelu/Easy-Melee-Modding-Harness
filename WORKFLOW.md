# Debugger-driven macro development workflow

This doc describes how an agent uses the harness's runtime debugger (`instr_writer.py` + `bp.py`) to answer a question about the game, then turn that answer into a shipped gecko code.

This workflow assumes the architecture in [`HARNESS.md`](HARNESS.md) is already up. If `verify_savestate.py` and `verify_bp.py` both PASS, you're ready.

---

## The five-stage loop

### Stage 1 — Pose the question, pick a hook

Macro development always comes down to questions the static code/docs don't answer:

- *"What action-state ID does Fox enter on the first frame of an aerial shine?"*
- *"Which PPC instruction writes Fox's action-state byte when shine begins?"*
- *"At hook X, what are the values of r3 and r4?"*
- *"Does writing PD+0x65C alone trigger an input, or does the engine clobber it first?"*

For each, pick a **hook address** where the CPU is guaranteed to be when the answer is observable. Sources in priority order:

1. **`SSBM memory address sheet/Function_Addresses.csv`** — function entry points. Best for "what happens when X runs?"
2. **Per-frame loop hooks** documented in `HARNESS.md` §6. `0x803775B8` (pad-read, BEFORE the buttons read) fires every frame per pad. `0x803775C0` (pad-process loop) is **taken by the meta-flush gecko** — do not use it for your own hooks.
3. **`slippi-ssbm-asm-master/External/*.asm`** — every `# Address:` header in there is a hook proven to work in Slippi. Search there before assuming a hook is unsafe.

### Stage 2 — Set the breakpoint, drive the trigger, freeze

```python
from melee_harness import Harness, POWERON_COUNT
import instr_writer as iw
import scenario as sc
import bp

h = Harness()
iw.install_meta_flush(h)          # MUST be before launch()
h.launch()
h.hook_dme()
h._wait_for_cpu_alive()
iw.wait_for_meta_flush_alive(h)   # codehandler beat
h.seed_snapshot()                 # F2 loads savestate slot 2, snapshots MEM1

# Install the BP.
b = bp.set_breakpoint(h, target_addr=0x80...)

# Drive whatever triggers the situation you want to inspect.
sc.force_action_state(h, port=1, state=sc.CATCH)   # e.g.

# Wait for the BP to fire. Game freezes when it does.
bp.wait_for_hit(b, timeout_s=5.0)
```

### Stage 3 — Inspect

While the BP is parked in its spin loop, the entire PPC core is halted. You can:

- **Read all registers**: `snap = bp.read_snapshot(b)` returns a dict with `r0..r31, lr, ctr, cr, pc`.
- **Read arbitrary memory** via the usual `h.read_word(addr)` / `h.read_bytes(addr, n)`. Player Data, controller state, anything.
- **Mutate registers before resume**: `bp.write_snapshot(b, r3=0xCAFE, lr=0x80003100)`. The handler reads the (potentially edited) snapshot on the way out and resumes with the new values. Use this to test "what if this branch had been taken?" without changing the code.

### Stage 4 — Release, iterate

```python
bp.continue_(b)
# BP refires on next pad pass (if hook is per-frame).
# wait_for_hit / read_snapshot / continue_ again as needed.
bp.remove_breakpoint(b)   # restores the original instruction
```

For conditional triggers ("only stop when r3 == 0xD4"): `bp.wait_for_condition(b, lambda s: s["r3"] == 0xD4)` — the handler fires every time the hook executes, but Python silently continues until the predicate matches. See `verify_bp_cond.py` for the pattern.

For single-step ("after this BP, halt at the next instruction"): `bp.step(b)` decodes the displaced original to find the successor PC, installs a one-shot BP there, and continues. See `verify_bp_step.py`. **Hazard:** if the successor PC is itself an already-installed gecko hook (like `0x803775C0`), step follows the gecko's branch into the codehandler cave rather than the vanilla instruction. The smoke test demonstrates this.

### Stage 5 — Convert finding to a gecko code

Once you know the instruction / register / address that matters, build the gecko:

```python
LOGIC = [
    0x3D80803F,   # lis r12, 0x803F           ← hand-written PPC, big-endian ints
    0x618C....,   # ori r12, r12, 0x....
    ...
]

h.install_gecko_c2(name="my-macro", hook_addr=0x80...,
                   logic_words=LOGIC, displaced_orig=0x...)
```

**Always verify the assembled bytes before launching.** `verify_v2_with_keystone.py` is the reference pattern: hand-write the logic AND a label-only PPC source, assemble the source with `keystone-engine`, bit-for-bit diff. Catches the hand-counted-branch-offset class of bug that ate D.1 in `docs/sessions/2026-05-15.md`.

For runtime iteration (without rebooting Dolphin per candidate), use `iw.write_instrs(h, cave, LOGIC)` to install at runtime. The meta-flush gecko issues `dcbf`/`sync`/`icbi`/`isync` so the CPU observes the new bytes. **Caveat:** runtime-installed code is wiped by `h.restore_snapshot()`. Either re-install after each restore, or install before `seed_snapshot()`.

---

## Sharp edges (read these before debugging)

1. **BPs halt the entire PPC core.** Dolphin's other threads (audio, graphics, window) keep running, so the application stays responsive. But on a real Slippi netplay session, the spin would desync. The BP primitive is **dev/offline only**.

2. **The meta-flush hook is at `0x803775C0`.** Any BP / runtime-installed code that wants the pad-process loop must use a different hook (e.g. `0x803775B8`, the pad-read).

3. **`runtime` patches do not persist across `restore_snapshot`.** Snapshot is taken once at `seed_snapshot()`. `restore_snapshot()` writes 24 MB of MEM1 back, wiping any runtime patches you installed after the snapshot. The shipped gecko codes (those installed via `install_gecko_c2`) survive because they're in MEM1 at snapshot time.

4. **The r0-as-rA trap.** In `addi`, `addis`, `lis`, `stmw`, and load/store instructions with `rA` = base register, an rA *field* value of 0 reads as the literal value 0, NOT register r0. `addi r0, r0, 16` computes `0 + 16 = 16`, not `r0 + 16`. Always use r3..r12 as base registers in cave handlers. `bp.py` and `instr_writer.py` both navigate this carefully; copy their patterns when extending.

5. **`lmw rD, d(rA)` is undefined when rA is in [rD..r31].** Restore r1 manually with a separate `lwz r1, ...` after `lmw r2, ...` rather than relying on `lmw r0, ...` to also restore r1.

6. **Address sheet > `Project_Addresses.md`.** `SSBM memory address sheet/*.csv` is authoritative. The curated quick-reference often omits load-bearing details (e.g. the `+0x2C` indirection between GObj and Player Data).

7. **Stepping across an existing gecko hook follows the gecko's branch.** If you step past a patched instruction, the "displaced original" you captured is a branch into the codehandler cave, not the vanilla instruction. `bp.step()` does the right thing in most cases but the smoke test documents the edge case.

---

## End-to-end example: "where does Fox's action state get written when shine starts?"

Sketch (not a runnable script — adapt to your situation):

```python
h = Harness()
iw.install_meta_flush(h)
h.launch(); h.hook_dme(); h._wait_for_cpu_alive()
iw.wait_for_meta_flush_alive(h)
h.seed_snapshot()

# Hypothesis: Set_Action_State is called when shine begins. Per Function_Addresses.csv,
# Set_Action_State is at 0x800693AC (verify this from the sheet — example only).
b = bp.set_breakpoint(h, 0x800693AC)

# We expect this to fire constantly. Use a conditional BP that only stops
# when r3 (the port's Player Data ptr) belongs to Fox (P2) AND the new
# action state (in r4) is in the shine ID range.
def pred(s):
    pd = s["r3"]
    if not (0x80000000 <= pd < 0x81800000):
        return False
    port = h.read_word(pd + 0x0C) & 0xFF
    return port == 1   # P2 = port index 1

# Trigger: force Fox into the input pattern that should yield shine. Or
# play it manually with the Dolphin window in focus.
sc.force_action_state(h, port=2, state=sc.KNEE_BEND)   # set up jumpsquat

bp.wait_for_condition(b, pred, timeout_s=10)
snap = bp.read_snapshot(b)
print(f"Set_Action_State called with PD=0x{snap['r3']:08X}  new_state=0x{snap['r4']:04X}  LR=0x{snap['lr']:08X}")

# LR tells us which caller invoked Set_Action_State. That's the instruction
# we want to investigate further (BP on it, or read backwards from there).
bp.continue_(b)
bp.remove_breakpoint(b)
```

The answer (an exact action-state ID + the calling-instruction PC) becomes the input to Stage 5 — write a gecko that hooks the calling site and reacts.

---

## When NOT to use this workflow

- **For final macro shipping.** The shipped code is a boot-time gecko (`install_gecko_c2`) installed via `GameSettings/GALE01r2.ini`, exactly like `candidate_d_standalone_v2.py`. The debugger isn't on at runtime.
- **For pure-dme exploration.** If you can answer the question by reading/writing data memory only (no code patches, no BPs), `dme_experiment/` already has the patterns. See its `FINDINGS.md`.
- **For static questions** answerable from the address sheet + `slippi-ssbm-asm-master/`. Always check those first; they're cheaper than launching Dolphin.
