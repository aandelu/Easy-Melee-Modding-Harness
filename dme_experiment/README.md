# dme_experiment

Pure-dme reproductions of the findings the previous sessions made via gecko codes.
No gecko codes installed; no PowerPC assembled. Just dme.read/dme.write into a
running Slippi Dolphin from Python.

Goal: see how much of the canonical JC-shine + Frame-1 Marth->Fox reaction we
can reproduce without any code injection. Each experiment file is a one-shot
launchable script that documents its own findings inline at the bottom.

Scripts (in dependency order):
- `helpers.py` — shared dme helpers building on `melee_harness.Harness`.
- `exp01_action_state_direct.py` — confirm/refute the sheet's claim that
  writing PD+0x10 alone does nothing.
- `exp02_button_planes.py` — probe which "button plane" (PD+0x65C global
  0x804C1FAC, raw PADStatus...) Fox's engine logic actually reacts to when
  we write from dme.
- `exp03_find_padstatus.py` — try to make Fox shine using only dme writes.
- `exp04_brute_force_inputs.py` — try to reproduce the "3 frames KneeBend"
  finding via dme button writes.
- `exp05_dme_jc_shine.py` — try a JC-shine via dme writes only.
- `exp06_reactive_jc_shine.py` — poll Marth's action state, react with
  Fox's shine input as soon as Catch (0xD4) is observed.

Each script writes per-frame logs into a sibling `runs/` folder for offline
analysis. Findings get appended to `FINDINGS.md` as they accumulate.
