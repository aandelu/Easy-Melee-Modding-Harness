# auto_lcancel — automatic L-cancel macro

A macro that re-presses L every other frame while the player is in an
aerial attack state, so the 7-frame L-cancel window is always active when
they land. Eliminates the timing requirement.

Kicked off 2026-05-17. Offline / dev-only first; built with controller
writes (pad-buffer modification at the PadRead hook) so the macro can later
port to Slippi online without desync.

## Files

| File | Purpose |
| --- | --- |
| `auto_lcancel.py` | **The shipped macro.** Module-level constants + `install(harness, port=2)`. Hooks `HSD_PadRead` at `0x803775B8` and toggles the L bit in aerial states 0x41–0x45 on every even frame. Assembled with keystone — no hand-encoded branches. |
| `play_auto_lcancel.py` | Live-play driver: launches Dolphin, loads slot 2, installs the macro, then idles so you can play P2 (Fox). Ctrl-C to exit. |
| `lcancel_rig.py` | **The verify.** Installs a combined driver-and-L-toggle hook, runs two trials (L disabled vs enabled), measures landing-state duration in each, and emits `[PASS]`/`[FAIL]` based on the duration reduction. |
| `discover_lcancel.py` | Earliest probe (historical). Tested 0x680 timer semantics and confirmed dme controller writes don't propagate — see LESSONS.md #1. Keep for reference. |
| `notes.md` | Mechanic + addresses, updated with empirical findings. |
| `LESSONS.md` | **Read this first** if you're an agent picking this up. Documents the time-sinks from the 2026-05-17 build session. |

## Status

- [x] Research: 0x680 (L/R press timer) and aerial states identified.
- [x] Discovery: confirmed controller-region dme writes don't propagate; landing-duration is the real L-cancel observable (not the CSV-documented 0x2354 divisor).
- [x] Macro shipped: `auto_lcancel.install()`.
- [x] Verify: `lcancel_rig.py` -- two-trial test, measures landing duration, PASS if reduction ≥ 40%.

## How to run

All scripts need keystone's dylib path set on macOS:

```bash
# Verify the macro actually triggers L-cancel (auto-driven, ~30 s):
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/lcancel_rig.py

# Live play: drives Fox manually on your controller, macro handles L:
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 auto_lcancel/play_auto_lcancel.py
```

The verify expects:
- Trial 1 (L disabled): Fox's NAIR landing-state lasts ~15 frames.
- Trial 2 (L enabled):  Fox's NAIR landing-state lasts ~7 frames.
- Ratio ≤ 0.6 → `[PASS]`.

## Architecture in one paragraph

Runtime path only — no per-iteration boot-time gecko. The meta-flush gecko
(`instr_writer.install_meta_flush`) is the ONLY boot-time install; it
provides the `dcbf/icbi/isync` primitive that lets dme write fresh PPC
instructions anywhere in MEM1 and have the emulated CPU observe them. We
use it to install a `b cave` at `0x803775B8` (the per-frame PadRead point)
that runs our logic. The macro reads Fox's action state from
`*(0x80453FC0 + 0x2C) + 0x10`, checks if it's in `[0x41..0x45]`, and if so
ORs `0x40` into the buttons halfword at `(r25)` on POWERON_COUNT-even
frames. The engine's "Frames Since L/R Pressed" counter at Player Data
`+0x680` resets every other frame as a result, and every landing during an
aerial sees `≤ 1` there — well inside the 7-frame window.

## Online portability note

The macro writes only the pad-buffer halfword at `(r25)` — what Slippi
rebroadcasts. No writes to action state, L-cancel timer, or landing-lag
fields. When porting online, add the netplay-safety scene + local-port
gating pattern from `candidate_d_standalone_v2.py` (compare `0x80489D30`
to `0x208`, gate on `r13 - 0x49E4`) and rebuild as a boot-time gecko via
`Harness.install_gecko_c2` since the runtime meta-flush path isn't
appropriate for live online play.
