#!/usr/bin/env bash
# Smoke + bring-up driver for the Melee modding harness (macOS-ONLY).
#
# Verifies prerequisites, then runs the canonical harness bring-up via
# verify_savestate.py: launch Slippi Dolphin -> hook dme -> auto-load savestate
# slot 2 -> observe game state (Marth P1 / Fox P2) -> snapshot + restore MEM1.
# The "observation" is dme memory reads (entity pointers, char ids, action
# states, the frame counter) -- this harness has no screen-scrape path; dme IS
# the handle a driving agent uses to see/poke the running game.
#
# Run from the repo root:
#   bash .claude/skills/run-melee-harness/smoke.sh
# Exit code is verify_savestate.py's: 0 on [PASS], non-zero on [FAIL].
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export DYLD_LIBRARY_PATH="${DYLD_LIBRARY_PATH:-/opt/homebrew/lib}"   # keystone needs this on this machine

echo "=== prerequisites ==="
csrutil status 2>/dev/null | grep -qi disabled \
  && echo "  [ok] SIP disabled (dme can task_for_pid Dolphin)" \
  || echo "  [!!] SIP NOT disabled -> EVERY dme read/write will fail. Disable in Recovery."

python3 - <<'PY'
import os, sys
try:
    import dolphin_memory_engine, keystone, capstone  # noqa: F401
    print("  [ok] python deps: dolphin_memory_engine + keystone + capstone")
except Exception as e:
    print(f"  [!!] python deps missing ({e}); pip install dolphin-memory-engine keystone-engine capstone")
    sys.exit(0)
import melee_harness as m
hl = m.DOLPHIN_HARDLINK
sib = os.path.join(os.path.dirname(hl), "Slippi Dolphin")
ok = os.path.exists(hl) and os.path.exists(sib) and os.stat(hl).st_ino == os.stat(sib).st_ino
print(f"  [{'ok' if ok else '!!'}] Dolphin hardlink "
      + ("inodes match" if ok else "MISSING/mismatch -> `ln \"Slippi Dolphin\" Dolphin` (see SKILL.md)"))
print(f"  [{'ok' if os.path.exists(m.ISO_PATH) else '!!'}] Melee ISO: {m.ISO_PATH}")
PY

echo "=== kill stale Dolphin (dme hooks by process name 'Dolphin'; a stale one mis-attaches) ==="
pkill -9 -x Dolphin 2>/dev/null || true
python3 - <<'PY'
import time, subprocess
for _ in range(25):
    if not subprocess.run(["pgrep", "-x", "Dolphin"], capture_output=True, text=True).stdout.strip():
        break
    time.sleep(0.3)
print("  stale gone")
PY

echo "=== canonical bring-up: verify_savestate.py (launch + dme hook + slot-2 + observe + restore) ==="
exec python3 verify_savestate.py
