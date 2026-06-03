# auto_lcancel — automatic L-cancel macro

> **The macro is `auto_lcancel.py`. Everything else in this folder is tests,
> probes, or scanners used to build/verify it. The live-play launcher is
> `play_auto_lcancel.py`.**

A macro that presses L on a 7-frame cycle (1 press, 6 release) while the
player is in an aerial-attack state, so the 7-frame L-cancel window is
always active when they land. Eliminates the timing requirement on the
player.

Kicked off 2026-05-17. Offline / dev-only first; built with controller
writes (pad-buffer modification at the PadRead hook) so the macro can later
port to Slippi online without desync.

## Files

### Production (what you use)

| File | Purpose |
| --- | --- |
| `auto_lcancel.py` | **THE MACRO.** Module-level constants + `install(harness, port=2)`. Hooks `HSD_PadRead` at `0x803775B8` and presses L once every 7 frames in aerial states 0x41–0x45. Assembled with keystone. |
| `play_auto_lcancel.py` | **Live-play launcher.** Launches Dolphin, loads slot 2, installs the macro, then idles so you can play P2 (Fox). Ctrl-C to exit. |

### Tests (verify the macro works)

Listed newest → oldest. Run the top one if you just want to confirm everything's fine.

| File | What it proves |
| --- | --- |
| `test_l_timer_invariant.py` | **Universal-ish proof.** 13 NAIR trials with varying fast-fall delays. Samples `Char Data + 0x680` every aerial frame; confirms steady-state cycle is `0,1,2,3,4,5,6,0,...` (max = 6) and landing-frame value ≤ 5 in every trial. (Added 2026-05-19.) |
| `test_fox_aerials.py` | **Comprehensive aerial sweep.** 20 trials = 5 aerials × short/full hop × L off/cycle-7. Confirms 0 airdodges. |
| `lcancel_rig.py` | **Earliest verify.** 4 trials = short/full hop × L off/cycle-7. Measures NAIR landing-state duration reduction. Historical — superseded by `test_fox_aerials.py`. |

### Probes & scanners (historical, kept for reference)

| File | Purpose |
| --- | --- |
| `discover_lcancel.py` | Round-2 probe that proved dme writes to `0x804C1FAC` don't propagate (Dolphin's input pipeline clobbers them). See LESSONS.md #1. |
| `find_lcancel_check.py` | Capstone-based MEM1 scanner that located the engine's L-cancel check function at `0x8008E4A8`. Re-runnable if you want to verify the check moved. |

### Docs

| File | Purpose |
| --- | --- |
| `README.md` | This file. |
| `notes.md` | Mechanic + addresses + per-aerial baseline lag table + invariant-test results. |
| `LESSONS.md` | **Read this first** if you're an agent picking this up. 11 numbered time-sinks from the 2026-05-17 build session. |

## How to run

All scripts need keystone's dylib path set on macOS:

```bash
# Strongest verify: 13-trial fast-fall sweep, samples 0x680 every aerial frame (~60 s).
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/test_l_timer_invariant.py

# Comprehensive: every aerial in short/full hop, checks for airdodges (~90 s).
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/test_fox_aerials.py

# Live play: drives Fox manually on your controller, macro handles L.
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/play_auto_lcancel.py
```

Each test exits 0 on `[PASS]`, non-zero on `[FAIL]`.

## Architecture in one paragraph

Runtime install only — no per-iteration boot-time gecko. The meta-flush
gecko (`instr_writer.install_meta_flush`) is the only boot-time install;
it provides the `dcbf/icbi/isync` primitive that lets dme write fresh PPC
instructions anywhere in MEM1 and have the emulated CPU observe them. We
use it to install a `b cave` at `0x803775B8` (the per-frame PadRead point)
that runs our logic. The macro reads the target port's action state from
`*(GObj + 0x2C) + 0x10`, checks if it's in `[0x41..0x45]`, and if so
maintains a cycle-7 counter at `0x803FA470`: counter==0 → OR `0x40` into
the pad buttons halfword at `(r25)` (press L); 1..6 → don't touch the
pad; wrap. On non-aerial frames the counter resets to 0 so the next aerial
entry starts on a press frame. The engine's "Frames Since L/R Pressed"
field at Player Data `+0x680` cycles through `0..6` as a result —
strictly less than the 7-frame window (`bgt` at `0x8008E4CC`).

## Online portability note

The macro writes only the pad-buffer halfword at `(r25)` — what Slippi
rebroadcasts. No writes to action state, L-cancel timer, or landing-lag
fields. To port online you'd:

1. Replace the hardcoded port gate (`cmpwi r24, 1`) with `lwz rX, -0x49E4(r13); cmpw r24, rX` — Slippi's documented local-port read.
2. Add the scene check `*0x80489D30 == 0x208` so the macro only fires inside a Slippi online match.
3. Repackage as a boot-time C2 gecko via `Harness.install_gecko_c2`, modeled after `candidate_d_standalone_v2.py`. The runtime meta-flush path isn't appropriate for shipped online play.
