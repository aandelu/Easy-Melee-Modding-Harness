# Easy Melee Modding Harness

**Easy Melee** makes Super Smash Bros. Melee (v1.02 NTSC) easier for beginners by unlocking
complicated tech skill: each macro is a **Slippi gecko code** that performs a technique
frame-perfectly from simple controller input — offline and online (netplay-safe, no desyncs).

Shipped so far: frame-1 **JC-shine** reaction, automatic **L-cancel** (offline + online),
**up-bound wavedash** (offline + online), and a WIP **dash-back reversal** — see
[`docs/STATUS.md`](docs/STATUS.md) for the live state board.

This repo is also the thing that *builds* them: an agent-operated development harness.
Describe the technique you want; the agent uses `dolphin-memory-engine` to launch Slippi
Dolphin, inject candidate PowerPC code, set software breakpoints, drive scenarios, observe
frame-by-frame results, and even auto-drive a second Windows machine for live netplay tests.
Output: a gecko code you paste into Slippi.

## Where to start

| You are… | Read |
| --- | --- |
| An agent (or human) picking up work | [`CLAUDE.md`](CLAUDE.md) → [`docs/STATUS.md`](docs/STATUS.md) |
| Setting up the harness on a machine | [`HARNESS.md`](HARNESS.md) §9 |
| Developing a macro | [`WORKFLOW.md`](WORKFLOW.md) |
| Looking up any address/hook/rule | [`docs/REFERENCE.md`](docs/REFERENCE.md) |

## Quick health check

```bash
python3 verify_savestate.py     # launches Dolphin, ~12s, prints [PASS]/[FAIL]
```

## Platform requirements

macOS, **SIP disabled** (dme needs `task_for_pid`), Slippi Launcher, a `Dolphin` hardlink
next to the real binary (see HARNESS.md §9 — Slippi updates wipe it), Accessibility permission
for synthetic keystrokes, Python 3.13 with `dolphin-memory-engine`, `pyobjc`, `capstone`, `keystone-engine`.

Credits: address research in [`SSBM memory address sheet/`](SSBM%20memory%20address%20sheet/) is
the community's work; `vendor/slippi-ssbm-asm-master/` is the Slippi Online mod this project
builds on top of; `vendor/gecko-master/` is the gecko assembler source.
