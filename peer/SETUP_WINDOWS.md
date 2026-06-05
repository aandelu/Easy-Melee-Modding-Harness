# Windows netplay-peer — one-time setup

Goal: let the Mac harness drive the Windows netplay peer with **zero physical
interaction** — `Harness.enter_online(peer=Peer())` triggers Slippi launch + F1
(load slot-1 direct-connect savestate) + Enter (connect) on this box over SSH.

You do this **once**. After that the Windows box just needs to be powered on,
logged in, and unlocked; the Mac does the rest.

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
- `WINDOW_TITLE_SUBSTR` — a substring of Dolphin's window title from the dump
  (default `"Dolphin"` usually works).
- `USER_DIR` — leave `""` unless you launch Dolphin with a non-default `-u` dir.

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

On the **Mac**, install your public key for passwordless login (so `BatchMode`
SSH from `peer.py` is non-interactive):

```bash
ssh-keygen -t ed25519        # if you don't already have a key
# append ~/.ssh/id_ed25519.pub to the Windows user's authorized_keys, OR:
ssh-copy-id USER@WINDOWS_IP  # if available
```

For a **non-admin** Windows user the key goes in `C:\Users\USER\.ssh\authorized_keys`.
(Admin users use `C:\ProgramData\ssh\administrators_authorized_keys` with tight
ACLs — prefer a non-admin user to avoid that.)

Add a convenient host alias in the Mac's `~/.ssh/config` and set it as
`PEER_SSH_HOST` in `peer.py`:

```
Host winbox
    HostName 192.168.1.NN      # the Windows LAN IP (give it a static/DHCP-reserved IP)
    User USER
    IdentityFile ~/.ssh/id_ed25519
```

Test from the Mac: `ssh winbox echo ok` → prints `ok` with no prompt. Then
`python3 -c "import peer; print(peer.Peer().ping())"` → `True`.

## 4. Register the three interactive Scheduled Tasks

Run on the Windows box (normal console; `/it` = interactive token, `/rl LIMITED`
keeps it in the user session). Adjust the python path / script path.

```bat
set PEER=C:\melee\peer\melee_peer.py
set PY=pythonw

schtasks /create /tn MeleePeer_Ensure /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" ensure" /f
schtasks /create /tn MeleePeer_Enter  /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" enter" /f
schtasks /create /tn MeleePeer_Kill   /sc ONCE /st 00:00 /it /rl LIMITED ^
  /tr "%PY% \"%PEER%\" kill" /f
```

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

## 5. Make it always-ready (zero-touch after a reboot)

- **Auto-login**: `netplwiz` → uncheck "Users must enter a user name and
  password" → enter the password once. (Or set up per your security comfort.)
- **Stay unlocked**: Settings ▸ Accounts ▸ Sign-in options → "If you've been
  away, when should Windows require sign-in" = **Never**; Power & screen → screen
  off / sleep = **Never**; disable the screensaver / lock policy. Keystrokes need
  an unlocked interactive desktop.
- Optional: a "MeleePeer is set up" sanity check after reboot — log in, then from
  the Mac `python3 verify_peer.py`.

## 6. Done — drive it from the Mac

```bash
python3 verify_peer.py     # [PASS]/[FAIL] end-to-end: SSH, remote launch, online match
```

Or in any online dev script: `h.enter_online(peer=Peer())`.

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
