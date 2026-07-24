# Auto L-Cancel

Automatically L-cancels every aerial for the local player, offline and online.
Shared addresses/offsets: [`../REFERENCE.md`](../REFERENCE.md). Dev loop:
[`../../WORKFLOW.md`](../../WORKFLOW.md). Status board: [`../STATUS.md`](../STATUS.md).

## Mechanic + constants

- **Trigger window:** aerial-attack action states **`0x41`–`0x45`** (NAIR..DAIR).
- **Effect:** halves landing lag (NAIR landing 15f → ~7f).
- **Observable:** `LCancelStatus` = Player Data **`+0x25FF`** (u8: 0=none, 1=success,
  2=fail) — the direct per-landing flag; prefer it over measuring landing duration.
- **Online mechanism (v4, shipped): pulsed light ANALOG L.** Value **`0x80`** (any
  `0x50`–`0xA9` works), pulsed **every other frame** keyed to global frame counter
  (`0x80479D60`) parity. Why analog:
  - Below the `0xAA` analog→digital threshold it sets **no digital bit** and presses no Z,
    so it physically **cannot airdodge or re-nair** (the airborne trigger check reads the
    digital L/R timer `+0x680`) — trailing-spill misfires are impossible by construction.
  - Global-parity cadence keeps ticking through **hitlag** (the action-state frame counter
    `+0x894` freezes in hitlag), so hit aerials still cancel.
  - Held analog does NOT L-cancel — the window needs a rising edge, hence the pulse.
- **Offline mechanism (`auto_lcancel/`):** digital **L** (`0x40`) pressed once per
  7-frame cycle (counter byte at `0x803FA470`), keeping the 7-frame window always armed.

## Hook / cave logic

- **Online (shipped):** producer-side hook **`0x8034E680`** — inside PAD_Read right after
  the analog L byte `6(r4)` is finalized, upstream of the EXI scrape → netplay-safe.
  Displaced original `lbz r0,7(r3)` = `0x88030007`; preserve r3/r4/r13. Cave: resolve the
  local player via ODB (`port = *(*(r13-0x49E4)+0)`, GObj `0x80453130 + port*0xE90`,
  Player Data `+0x2C`, every pointer MEM1-checked), gate on state `0x41..0x45`, and on
  even global frames `stb 0x80, 6(r4)`.
- **Offline:** consumer-side hook `0x803775B8` (fine offline, **desyncs online**), P2
  port gate, ORs `0x40` into the pad buttons halfword on cycle-frame 0.

## Coexistence rule (critical — deploy-time)

**Never enable the standalone L-cancel gecko together with the wavedash gecko.** Both
hook the same instruction `0x8034E680`; the second branch installed clobbers the first.
Since 2026-06-05 the analog-L pulse is **folded into the wavedash stick cave**
(`make_wavedash_gecko.py`, `INCLUDE_LCANCEL=True`) — disjoint states (aerials vs
KneeBend) and disjoint bytes (`6(r4)` vs `2/3(r4)`), zero conflict, validated live
(19/0 L-cancels alongside 12 wavedashes in one match). The wavedash gecko already
contains the L-cancel; the standalone `online_auto_lcancel.gecko.txt` is only for
L-cancel-without-wavedash setups.

**OFFLINE scratch overlap too:** the offline `auto_lcancel/` cycle counter and the offline
wavedash `WD_PEND` latch both use scratch `0x803FA470` — don't install both offline macros
in the same session either.

History: v1–v3 pulsed digital **Z** (global `%7`, then action-state-anchored cadence);
retired because `+0x894` freezes in hitlag and digital edges could airdodge/re-nair.
The digital-Z generator and online dev rigs were deleted from HEAD 2026-07-24
(recoverable from git history); narrative in `docs/archive/`.

## Files

| File | Role |
| --- | --- |
| `online_auto_lcancel.gecko.txt` | **Shipped online gecko** (v4 analog-L pulse). |
| `make_online_analog_lcancel_gecko.py` | Generator (keystone build + capstone verify). |
| `auto_lcancel/auto_lcancel.py` | **Shipped offline macro** (runtime install via meta-flush). |
| `auto_lcancel/play_auto_lcancel.py` | Offline live-play launcher. |
| `auto_lcancel/test_l_timer_invariant.py`, `test_fox_aerials.py`, `lcancel_rig.py` | Offline verify suite (`[PASS]`/`[FAIL]`). |
| `auto_lcancel/README.md`, `notes.md`, `LESSONS.md` | Offline build docs. |

The online self-drive/hitlag test rigs and the disasm probe that found the `6(r4)`
injection point were removed in the 2026-07-24 cleanup (git history).

## Current status

**Offline: SHIPPED** (`auto_lcancel/`). **Online: SHIPPED** — analog-L v4
(`make_online_analog_lcancel_gecko.py` → `online_auto_lcancel.gecko.txt`), validated:
offline 15f→~7f with zero misfires; online self-drive 14.8f→7.1f, `LCancelStatus` 15/15,
no desync; hitlag test 10/10 hit-aerials cancelled. Also **folded into the wavedash
gecko's `0x8034E680` cave** (2026-06-05) — see the coexistence rule above.

## Open items

None blocking. Air-ending-aerial spill is solved by construction (analog L can't
airdodge) but was never exercised by the self-drive, which always lands its nairs —
optionally confirm in real free play.
