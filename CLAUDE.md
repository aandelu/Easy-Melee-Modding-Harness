# CLAUDE.md — Easy Melee Modding Harness

**Easy Melee** makes Super Smash Bros. Melee (NTSC 1.02) easier for beginners by automating
complicated tech skill: each deliverable is a **Slippi gecko code** that performs a technique
(JC-shine, L-cancel, dash-back reversal, wavedash) from simple controller input — offline, and
online (netplay-safe) for the macros `docs/STATUS.md` marks online-shipped. This repo is the development harness: an all-`dme`
(dolphin-memory-engine) closed loop that launches Slippi Dolphin, injects candidate PPC code,
drives scenarios, and observes results — plus a software debugger (breakpoints) and an
autonomous Windows netplay peer, so agents can build and test macros end-to-end on their own.

## Route yourself

| You need | Read |
| --- | --- |
| **Current state / what to do next** | [`docs/STATUS.md`](docs/STATUS.md) — the state board. Start here every session. |
| Any address, offset, hook, rule, or trap | [`docs/REFERENCE.md`](docs/REFERENCE.md) — every stable fact, stated once. The CSVs in [`SSBM memory address sheet/`](SSBM%20memory%20address%20sheet/) are the upstream authority. |
| How to discover / iterate / ship a macro | [`WORKFLOW.md`](WORKFLOW.md) — the one dev-loop doc (offline, online, shipping). |
| Harness architecture & API | [`HARNESS.md`](HARNESS.md) (setup checklist in §9). |
| A specific macro's design & open items | `docs/macros/<name>.md` (jc_shine, lcancel, cactuar_dash, wavedash). |
| The Windows peer (online test automation) | [`peer/SETUP_WINDOWS.md`](peer/SETUP_WINDOWS.md). |
| Slippi online internals (the mod we build on) | `vendor/slippi-ssbm-asm-master/` — `Common/Common.s` is authoritative for Slippi addresses. |
| History / superseded plans / session logs | `docs/archive/` (all banner-marked HISTORICAL — do not treat as current). |

## The five fatal gotchas

1. **macOS SIP must be disabled** and Dolphin launched via a hardlink named `Dolphin`
   (Slippi updates wipe the hardlink — recreate it, see HARNESS.md §9). Stale/zombie
   `Dolphin` processes steal the dme attach: `pkill -9 -x Dolphin` and poll `pgrep` until empty.
2. **`0x80453130` is a GObj pointer, not Player Data** — Player Data is `*(GObj + 0x2C)`.
   Use `Harness.player_data_ptr(port)`.
3. **Netplay safety = producer-side only.** Online input edits go inside PAD_Read
   (`0x8034E2AC` digital / `0x8034E680` analog+stick); the offline hook `0x803775B8` desyncs online.
4. **Savestate loads wipe runtime code patches** (boot geckos survive; `write_instrs` patches
   don't). Install runtime patches after `seed_snapshot()`; online, bake geckos into the slot-4 savestate.
5. **Never hand-trust PPC hex.** Keystone-assemble + capstone-verify before Dolphin ever
   runs it — `gecko_tools.assemble_and_verify` for new payloads (existing `make_*_gecko.py`
   inline the same check). Hand-counted branches and the C2-codehandler-eats-last-word bug
   are the #1 historical time sinks.

## Common commands

```bash
python3 verify_savestate.py     # harness alive (~12s). Then: verify_inject_gecko,
                                # verify_meta_flush, verify_bp, verify_scenario, verify_peer
python3 verify_d_standalone_v2.py   # shipped JC-shine reproduces on the savestate
python3 play_d2.py                  # live play: you drive Marth, gecko shines Fox
pkill -9 -x Dolphin                 # if Dolphin wedges
```

Machine paths live at the top of `melee_harness.py` (`DOLPHIN_HARDLINK`, `ISO_PATH`, `USER_DIR`).

## Rules

- Search `SSBM memory address sheet/*.csv` before declaring anything undocumented; trust
  empirical measurement over CSV prose when they conflict.
- When a macro ships or changes state, update `docs/STATUS.md` — it is the only status ledger.
- Hex literals `0x...`; PPC words as big-endian ints in Python lists.
- Don't modify `vendor/`.
