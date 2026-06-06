# Windows netplay-peer setup — handoff notes

**Date:** 2026-06-06
**Box:** `DESKTOP-RU18U2Q`, user `esash` (Administrator, Microsoft account)
**Peer files:** `C:\Users\esash\Desktop\melee 2\peer\`
**Status:** ✅ Fully set up and verified **end-to-end** — the Mac launched Slippi
and connected over SSH with zero physical interaction on the Windows box.

This documents what was configured, the bugs fixed, the code added, and what the
Mac side still needs to do. Written for the main (Mac-side) agent.

---

## 1. End-to-end result

`ssh winbox schtasks /run /tn MeleePeer_Enter` from the Mac → Slippi launched,
focused, F1 loaded the slot-1 direct-connect savestate, Enter connected. Confirmed
visually on the Windows screen, hands-off. The full `SETUP_WINDOWS.md` chain works.

---

## 2. Resolved per-machine config (`win_paths.py`)

All four unknowns were probed (`python melee_peer.py debug`) and verified live:

| Constant | Value | Verified |
|---|---|---|
| `SLIPPI_EXE` | `C:\Users\esash\AppData\Roaming\Slippi Launcher\netplay\Slippi Dolphin.exe` | exists ✓ |
| `PROCESS_NAME` | `Slippi Dolphin.exe` | matches tasklist ✓ |
| `ISO_PATH` | `C:\Andrew generated\melee\Super Smash Bros. Melee (USA) (En,Ja) (v1.02).iso` | exists ✓ |
| `WINDOW_TITLE_SUBSTR` | `Faster Melee` | matches window ✓ |
| `USER_DIR` | `""` (default) | — |

⚠️ **`WINDOW_TITLE_SUBSTR` was wrong in the template** (`"Dolphin"`). This Slippi
build's game window is titled **`Faster Melee - Slippi (3.6.2)`** — no "Dolphin"
substring, so window discovery returned `None` and `enter` could never focus the
window. Changed to `"Faster Melee"` (deliberately not `"Slippi"`, which would also
match the separate `Slippi Launcher` window).

---

## 3. Bugs found & fixed in `melee_peer.py`

Three real defects, all fixed and in the code now:

1. **`INPUT` struct was the wrong size on 64-bit → every keystroke rejected.**
   The ctypes `INPUT` union only declared its `KEYBDINPUT` member, making
   `sizeof(INPUT) = 32`. Win32 `SendInput` requires `cbSize == 40` on x64 and
   rejected all input with `ERROR_INVALID_PARAMETER (87)`. **No F1/Enter would
   ever have worked.** Fixed by adding the `MOUSEINPUT` member to the union
   (the union's true largest member) so `sizeof(INPUT)` is 40 (x64) / 28 (x86).
   Also pinned `SendInput.argtypes`/`restype`.

2. **`_log` crashed on non-cp1252 window titles.** `debug` (and any run that logs
   a Unicode window title) hit `UnicodeEncodeError` when `print()` went to the
   cp1252 console. The file write was already UTF-8; wrapped the console `print`
   in a fallback that emits a lossy version instead of crashing.

3. **Keystroke failures were silent in the exit code.** `_send_scancode` only
   logged a WARNING on a `SendInput` failure; `enter()` still returned normally
   and the process exited 0. This meant any success signal (exit code / status
   file) would *falsely report success* on a real keystroke failure. Fixed:
   `_send_scancode` now returns a bool, and `enter()` raises if either F1 or
   Enter is rejected, so the exit code and status file are truthful.

---

## 4. Code added

### `caffeinate.py` (new)
On-demand keep-awake using `SetThreadExecutionState(ES_CONTINUOUS |
ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)`. Run it at the start of a session and
leave the window open; it prevents sleep + screensaver (so the desktop never
auto-locks and SendInput always lands), and **restores normal power behavior the
moment it's closed**. Changes no settings and does not disable the login PIN.

This was a deliberate choice over permanently disabling sleep/lock: the user
keeps normal power + PIN security and only "caffeinates" during testing sessions.

### `peer_status.json` machine-readable result (new, in `melee_peer.py`)
Every command now writes `peer_status.json` (atomically) with the outcome of the
last run, so the Mac can confirm success **without inferring it from the game**:

```json
{"cmd":"enter","ok":true,"error":null,
 "detail":{"focused":true,"f1_sent":true,"enter_sent":true},
 "time":"2026-06-06 09:27:07","epoch":1780763227}
```

`ok` is `false` (and exit code nonzero) on: no Dolphin window, focus failure, or
a rejected keystroke. `epoch` lets the Mac confirm it's reading a **fresh** result.

---

## 5. SSH / auth specifics (important for the Mac side)

- **OpenSSH Server** installed and running (`sshd`, StartupType Automatic). Only
  inbound TCP 22 required; the installer added the firewall rule.
- **The account is an Administrator**, so Windows OpenSSH ignores the per-user
  `C:\Users\esash\.ssh\authorized_keys` and **only** reads
  `C:\ProgramData\ssh\administrators_authorized_keys` (the `Match Group
  administrators` block in `sshd_config`). The Mac's key lives there with locked
  ACLs (`SYSTEM:F` + `BUILTIN\Administrators:F`, inheritance removed).
- **Key:** the Mac's original `~/.ssh/id_ed25519` had a **forgotten passphrase**
  (unrecoverable), which would have blocked non-interactive `BatchMode` SSH. We
  generated a **dedicated passphrase-free key** `~/.ssh/id_winbox` and installed
  its public half on Windows.
- **Mac `~/.ssh/config`** has:
  ```
  Host winbox
      HostName 192.168.68.87
      User esash
      IdentityFile ~/.ssh/id_winbox
      IdentitiesOnly yes
  ```
- **Confirmed:** `ssh winbox echo ok` → `ok`, passwordless.

⚠️ **`192.168.68.87` is a DHCP lease.** Reserve it in the router or the Mac's
`HostName` will eventually go stale.

---

## 6. Scheduled Tasks

Four interactive tasks registered (LogonType **Interactive**, RunLevel
**Limited** = the doc's `/it /rl LIMITED`), all `Ready`:
`MeleePeer_Ensure`, `MeleePeer_Enter`, `MeleePeer_Kill`, `MeleePeer_Restart`.
They run `pythonw "...\melee_peer.py" <cmd>`. Verified a task-triggered cold
launch delivers keystrokes in the interactive session.

---

## 7. Operating procedure (each session)

1. Log into Windows normally (PIN — auto-login intentionally **not** set up).
2. Run `peer\caffeinate.py`, leave the window open.
3. Drive from the Mac: `ssh winbox schtasks /run /tn MeleePeer_Enter`
   (or `h.enter_online(peer=Peer())`). Close caffeinate when done.

---

## 8. Recommended Mac-side integration

`schtasks /run` is async, so confirm success by polling for a **fresh**
`peer_status.json` (compare `epoch` to the previous value to avoid clock skew):

```python
import json, time, subprocess
STATUS = r'C:\Users\esash\Desktop\melee 2\peer\peer_status.json'

def _ssh(host, cmd):
    return subprocess.run(["ssh", host, cmd], capture_output=True, text=True).stdout

def _status(host):
    try: return json.loads(_ssh(host, f'type "{STATUS}"'))
    except Exception: return None

def enter(host, timeout=40):
    before = (_status(host) or {}).get("epoch")
    _ssh(host, "schtasks /run /tn MeleePeer_Enter")
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _status(host)
        if st and st.get("cmd") == "enter" and st.get("epoch") != before:
            return bool(st.get("ok")), st
        time.sleep(1.0)
    return False, None
```

**Use two signals, not one:**
- `peer_status.json ok` = "the Windows peer did its part" (catches locked
  desktop, crashed Slippi, keystroke rejection — the *fixable* failures).
- The Mac's own game **scene** (`0x0208` online match, already read by the
  harness) = "we're actually in a match."

If the peer reports `ok:true` but the scene never advances → the problem is the
savestate/timing/network, not the peer plumbing. That's the diagnostic split the
`SETUP_WINDOWS.md` troubleshooting section was previously guessing at.

---

## 9. Outstanding / not done here

- **Mac `peer.py`**: set `PEER_SSH_HOST = "winbox"`; optionally fold in the
  status-polling above. (Not in this repo's `peer/` folder — Mac side.)
- **`verify_peer.py`**: referenced by `SETUP_WINDOWS.md` but not present in
  `peer/`. The manual `schtasks` test is its equivalent; worth writing a real
  one on the Mac.
- **`SETUP_WINDOWS.md`**: not yet updated to mention `caffeinate.py`,
  `peer_status.json`, or the `WINDOW_TITLE_SUBSTR = "Faster Melee"` correction.
- **DHCP reservation** for `192.168.68.87` (see §5).
- **Auto-login**: intentionally skipped (manual PIN after reboot is acceptable).
  The `netplwiz` checkbox is hidden on this MS account; revealing it requires
  turning off Settings ▸ Accounts ▸ Sign-in options ▸ "only allow Windows Hello
  sign-in…" if ever wanted.
- **`PostMessage(WM_KEYDOWN)` fallback**: not needed — `SendInput` works once the
  struct size was fixed.
