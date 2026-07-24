> **HISTORICAL (archived 2026-07-24).** The original development plan; executed. Three of its claims were later DISPROVEN: the "right hook" is NOT 0x803775C0 (buttons are read at 0x803775B8, and 0x803775C0 is now owned by meta-flush); netplay-safe macros are NOT intrinsically 1 frame late (producer-side hooks are frame-perfect); runtime dme patching IS possible (meta-flush). Current truth: docs/REFERENCE.md.

# Macro Development Plan (post-harness)

Captures the architectural choices, candidate designs, and open questions for actually building the Frame-1 macro now that the iteration harness is done. Authored before a `/compact` so details survive.

---

## 1. The frame-timing constraint (load-bearing)

From `Project_Context.md`: Marth's grab animation initiates on frame 1 of action state `0xD4 Catch`. The actual hitbox emerges on frame 7. For a jump-cancelled shine response:

- Frame 1: Fox must input **jump** (Y/X) on the *same frame Marth enters Catch*.
- Frame 4: Fox is airborne (jumpsquat ends).
- Frame 7: Fox can input **down + B** (shine).
- Plus 2 frames of Slippi netplay rollback delay baked in.

This is why frame-1 reaction matters. The macro is not just "press shine when Marth grabs" — it's a **multi-frame input sequence** starting on the trigger frame.

## 2. Where to hook — the timing trap (already paid for)

User already tried and ruled out:
- **C0 code (global frame hook)** — runs at frame boundary; Fox's inputs already processed → 1 frame late.
- **C2 at `0x8006AD10` (main character loop)** — per-character but inputs gathered before action state assignment → 1 frame late.
- **C2 at `0x803775C0` (HSD_PadRead) writing global float arrays (`0x804C1FAC`)** — corrupted joystick X-axis.
- **C2 at `0x803775C0` writing the raw PADStatus buffer** — works (Spot_Dodge_Macro.md is this), but spot-dodge specifically didn't trigger reliably.

The **right** hook is **`0x803775C0` writing raw PADStatus**, because:
- It fires per-pad per-frame, *before* Melee's engine reads PAD into the processed input register.
- We can read **Marth's raw input** (Z press) and **write Fox's raw input** (B + stick down) in the same hook pass, before either character's per-frame state transition runs.
- Both action-state transitions then happen in the same engine frame → frame-1 reaction.

**Key insight from the user's failure analysis:** action-state-keyed detection at this hook is *intrinsically 1 frame late* because action states for the current frame are assigned later in the frame. **Input-keyed detection (read Z press, react in same frame)** is the only path that gives true frame-1.

## 3. Architectural pieces we need to add

### 3a. Persistent **input-driver gecko** (for testing button-press macros)

Our `scenario.force_action_state(...)` only drives action states. A macro keyed on raw PADStatus Z press won't fire under that trigger. Build a small persistent gecko code that the harness installs alongside the candidate-under-test:

- **Hook**: `0x803775C0` (same as the test macro — both can coexist as separate C2 codes in the same INI; the codehandler chains them).
  - Or pick a slightly earlier hook in the pad-process loop if order matters.
- **Logic**: when `r24 == 0` (port 1 pass), load a flag byte from a known scratch slot in MEM1 (e.g. `0x803FA420`). If non-zero, OR `0x10` (Z bit) into `r25`'s `button` field at offset 0.
- **dme control**: harness writes 1 to `0x803FA420` to "hold Z on port 1", 0 to release.

Pseudo-asm:
```
cmpwi r24, 0
bne end
lis r11, 0x803F
ori r11, r11, 0xA420
lbz r0, 0(r11)
cmpwi r0, 0
beq end
lhz r0, 0(r25)          # current port 1 buttons
ori r0, r0, 0x0010      # add Z
sth r0, 0(r25)
end:
# displaced original `lbz r0, 2(r25)` appended
```

This gives the harness a faithful "Marth presses Z this frame" primitive. Add `scenario.drive_marth_z(h, held=True)` wrapping the flag write.

### 3b. Scenario driver extensions

`scenario.py` needs:
- `drive_marth_z(h, held=True/False)` — uses the input-driver flag.
- `run_z_trigger_trial(h, observe_frames=12)` — variant of `run_grab_trial` that drives Z press instead of forcing action state.
- Possibly hold-Z-for-N-frames helper.

Keep the action-state-based trigger too — useful for testing action-state-keyed macros and for cases where we want to bypass input.

### 3c. Empirical discovery — Fox's shine action state ID

`Action_State_Reference.csv` only enumerates universal states (≤ 0x154). Fox-specific shine states are in the character-specific range (≥ 0x155). To find:

1. Install a candidate that should make Fox shine.
2. Run a trial; print `result['reaction_state']`.
3. That value is "Fox shine ground startup" (or whatever variant fires).
4. Add to `scenario.py` as e.g. `FOX_SHINE_GROUND = 0x0...`.

Same approach to find: Fox shine air, jumpsquat (0x18), end-lag states. Build a small `scenario.FOX_STATES` dict over a few iterations of running known-correct candidates.

## 4. Candidate macros to try, in order

### Candidate A — minimum viable shine on Z (smoke test)
*Just verifies the architecture works end-to-end. Not netplay-safe yet, not jump-cancelled.*

- Hook: `0x803775C0`. Original: `lbz r0, 2(r25)` = `0x88190002`.
- Logic: if `r24 == 1` (port 2 pass) AND port 1's buttons have Z, set port 2's buttons |= B (`0x0200`) and stick Y = `0x81` (-127, max down).

Hex (with offsets computed for a 10-instruction logic body):
```
0x2C180001    # cmpwi r24, 1
0x40820024    # bne _end (skip if not port 2)
0xA019FFF4    # lhz r0, -12(r25)         ; port 1 raw buttons
0x70000010    # andi. r0, r0, 0x0010    ; mask Z
0x41820018    # beq _end (skip if Z not pressed)
0xA0190000    # lhz r0, 0(r25)          ; port 2 buttons
0x60000200    # ori r0, r0, 0x0200      ; B button
0xB0190000    # sth r0, 0(r25)
0x38000081    # li r0, 0x81             ; -127
0x98190003    # stb r0, 3(r25)          ; stick Y
# _end:
# 0x88190002 (displaced original) + branch-back appended by gecko_c2_lines
```

**Validation**:
1. Without macro: `run_z_trigger_trial` → Fox stays in Wait (or whatever).
2. With macro: `run_z_trigger_trial` → Fox transitions out of Wait on trigger frame. Record `reaction_state`.
3. Visually verify Fox is actually shining (load up Dolphin manually with the gecko installed, press Z on port 1, watch port 2 Fox).

Expected outcome: Fox shines on frame 1 of Marth's grab. NOT jump-cancelled — just grounded shine.

### Candidate B — jump-cancelled shine (the real thing)

Needs multi-frame state. Reserve a scratch slot for a per-trial counter:
- Address: `0x803FA424` (within `DEFAULT_CAVE` region, separate from Z-flag).
- Initialized to 0 by snapshot (or explicitly by macro on first trigger).

Macro behavior:
- Frame 0 (when Z press detected on port 1, counter is 0): press Y (jump) on port 2; set counter = 1.
- Frame 1-3 (counter 1-3): nothing (jumpsquat).
- Frame 4-6 (counter 4-6): press B + stick down (shine) on port 2.
- Frame 7+ (counter > 6): clear / reset.

Counter increments each frame `r24 == 1`. Trigger condition is "Marth Z pressed AND counter not currently active OR counter < some cap".

This is significantly more PowerPC than Candidate A. Build incrementally:
- B.1: just press Y once when Z detected. Verify Fox jumps.
- B.2: add frame counter, press Y on frame 0 and B+down on frame 4.
- B.3: fine-tune the frame timings until jump-cancel-shine fires cleanly.

### Candidate C — action-state-keyed (fallback if Z-press approach has issues)

- Hook: somewhere we have access to action state AFTER Marth's transition for the frame but BEFORE Fox's input is consumed.
- Read Marth's action state via global pointer (`0x80453130 → +0x2C → +0x10`).
- If `∈ {0xD4, 0xD5, 0xD6}`, force Fox's input.

Caveat: this fires *after* Marth's state has been set, which is *after* Fox's input was read for the frame → 1 frame late by construction. Useful for testing the rest of the pipeline (does forcing Fox's input cause shine?) without relying on the Z-press detection working.

### Candidate D — netplay-safe (final shipped version)

Once a candidate works offline, add:
- **Slippi online-mode check**: `lwz r7, 0x80489D30; rlwinm r7, r7, 8, 16, 31; cmpwi r7, 0x208`. Skip the modification if not online OR if local port != Fox's port. (See `Gecko_Code_Analysis.md` "L-Cancel Flash Red" example.)
- **Local-port verification**: `lwz r5, -0x49E4(r13); lbz r5, 0(r5)` → local player port. Only modify if `r24 == local_port`. Currently we hardcode r24==1 (port 2) for offline; netplay-safe should be dynamic.
- **Don't read Marth's buttons** — that's the opponent's input, and in netplay we shouldn't react based on the opponent's raw inputs (they're network-transmitted, not local). Instead, key on Marth's *action state* (action states are part of the synced game state).

The netplay-safe version is necessarily action-state-keyed (Candidate C structure), which means it's intrinsically 1 frame later than the offline Z-press version. That extra frame might or might not be acceptable for the jump-cancel-shine timing. Worth measuring.

## 5. PadStatus layout reminder

At hook `0x803775C0`, `r25` points to current port's raw `PADStatus`:
```
struct PADStatus {           // libogc / GameCube standard
    u16 buttons;     // 0x00
    s8  stickX;      // 0x02   <-- displaced original reads this byte
    s8  stickY;      // 0x03
    s8  substickX;   // 0x04
    s8  substickY;   // 0x05
    u8  triggerLeft; // 0x06
    u8  triggerRight;// 0x07
    s8  analogA;     // 0x08
    s8  analogB;     // 0x09
    s8  err;         // 0x0A
}
```

Button bitmask (raw, NOT the processed `0x804C1FAC` layout):
```
LEFT  0x0001    RIGHT 0x0002    DOWN  0x0004    UP    0x0008
Z     0x0010    R     0x0020    L     0x0040
A     0x0100    B     0x0200    X     0x0400    Y     0x0800
START 0x1000
```

The Spot_Dodge_Macro confirms: `r25 - 12` reaches the previous port's PADStatus, so PADStatus structs are 12 bytes apart in this raw buffer.

## 6. Open empirical questions (to settle by running trials)

- **Fox shine ground startup action state ID** — record from candidate A trial output.
- **Does writing `r25` PADStatus actually feed into Fox's input?** Spot_Dodge_Macro got partial success — verify shine works under our trigger.
- **Are jumpsquat frames 1-4 or 1-3?** Empirically count by recording per-frame action state under Candidate B.1 (just jump).
- **Does the engine respect raw PADStatus writes for all input types (button + analog stick)?** Spot_Dodge_Macro writes both successfully; reproduce.
- **What does Marth's action state look like one frame before and one frame after Z press?** Determines whether action-state-keyed detection is unavoidably 1 frame late.
- **The codehandler order matters** if both our test macro AND input-driver gecko hook `0x803775C0`. Verify that the input-driver runs BEFORE the test macro within a single frame's pad pass, so the Z bit is set before the test macro reads it. Otherwise we might need different hook addresses or careful order in the INI.

## 7. Validation criteria (PASS bar for each candidate)

For a candidate to be considered "Frame-1 reaction works":
1. Trial: trigger Marth Z press at frame N (via input-driver gecko + dme flag).
2. Observation: Marth's action state becomes `0xD4` (Catch) at frame N.
3. Observation: Fox's action state changes from `0x000E` (Wait) at frame N (the SAME frame, not N+1).
4. The Fox state transition is to a sensible shine/jump-into-shine state (not stumble, not random).
5. Repeating the trial: deterministic — same trigger always yields same reaction state.

For jump-cancelled shine specifically:
6. Frame N: Fox enters jumpsquat (`0x0018` or similar).
7. Frame N+4: Fox enters jumping/airborne state.
8. Frame N+7: Fox enters shine air state.

## 8. Workflow per candidate

```python
from melee_harness import Harness
from scenario import run_grab_trial, classify_trial, WAIT
# (and the soon-to-exist run_z_trigger_trial + drive_marth_z + input-driver gecko helpers)

h = Harness()

# Install the input-driver gecko (always, when testing input-keyed macros).
h.install_gecko_c2(
    name="input-driver-port1-z",
    hook_addr=0x803775C0,
    logic_words=INPUT_DRIVER_LOGIC,
    displaced_orig=0x88190002,
)

# Install the candidate macro.
h.install_gecko_c2(
    name="fox-shine-candidate-A",
    hook_addr=0x803775C0,
    logic_words=CANDIDATE_A_LOGIC,
    displaced_orig=0x88190002,
)

h.launch()
h.hook_dme()
h.seed_snapshot()

for i in range(5):
    trial = run_z_trigger_trial(h, observe_frames=15)
    result = classify_trial(trial, baseline_p2_state=WAIT)
    print(f"trial {i}: reacted={result['reacted']} "
          f"latency={result['latency_frames']} "
          f"react_state=0x{result.get('reaction_state', 0):04X}")
    for r in result['records']:
        print(f"  f={r['frame']}  p1=0x{r['p1_action']:04X}  p2=0x{r['p2_action']:04X}")
h.close()
```

When iterating on a candidate's logic: edit the `LOGIC` list (or its source if I move to `.s` files), rerun. ~12 s per iteration once the harness is warm.

## 9. Two C2 codes at the same hook — coexistence

The Slippi codehandler `HANDLE_C2` (see `Bootloader/main.asm`) builds a branch from the hook into the code body and appends a branch back. If TWO C2 codes target the same hook address, the **second one to be processed** patches the hook, displacing the first. So both codes can't naively share `0x803775C0`.

Options:
- **Chain manually**: write the input-driver as a regular C2 at `0x803775C0`. Write the test macro as a C2 at a NEARBY instruction also in the pad-process loop (e.g. one of the addresses around `0x803775XX` adjacent to but not equal to the first hook).
- **Merge into one gecko code**: one C2 at `0x803775C0` whose body does both — input drive THEN candidate reaction. Less modular but simpler to install.

Probably go with merging into one gecko code for the first few candidates. Modularize later.

## 10. When to compile via `gecko-master/`

The current `install_gecko_c2(...)` takes raw PPC instruction words. That's fine for iteration but loses readability. Once a candidate is close to final:

1. Author it as a `.s` file using `slippi-ssbm-asm-master/` conventions (`Common/Common.s` macros: `backup/restore/branchl/load`).
2. Add to a small `.json` config and run the `gecko` Go tool to compile.
3. Diff the compiled output against my hand-rolled hex to catch encoding errors.
4. Eventually ship the `.s` file (plus a one-line entry in Slippi's `netplay.json`) as the canonical artifact.

For macOS compile path: the repo ships `.exe` assemblers. macOS needs `powerpc-eabi-as` (devkitPPC). Install via `brew install gcc-arm-embedded`? No, that's ARM. For PPC: `brew install gcc-powerpc-eabi`? Verify availability. Otherwise build devkitPPC from source. Defer until needed.

## 11. Risks / things that might bite

- **Synchronization between input-driver and test-macro gecko codes** at the same hook. May need to interleave them in the INI carefully, or merge.
- **PadStatus writes might be wiped each frame** by Dolphin's emulated SI controller poll. Mitigation: the gecko runs in the pad-process loop, AFTER the poll, BEFORE the engine consumes. Spot_Dodge_Macro proves this window exists.
- **Forcing Fox's input mid-frame might not cleanly trigger shine** if the engine has already evaluated Fox's input. May need to hook even earlier in the frame. Empirical.
- **dme write contention with the input-driver flag** during the trial — if dme writes the flag at the wrong moment (e.g. mid-pad-pass), the input-driver might read inconsistent state. Mitigation: write the flag during `wait_frames(1)` boundaries, never during state observation.
- **Slippi recording / online codes may interact with our hook**. Slippi's `TriggerSendInput` at `0x80376a20-28` is right next door to `0x803775C0`. Verify no collision (they're at different addresses but in the same function).
- **`UsePanicHandlers=False` masks real errors too** — if a candidate macro corrupts state, we'd see no dialog, just weird behavior. Periodically re-enable panic handlers during macro debugging.

## 12. Order of operations checklist (start here next session)

1. Author the **input-driver gecko** PPC bytes; hand-encode and verify with `gecko_c2_lines(...)`.
2. Author **Candidate A** PPC bytes (already drafted in section 4).
3. Decide: install both as separate gecko codes OR merge into one. (Recommend: merge for first try.)
4. Update `scenario.py` with `drive_marth_z(h)` and `run_z_trigger_trial(h)`.
5. Run trial WITHOUT the candidate macro to baseline the input-driver: Marth should enter `0xD4` Catch when flag is set, Fox should stay in Wait.
6. Add the candidate macro (or use the merged version). Re-run trial.
7. Observe Fox's reaction state. Add to `scenario.FOX_STATES` table.
8. Iterate on Candidate B (jump cancel) once A works.
9. Add Slippi-online + local-port checks for Candidate D netplay-safe shipping.
10. Compile final `.s` via `gecko-master/`; vendor into the harness for repeatable installs.

## 13. Stuff in `HARNESS.md` that this builds on

- `Harness.install_gecko_c2(name, hook_addr, logic_words, displaced_orig)` — install BEFORE `launch()`.
- `gecko_c2_lines(hook, logic, displaced, name)` — exposed function that formats a C2 code as INI lines (for sanity-checking the hex blob).
- `scenario.force_action_state(h, port, state)` — direct dme write; bypasses input. Use for action-state-keyed candidates only.
- The 13 gotchas list — re-read before any new session.

## 14. Stuff NOT to do (saves time)

- Don't try to patch hooks via dme at runtime — confirmed not visible to the CPU emulator.
- Don't try to write raw PADStatus via dme at runtime as the trigger — the input poll overwrites it each frame. Use the input-driver gecko.
- Don't author macros in pure-interpreter mode just to get cleaner debugging — interpreter mode is ~5 fps, makes iteration painful, and the JIT-vs-interpreter distinction doesn't matter for boot-installed gecko codes (the codehandler flushes caches properly either way).
- Don't add a Slippi-online check before the macro itself works offline. Layer it in last.
