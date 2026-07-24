# JC-Shine — Frame-1 reaction macro

Fox (P2) executes a jump-cancelled shine on the same frame Marth (P1) starts a grab.
The original project deliverable. Shared addresses/offsets: [`../REFERENCE.md`](../REFERENCE.md).
Dev loop: [`../../WORKFLOW.md`](../../WORKFLOW.md). Status board: [`../STATUS.md`](../STATUS.md).

## Mechanic + constants

- **Trigger:** Marth (P1) enters any grab startup — Catch `0xD4`, CatchDash `0xD5`,
  CatchTurn `0xD6`. Read via the P1 GObj chain: `*(0x80453130)` → `+0x2C` (Player Data)
  → action-state halfword at `+0x12`.
- **Response:** a buffered jump-cancel shine on Fox (P2). A 6-step state machine keyed
  off a scratch counter byte at **`0x803FA424`**:
  - counter 0 + Marth in grab → arm (counter=1) and press **Y** (`0x800`) — jump, same frame.
  - counter 1–2 → increment only (Fox's jumpsquat frames).
  - counter 3–5 → press **B** (`0x200`) + **stickY = −127** (`0x81`) — the buffered
    down-B shine that cancels the jumpsquat.
  - counter ≥6 → terminal; the standalone v2 resets the counter to 0 once Marth leaves
    the grab states, so it re-fires on every grab.
- **Port gate:** hardcoded `cmpwi r24, 1` — acts only on the P2 pad pass.

## Hook / cave logic

Single hook at the consumer-side per-frame pad read **`0x803775B8`** (`HSD_PadRead`;
displaced original `lhz r0, 0(r25)` = `0xA0190000`). On entry `r24` = 0-indexed port,
`r25` = pad struct (buttons at `0(r25)`, stickY at `3(r25)`). The logic ORs button bits
into the pad halfword and stores the stick byte — controller-data writes only, no
action-state forcing.

v2's distinguishing feature is **MEM1 range checks on every pointer**: after loading the
P1 GObj and Player Data pointers it `srwi`-extracts the top byte and requires `0x80`
before dereferencing. On menus/scene transitions the GObj slot can hold non-NULL garbage;
v1 NULL-checked only and produced Dolphin "Invalid read" panics. LOGIC is 50 instructions;
ships as a boot-time C2 gecko (`gecko_c2_lines` packaging, which reserves the throwaway
last word the codehandler overwrites).

History: D.1 "never fired" traced to a single mis-encoded instruction (`lbz r9,0(r11)`
encoded as `lbz r11,0(r11)`); D.2 fixed it, v2 added the MEM1 pointer checks.
Full narrative: `docs/archive/`.

## Files

| File | Role |
| --- | --- |
| `candidate_d_standalone_v2.py` | **Shipped macro.** Self-resetting, menu-safe. Paste into a Slippi user dir to use. |
| `candidate_d2.py` | Same state machine packaged for harness-driven play. |
| `play_d2.py` | Live play: user controls Marth on P1, gecko auto-JC-shines Fox on P2. |
| `verify_d_standalone_v2.py` | Smoke test: reproduces the JC-shine on the slot-2 savestate (~15 s, `[PASS]`/`[FAIL]`). |
| `verify_d2.py` | Verify for the harness-packaged variant. |
| `verify_v2_with_keystone.py` | Keystone cross-check of the encoded body. |

## Current status

**OFFLINE SHIPPED.** `candidate_d_standalone_v2.py` is the deliverable;
`verify_d_standalone_v2.py` passes; live play via `candidate_d2.py` + `play_d2.py`.
It is **labeled netplay-safe by design** (the scene+port gating pattern, per
`docs/archive/Gecko_Code_Analysis.md`) but has **NEVER been verified in a live online match** —
that is the open item.

## Open items

1. **One live online validation match.** The netplay-safe label is a design claim, not a
   measured result. Two specific things to check before trusting it online:
   - the hook `0x803775B8` was later established (L-cancel work) to be the **consumer-side**
     pad read, and consumer-side input edits **desync** netplay — the online-safe producer
     hooks are `0x8034E2AC`/`0x8034E680` (see [`../REFERENCE.md`](../REFERENCE.md));
   - the shipped body carries the hardcoded P2 port gate, not the ODB local-port resolve
     (`*(*(r13-0x49E4)+0)`) the later online macros use.
   If the live test desyncs, the fix path is a producer-side re-port following the
   wavedash/L-cancel pattern.
