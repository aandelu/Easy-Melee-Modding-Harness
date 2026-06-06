# Windows netplay-peer — one-time setup

Goal: let the Mac harness drive the Windows netplay peer with **zero physical
interaction** — `Harness.enter_online(peer=Peer())` triggers Slippi launch + F1
(load slot-1 direct-connect savestate) + Enter (connect) on this box over SSH.

You do this **once**. After that the Windows box just needs to be powered on,
logged in, and unlocked; the Mac does the rest.

> **Status: verified end-to-end (2026-06-06)** on a real two-machine setup — the
> Mac launched Slippi and connected with zero physical interaction on the Windows
> box. The committed `peer/` files include the bug-fixed `melee_peer.py` and the
> verified `win_paths.py`; see `HANDOFF_WINDOWS_PEER.md` for the original
> bring-up notes.

## Why it's built this way (read this first)

Synthetic keystrokes (`SendInput`) reach Dolphin **only from the interactive
desktop session**. A process spawned directly by OpenSSH runs in a
non-interactive session (Session 0), so its keystrokes go nowhere. The bridge:
the Mac SSHes in and runs `schtasks /run` to trigger a **Scheduled Task set to
"run only when the user is logged on"**, which executes `melee_peer.py` in the
interactive session — where the keystrokes land. Two consequences:

- The Windows user must be **logged in and the desktop UNLOCKED** (a locked
  secure-desktop blocks `SendInput`). Disable the lock screen / screensaver /
  sleep, and enable auto-login (steps below).
- Don't expect `ssh winbox python melee_peer.py enter` to work directly — it
  won't deliver keystrokes. Always go through the Scheduled Task.

## 0. Prereqs

- Slippi (netplay Dolphin) installed and working — you can already direct-connect
  to the Mac manually.
- A **slot-1 savestate** baked at the direct-connect menu with the **Mac's
  connect code/name pre-typed** (this is what F1 loads). It's the Windows analog
  of the Mac's slot-4. Make it once: open the direct-connect menu, type the Mac's
  code, then Dolphin menu **Emulation ▸ Save State ▸ Slot 1** (or Shift+F1).
  Confirm **F1 = Load State Slot 1** is bound in Dolphin's Hotkeys (Config ▸
  Hotkeys), and that **Enter** triggers the connect/search on that menu.
- Python 3 installed and on `PATH` (`python --version`).

## 1. Drop the peer files on the box

Copy this `peer/` folder (at least `melee_peer.py` and `win_paths.py`) to a
stable path, e.g. `C:\melee\peer\`. (Cloning the repo is fine too.)

## 2. Resolve the unknowns → edit `win_paths.py`

Run the built-in probe and edit `win_paths.py` to match:

```bat
cd C:\melee\peer
python melee_peer.py debug
```

It prints (and appends to `melee_peer.log`): whether `SLIPPI_EXE` / `ISO_PATH`
exist, whether Dolphin is detected as running, the list of visible window titles,
and which one matched `WINDOW_TITLE_SUBSTR`. Fix in `win_paths.py`:

- `SLIPPI_EXE` — real path to the netplay Dolphin exe (often under
  `%APPDATA%\Slippi Launcher\netplay\`; could be `Slippi Dolphin.exe` or
  `Dolphin.exe`). Find it: `where /r "%APPDATA%\Slippi Launcher" *.exe`.
- `PROCESS_NAME` — its image name in `tasklist` (`tasklist | findstr /i dolphin`).
- `ISO_PATH` — the Melee 1.02 NTSC ISO.
- `WINDOW_TITLE_SUBSTR` — a substring of the game window's title from the dump.
  **Do NOT assume `"Dolphin"`** — Slippi's game window is often titled e.g.
  `Faster Melee - Slippi (3.6.2)`, which has no "Dolphin" in it (window discovery
  silently returns `None` and `enter` can't focus). Pick a substring of the ACTUAL
  title, and avoid `"Slippi"` alone (it also matches the separate `Slippi Launcher`
  window). The committed `win_paths.py` uses `"Faster Melee"` — verify yours.
- `USER_DIR` — leave `""` unless you launch Dolphin with a non-default `-u` dir.

> The committed `win_paths.py` already holds a **verified-working** config for one
> box (`esash`'s: `Slippi Dolphin.exe`, `"Faster Melee"`, the ISO under
> `C:\Andrew generated\melee\…`). If you're on that box it should work as-is; on a
> different box, re-probe and edit.

### Verify launch + keystrokes locally (NOT over SSH yet)

From a normal interactive console on the box (you sitting at it):

```bat
python melee_peer.py kill
python melee_peer.py enter
```

You should see Slippi launch, focus, F1 load the slot-1 menu, then Enter connect.
If F1/Enter don't register: in Dolphin **Config ▸ Hotkeys**, set the input
**Device** to the keyboard backend and confirm the F1/Enter bindings. If a custom
backend ignores `SendInput`, tell the agent — there's a `PostMessage(WM_KEYDOWN)`
fallback to add. Check `melee_peer.log` for what was sent.

## 3. Enable OpenSSH Server + passwordless key auth from the Mac

```powershell
# (elevated PowerShell) install + start + autostart the OpenSSH server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

On the **Mac**, install a public key for passwordless login (so `BatchMode` SSH
from `peer.py` is non-interactive). **Use a dedicated passphrase-free key** — a
passphrase-protected key prompts interactively and breaks `BatchMode`:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_winbox    # dedicated, no passphrase
```

Then install `~/.ssh/id_winbox.pub` on Windows. **Where depends on the account:**
- **Non-admin user** → `C:\Users\USER\.ssh\authorized_keys`.
- **Administrator user** (common!) → Windows OpenSSH *ignores* the per-user file
  and reads ONLY `C:\ProgramData\ssh\administrators_authorized_keys` (the
  `Match Group administrators` block in `sshd_config`). That file needs tight
  ACLs — owner `SYSTEM` + `Administrators`, inheritance removed:
  ```powershell
  $f="C:\ProgramData\ssh\administrators_authorized_keys"
  icacls $f /inheritance:r /grant "SYSTEM:F" "BUILTIN\Administrators:F"
  ```

Add a host alias on the Mac (`~/.ssh/config`) and set its name as `PEER_SSH_HOST`
in `peer.py` (default is already `winbox`). `IdentitiesOnly yes` forces this key
(skips any others that would prompt):

```
Host winbox
    HostName 192.168.68.87     # the Windows LAN IP -- RESERVE it in your router!
    User esash                 # a DHCP lease will eventually go stale otherwise
    IdentityFile ~/.ssh/id_winbox
    IdentitiesOnly yes
```

Test from the Mac: `ssh winbox echo ok` → prints `ok`, no prompt. Then
`python3 -c "import peer; print(peer.Peer().ping())"` → `True`. (A
`post-quantum key exchange` warning from newer OpenSSH is cosmetic — ignore it.)

## 4. Register the four interactive Scheduled Tasks

Run on the Windows box (normal console; `/it` = interactive token, `/rl LIMITED`
keeps it in the user session). Adjust the python path / script path.

```bat
set PEER=C:\melee\peer\melee_peer.py
set PY=pythonw

schtasks /create /tn MeleePeer_Ensure  /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" ensure" /f
schtasks /create /tn MeleePeer_Enter   /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" enter" /f
schtasks /create /tn MeleePeer_Kill    /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" kill" /f
schtasks /create /tn MeleePeer_Restart /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" restart" /f
```

(`MeleePeer_Restart` force-closes and relaunches Slippi — the recovery path the
Mac uses, manually via `peer.Peer().restart()` or automatically inside
`Harness.enter_online` when a wedged peer keeps it out of an online match.)

(The `/sc ONCE /st 00:00` schedule never auto-fires; we only ever trigger these
with `schtasks /run`. Use `pythonw` so there's no console window — output goes to
`melee_peer.log`.)

Verify interactive-session delivery (the real test): **lock-free desktop**, then
from the Mac:

```bash
ssh winbox schtasks /run /tn MeleePeer_Kill
ssh winbox schtasks /run /tn MeleePeer_Enter
```

Slippi should launch and F1+Enter should connect — driven entirely from the Mac.
Tail `C:\melee\peer\melee_peer.log` to debug.

## 5. Keep the desktop awake + unlocked during a session

`SendInput` is dropped by a **locked** secure-desktop (which happens on
sleep→resume or a password screensaver). Two ways to handle it:

- **Recommended — `caffeinate.py` (per-session, changes no settings):** at the
  start of a testing session run `python peer\caffeinate.py` and leave the window
  open. It holds an "app is busy, stay awake" request (the same mechanism video
  players use) so the box won't sleep or screensaver-lock; closing the window or
  Ctrl+C restores normal power behavior. It does **not** disable your login PIN.
- **Or permanently:** Settings ▸ Accounts ▸ Sign-in options → require sign-in =
  **Never**; Power & screen → sleep = **Never**; disable the screensaver lock.
- **Auto-login** (optional, for true zero-touch from cold boot): `netplwiz` →
  uncheck "Users must enter a user name and password". On a Microsoft-account /
  Windows-Hello box the checkbox is hidden until you turn off Settings ▸ Accounts
  ▸ Sign-in options ▸ "…only allow Windows Hello sign-in". Skipping this just
  means you type your PIN once after a reboot.

Sanity check after login: from the Mac, `python3 verify_peer.py`.

### The status return channel (`peer_status.json`)

Each `melee_peer.py` run writes its outcome to `peer_status.json` next to the
script (atomic write): `{"cmd","ok","error","detail","time","epoch"}`. The Mac
reads it back over SSH (`peer.Peer.read_status()`) so it knows whether a triggered
command *actually* succeeded — `ok:false` on a locked desktop, crashed Slippi, or
keystroke rejection. `Harness.enter_online` uses this as one of its two signals
(the other is the Mac's own game scene). Set `PEER_STATUS_PATH` in `peer.py` to
this file's absolute path on the box. `peer_status.json` and `melee_peer.log` are
runtime artifacts (gitignored), not source.

## 6. Done — drive it from the Mac

Each session: log in, run `python peer\caffeinate.py` on the box (leave it open),
then from the Mac:

```bash
python3 verify_peer.py     # [PASS]/[FAIL] end-to-end: SSH, status channel, online match
```

Or in any online dev script: `h.enter_online(peer=Peer())`. Recover a wedged peer
anytime with `python3 peer.py restart` (or it auto-recovers inside `enter_online`).

## Firewall note

The SSH-only trigger needs just inbound **TCP 22** (OpenSSH's installer adds the
rule automatically). No other port is required for the MVP — the Mac never opens a
socket to a custom agent; it only runs `schtasks` over SSH.

## Troubleshooting

- `schtasks /run` succeeds but nothing happens on screen → desktop is **locked**,
  or the task isn't interactive (recreate with `/it`), or the user isn't logged
  in. Check `melee_peer.log` — if it has fresh `=== melee_peer enter ===` lines
  the task ran but `SendInput` hit a locked desktop.
- F1/Enter do nothing but Dolphin is focused → Hotkeys Device/binding issue
  (step 2). Ask the agent to add the `PostMessage` fallback.
- `peer.Peer().ping()` is False → SSH/key/IP problem (step 3); test `ssh winbox
  echo ok` directly.
- Mac reaches scene `0x0008` (online CSS) not `0x0208` → the peer connected but
  didn't get into a match; usually the slot-1 savestate isn't the right
  direct-connect state, or timing — the retry loop re-fires both sides.
- Mac drifts to `0x0202` (offline VS) → never blind-Enter; the harness retry
  re-loads the direct-connect savestate each attempt, which avoids this.
