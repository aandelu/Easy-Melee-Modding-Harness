"""
Closed-loop harness for developing the Frame-1 gecko macro (all-dme architecture).

libmelee was dropped: loading a savestate / reverting MEM1 corrupts libmelee's
Slippi EXI channel ("Invalid command byte" -> permanent desync). dme has no
host-side stateful channel, so it survives a MEM1 reset. dme is now the ONLY
channel and does everything:
  * observation -- read action states, entity pointers, the frame counter
  * scenario driving -- write the controller-data region to press buttons
  * reset -- snapshot all of MEM1 once, write it back each iteration
  * injection -- write a candidate gecko payload into a RAM code cave and patch
                 the hook branch
dme takes GC virtual addresses directly (e.g. 0x80453130).

Reset model: the user seeds the scenario once per session by loading savestate
slot 2 from Dolphin's GUI menu (Emulation > Load State); the harness snapshots
all of MEM1 via dme and writes it back each iteration. Writing the snapshot back
reverts the code region too, so the restore IS the reset -- no separate
hook-restore step, and injection happens AFTER each restore.

Process-name note: dme.hook() only scans for a process literally named "Dolphin",
but Slippi's executable is "Slippi Dolphin". We launch Dolphin ourselves via a
hardlink named "Dolphin" next to the real executable so macOS reports
p_comm == "Dolphin".
"""
import collections
import configparser
import contextlib
import dataclasses
import os
import pathlib
import shutil
import signal
import struct
import subprocess
import tempfile
import time

import dolphin_memory_engine as dme


_T0 = time.time()


def _log(msg):
    print(f"[harness +{time.time() - _T0:6.1f}s] {msg}", flush=True)


@contextlib.contextmanager
def _deadline(seconds, what):
    """Hard timeout that interrupts blocking syscalls. Main-thread only --
    Python only delivers signals to the main thread."""
    def _handler(signum, frame):
        raise TimeoutError(f"{what} timed out after {seconds}s")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_F1_VKEY = 122          # macOS virtual key code for F1
_F2_VKEY = 120          # macOS virtual key code for F2
_F4_VKEY = 118          # macOS virtual key code for F4 (load slot-4 = online entry)
_RETURN_VKEY = 36       # macOS virtual key code for Return/Enter (search/connect)
_SHIFT_L_VKEY = 56      # macOS virtual key code for left shift


def _focus_pid(pid: int) -> bool:
    """Bring the GUI app for `pid` to the foreground. Returns True on success."""
    from AppKit import NSRunningApplication
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    # 0x3 = NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps
    return bool(app.activateWithOptions_(0x3))


def _send_key(vkey: int):
    """Synthesize a keydown+keyup for `vkey` to the focused app. Requires
    Accessibility perm."""
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap,
    )
    CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, vkey, True))
    time.sleep(0.05)
    CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, vkey, False))


def _send_chord(modifier_vkey: int, key_vkey: int):
    """Press `modifier`, then press+release `key`, then release `modifier`.

    Dolphin's hotkey parser distinguishes between flag-only modifiers and
    actually-pressed modifier keys (Slippi's Hotkeys.ini watches for
    `Shift_L` | `Shift_R` press states, not the CGEvent modifier flag). So
    we synthesize the modifier as a separate keydown around the trigger key.
    """
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
        kCGEventFlagMaskShift, kCGHIDEventTap,
    )
    flag = kCGEventFlagMaskShift if modifier_vkey == _SHIFT_L_VKEY else 0
    # mod down
    CGEventPost(kCGHIDEventTap,
                CGEventCreateKeyboardEvent(None, modifier_vkey, True))
    time.sleep(0.02)
    # key down (with the modifier flag set so apps that consult flags see it)
    ev = CGEventCreateKeyboardEvent(None, key_vkey, True)
    if flag:
        CGEventSetFlags(ev, flag)
    CGEventPost(kCGHIDEventTap, ev)
    time.sleep(0.05)
    # key up
    ev = CGEventCreateKeyboardEvent(None, key_vkey, False)
    if flag:
        CGEventSetFlags(ev, flag)
    CGEventPost(kCGHIDEventTap, ev)
    time.sleep(0.02)
    # mod up
    CGEventPost(kCGHIDEventTap,
                CGEventCreateKeyboardEvent(None, modifier_vkey, False))


def _send_f2():
    _send_key(_F2_VKEY)


def _send_f1():
    """Slippi Hotkeys.ini binds F1 to 'Load State Slot 1'."""
    _send_key(_F1_VKEY)


def _send_shift_f1():
    """Slippi Hotkeys.ini binds Shift+F1 to 'Save State Slot 1' -- requires
    Shift_L or Shift_R to be pressed, not just the modifier flag."""
    _send_chord(_SHIFT_L_VKEY, _F1_VKEY)


# --- paths -----------------------------------------------------------------
DOLPHIN_HARDLINK = (
    "/Users/andrewashman/Library/Application Support/Slippi Launcher/"
    "netplay/Slippi Dolphin.app/Contents/MacOS/Dolphin"
)
ISO_PATH = "/Users/andrewashman/Desktop/isos/Super Smash Bros. Melee (v1.02 NTSC).iso"
USER_DIR = ("/Users/andrewashman/Library/Application Support/"
            "com.project-slippi.dolphin/netplay/User")
DOLPHIN_LOG = "/Users/andrewashman/Desktop/melee/dolphin.log"
# GameSettings override vendored from libmelee (its bundled GALE01r2.ini with
# {extra_codes} substituted empty). Replacing Slippi Dolphin's default GALE01
# gecko-code list with this minimal one suppresses an IntCPU "Unknown
# instruction" dialog that fires while loading our savestate. libmelee did the
# same thing via tmp_home_directory + setup_gecko_codes; without it, direct
# subprocess launches trigger the dialog every session.
GAME_SETTINGS_INI = "/Users/andrewashman/Desktop/melee/GALE01r2.ini"

# --- GameCube MEM1 (the snapshot/restore region used as the "savestate") ---
MEM1_BASE = 0x80000000
MEM1_SIZE = 0x1800000               # 24 MB

# --- SSBM memory map (v1.02) -----------------------------------------------
# 0x80453130 holds a pointer to a GObj struct, NOT directly to player data.
# Per Entity_Data_Offsets.csv, GObj.0x2C holds the actual Player Data pointer.
# UnclePunch's X+Y short-hop macro confirms this: `lwz r4,0x2c(r31)` then
# `lwz r3,0x65c(r4)` -- r31 is the GObj, +0x2c gets player data, then offsets
# like 0x65C (buttons) and 0x10 (action state) are inside player data. The
# OFF_* constants below are Player-Data-relative.
P1_ENTITY_PTR = 0x80453130          # P2 is +0xE90 (this is the GObj pointer)
ENTITY_PTR_STRIDE = 0xE90
OFF_PLAYER_DATA = 0x002C            # GObj -> Player Data pointer
OFF_CHAR_ID = 0x0004                # word, in Player Data
OFF_ACTION_STATE = 0x0010           # word, in Player Data
OFF_PORT_ID = 0x000C                # byte, in Player Data
OFF_BUTTONS = 0x065C                # word, in Player Data (processed buttons)

# Frame counters (Global_Addresses.csv):
#   0x80479D60 "Global frame timer"      -- primary; may reset between scenes.
#   0x804D7420 "Global Power-on Count"   -- +1 every frame, never resets; fallback.
# Both live in MEM1, so restore_snapshot() reverts them too -- fine, wait_frames()
# measures deltas from a fresh read.
FRAME_TIMER = 0x80479D60
POWERON_COUNT = 0x804D7420

# Scene controller (Global_Addresses.csv). getMinorMajor = minor_major(word).
SCENE_WORD = 0x80479D30
SCENE_ONLINE_IN_GAME = 0x0208       # online netplay, in a match (the entry target)
SCENE_ONLINE_CSS = 0x0008           # online connected, character-select (not started)
SCENE_OFFLINE_VS = 0x0202           # offline VS in-game (the "blind-Enter drift" trap)


def minor_major(word: int) -> int:
    """Decode the scene controller word at SCENE_WORD into Slippi's
    getMinorMajor value (e.g. 0x0208 = online in-game)."""
    return ((word << 8) | (word >> 24)) & 0xFFFF

# Melee's processed controller digital data (Global_Addresses.csv 0x804C1FAC):
# controllers 2-4 are at +0x44 multiples. Bit layout per the sheet:
#   xxxx xxxx UDLR UDLR xxxS YXBA xLRZ UDRL
# NOTE: the Spot_Dodge test macro hooks the *raw hardware PADStatus* struct
# (a different, 12-byte structure), whose base still needs locating for the
# scenario driver (task #4). These constants are for the processed region.
CONTROLLER_DIGITAL = 0x804C1FAC
CONTROLLER_STRIDE = 0x44

# Code cave for gecko injection: debug-menu tables region from Free_Memory.csv
# (0x803FA3E8-0x803FC2EC, 0x1F04 bytes). Safe to clobber -- we never touch the
# in-game debug menu. Replaces the previous unconfirmed 0x804DF000.
DEFAULT_CAVE = 0x803FA3E8
DEFAULT_CAVE_SIZE = 0x1F04


def _is_valid_mem1_ptr(addr: int) -> bool:
    return MEM1_BASE <= addr < MEM1_BASE + MEM1_SIZE


@dataclasses.dataclass
class Candidate:
    """A gecko-code candidate under test.

    `payload` is the FULL list of PowerPC instruction words written into the
    cave -- it must end by executing the displaced original instruction and
    branching back to hook_addr+4. Use `finalize_payload()` to append that tail
    to a pure-logic body.
    """
    name: str
    hook_addr: int          # GC address whose instruction we replace with a branch
    expected_orig: int      # instruction we expect to find at hook_addr (safety check)
    cave_addr: int          # where `payload` is written
    payload: list           # PPC instruction words (big-endian natural ints)


def make_branch(src_addr: int, dst_addr: int, link: bool = False) -> int:
    """Build a PPC `b`/`bl` instruction from src_addr to dst_addr."""
    offset = (dst_addr - src_addr) & 0x03FFFFFC
    return 0x48000000 | offset | (1 if link else 0)


def finalize_payload(logic: list, hook_addr: int, cave_addr: int,
                     expected_orig: int) -> list:
    """Append the displaced original instruction + branch-back-to-hook+4.

    Mirrors the gecko C2 convention: your logic runs, then the instruction you
    overwrote executes, then control returns just past the hook.

    NOTE: this is used by the legacy dme-runtime-inject path (Harness.inject()),
    which is non-functional on Slippi Dolphin -- dme writes to instruction
    memory aren't observed by the emulated CPU's instruction fetch, even in
    pure interpreter mode. Code injection now goes through install_gecko_c2()
    + launch(), which writes the candidate to GameSettings/GALE01r2.ini so
    Slippi's bootloader installs it (with proper icache flush) at boot.
    """
    payload = list(logic)
    payload.append(expected_orig)                       # run displaced instruction
    branch_back_src = cave_addr + len(payload) * 4
    payload.append(make_branch(branch_back_src, hook_addr + 4))
    return payload


def gecko_c2_lines(hook_addr: int, logic_words: list, displaced_orig: int,
                   code_name: str) -> list:
    """Format a C2 gecko code as Dolphin GameSettings INI lines.

    The gecko C2 codetype tells Slippi's codehandler to (a) replace the
    instruction at hook_addr with a branch into the code body, (b) execute
    the body, (c) execute the displaced original instruction, (d) branch
    back to hook_addr+4. The codehandler handles (a)/(d) and the icache
    flush; we just supply the body + the displaced original.

    Layout per gecko spec:
      header: `C2{hook_lower_24:6X} {n_lines:8X}`
      then n_lines of `{word_hi:8X} {word_lo:8X}` (two 32-bit words per line)

    CRITICAL: the C2 codehandler OVERWRITES the LAST word of the body with the
    branch-back (it does not append). So the body must end with a throwaway word,
    or the codehandler clobbers your last real instruction. We always append a
    0x00000000 branch-slot as the final word (and a NOP before it if needed to
    keep the count even), so the displaced original is never the last word. Bug
    history: when len(logic)+displaced was EVEN, the old code left the displaced
    as the last word -> codehandler ate it -> button extraction never ran ->
    corrupted inputs. Idempotent displaced loads (lbz/lhz) and odd counts hid it.

    Returns a list of strings -- first element is the title line
    ("$harness: <name>"), then the gecko code lines.
    """
    words = list(logic_words) + [displaced_orig]
    if len(words) % 2 == 0:
        words.append(0x60000000)        # nop, so the branch-slot below stays last + count even
    words.append(0x00000000)            # sacrificial branch slot (codehandler overwrites it)
    n_lines = len(words) // 2
    header = (0xC2000000 | (hook_addr & 0x00FFFFFF))
    out = [f"$harness: {code_name}"]
    out.append(f"{header:08X} {n_lines:08X}")
    for i in range(0, len(words), 2):
        out.append(f"{words[i]:08X} {words[i + 1]:08X}")
    return out


class Harness:
    def __init__(self):
        self._proc = None
        self._hooked = False
        self._snapshot = None
        self._frame_addr = None
        self._tmp_user_dir = None
        # Gecko codes to install at boot via GameSettings/GALE01r2.ini. Each
        # entry: dict(name=str, hook=int, logic=list[int], displaced=int).
        # Call install_gecko_c2(...) BEFORE launch().
        self._gecko_codes = []

    # --- gecko-code install ------------------------------------------------
    def install_gecko_c2(self, name: str, hook_addr: int,
                         logic_words: list, displaced_orig: int):
        """Stage a C2 gecko code to be installed at the next launch().

        The code lands at hook_addr in MEM1 (replacing displaced_orig with a
        branch to the codehandler body). MUST be called before launch() --
        codes are written into the tmp GameSettings INI as part of launch().

        This is the supported path for code injection: Slippi's bootloader
        reads the INI at boot, copies each gecko body into a code cave, and
        flushes the instruction cache so the emulated CPU sees the patch.
        Runtime dme writes to instruction memory do NOT work (Dolphin's CPU
        emulator caches the original code and never observes the writes).
        """
        if self._proc is not None:
            raise RuntimeError("install_gecko_c2 must be called before launch()")
        self._gecko_codes.append(dict(
            name=name, hook=hook_addr, logic=list(logic_words),
            displaced=displaced_orig,
        ))

    # --- lifecycle ---------------------------------------------------------
    def launch(self):
        """Launch Dolphin directly as a subprocess (no libmelee).

        Boots straight into the ISO with the real Slippi user dir, so savestate
        slot 2 (GALE01.s02 in USER_DIR/StateSaves) is available for the user to
        load via the GUI menu. The GUI is shown deliberately -- the user needs
        the Emulation menu to seed the scenario.
        """
        stale = subprocess.run(["pgrep", "-x", "Dolphin"],
                               capture_output=True, text=True).stdout.split()
        if stale:
            _log(f"WARNING: pre-existing 'Dolphin' process(es) {stale} -- dme may "
                 "hook the wrong one; close stray Dolphins before continuing.")

        # Build a tmp user dir: symlink every subdir from USER_DIR except
        # GameSettings and Config (which we override). Symlinks preserve access
        # to StateSaves/GALE01.s02 (the seeded scenario) and the user's Slippi
        # state without copying gigabytes of replays.
        #
        # GameSettings/GALE01r2.ini override (vendored from libmelee): replaces
        # Slippi Dolphin's default GALE01 gecko-code list with a minimal one.
        #
        # Config/Dolphin.ini override: copies the user's Config contents and
        # sets Interface.UsePanicHandlers=False so Dolphin logs panic alerts
        # instead of popping a modal dialog. Without this, every savestate load
        # triggers an IntCPU "Unknown instruction at 0x80c833a4" dialog (stale
        # gecko-codehandler branch into restored heap) that blocks iteration.
        # The dialog is functionally a "click Yes to skip the bad instruction
        # and continue"; UsePanicHandlers=False does exactly that automatically.
        self._tmp_user_dir = tempfile.mkdtemp(prefix="melee_harness_")
        real = pathlib.Path(USER_DIR)
        tmp = pathlib.Path(self._tmp_user_dir)
        for child in real.iterdir():
            if child.name in ("GameSettings", "Config"):
                continue
            os.symlink(child, tmp / child.name)

        (tmp / "GameSettings").mkdir()
        # Start from the vendored INI (suppresses Slippi default codes that
        # break savestate loads), then append every staged gecko-C2 code into
        # [Gecko] and enable it in [Gecko_Enabled].
        with open(GAME_SETTINGS_INI) as f:
            ini_text = f.read()
        if self._gecko_codes:
            enabled_block = ""
            codes_block = "\n"
            for c in self._gecko_codes:
                title = f"$harness: {c['name']}"
                enabled_block += title + "\n"
                for line in gecko_c2_lines(c["hook"], c["logic"],
                                           c["displaced"], c["name"]):
                    codes_block += line + "\n"
            # Insert enabled titles right after the [Gecko_Enabled] header.
            ini_text = ini_text.replace(
                "[Gecko_Enabled]\n",
                "[Gecko_Enabled]\n" + enabled_block,
                1,
            )
            # Append the code bodies at the end (the file ends inside the
            # [Gecko] section, after the last vendored code).
            ini_text += codes_block
            _log(f"staging {len(self._gecko_codes)} gecko code(s) into "
                 "GameSettings/GALE01r2.ini")
        with open(tmp / "GameSettings" / "GALE01r2.ini", "w") as f:
            f.write(ini_text)

        shutil.copytree(real / "Config", tmp / "Config", symlinks=False)
        dolphin_ini = tmp / "Config" / "Dolphin.ini"
        # strict=False: newer Slippi builds write a duplicate 'isopaths' key in
        # [General]; the default strict parser raises DuplicateOptionError. We only
        # touch [Interface].UsePanicHandlers, so last-value-wins is fine.
        cfg = configparser.ConfigParser(strict=False)
        if dolphin_ini.exists():
            cfg.read(dolphin_ini)
        if not cfg.has_section("Interface"):
            cfg.add_section("Interface")
        cfg.set("Interface", "UsePanicHandlers", "False")
        with open(dolphin_ini, "w") as f:
            cfg.write(f)
        _log(f"tmp user dir: {self._tmp_user_dir} (UsePanicHandlers=False)")

        _log("launching Dolphin")
        logf = open(DOLPHIN_LOG, "w")
        self._proc = subprocess.Popen(
            [DOLPHIN_HARDLINK, "-e", ISO_PATH, "-u", self._tmp_user_dir],
            stdout=logf, stderr=subprocess.STDOUT,
        )
        _log(f"Dolphin launched (pid {self._proc.pid}); log -> {DOLPHIN_LOG}")

    def hook_dme(self, attempts: int = 30, delay: float = 0.5):
        """Attach dme. Must run on the main thread: dme.hook() establishes
        process-attach state that does NOT survive being called from a
        short-lived worker thread (an earlier thread wrapper made hook() return
        but is_hooked() stay False forever).

        hook() can fail transiently right after launch (MEM1 not mapped yet),
        so retry with a plain sleep.
        """
        for i in range(attempts):
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"Dolphin exited (code {self._proc.returncode}) before dme "
                    f"hooked -- check {DOLPHIN_LOG}")
            dme.hook()
            if dme.is_hooked():
                _log(f"dme hooked on attempt {i + 1}")
                self._hooked = True
                return
            time.sleep(delay)
        raise RuntimeError("dme.hook() failed after retries -- "
                           "check the launched process is named 'Dolphin'")

    def close(self):
        """Tear down without ever hanging."""
        if self._hooked:
            try:
                dme.un_hook()
            except Exception:
                pass
            self._hooked = False
        if self._proc is not None:
            try:
                self._proc.kill()
                with _deadline(5, "dolphin process wait"):
                    self._proc.wait()
            except Exception as e:
                _log(f"close(): {e}")
            self._proc = None
        if self._tmp_user_dir is not None:
            try:
                shutil.rmtree(self._tmp_user_dir)
            except Exception as e:
                _log(f"close(): tmp dir cleanup: {e}")
            self._tmp_user_dir = None

    # --- frame timing ------------------------------------------------------
    def _pick_frame_counter(self):
        """Return whichever documented frame counter is currently advancing
        at ~60 Hz, or None if neither is. Right after a savestate load the
        game can be momentarily slow / paused, so retry a few times with a
        longer sample window."""
        for _ in range(5):
            for addr in (FRAME_TIMER, POWERON_COUNT):
                v0 = self.read_word(addr)
                time.sleep(0.5)             # ~30 expected frames at 60 Hz
                v1 = self.read_word(addr)
                delta = (v1 - v0) & 0xFFFFFFFF
                if 15 <= delta <= 45:
                    return addr
            time.sleep(0.5)
        return None

    def frame(self) -> int:
        """Return the current frame counter. Falls back to a wall-clock-based
        proxy (time.time() * 60) if no live counter was calibrated -- not
        frame-locked, but lets record_window/wait_frames keep running."""
        if self._frame_addr is None:
            return int(time.time() * 60)
        return self.read_word(self._frame_addr)

    def wait_frames(self, n: int, timeout_s: float = 5.0) -> int:
        """Block until the frame counter advances by `n`. Polls far faster than
        60 Hz so no frame is skipped. Falls back to wall-clock if no counter was
        found at seed time."""
        if self._frame_addr is None:
            time.sleep(n / 60.0)
            return -1
        start = self.frame()
        deadline = time.time() + timeout_s
        while True:
            cur = self.frame()
            if (cur - start) & 0xFFFFFFFF >= n:
                return cur
            if time.time() > deadline:
                raise TimeoutError(
                    f"wait_frames({n}) timed out -- counter stuck at {cur}")
            time.sleep(0.0005)

    # --- savestate loading -------------------------------------------------
    def _wait_for_cpu_alive(self, timeout_s: float = 60.0):
        """Block until the emulated CPU is incrementing the power-on counter.
        Before this point Dolphin hasn't booted Melee far enough to process
        savestate hotkeys."""
        prev = self.read_word(POWERON_COUNT)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(0.5)
            cur = self.read_word(POWERON_COUNT)
            if cur != prev:
                _log(f"CPU live (power-on counter {prev} -> {cur})")
                return
            prev = cur
        raise TimeoutError(f"CPU never started ticking within {timeout_s}s")

    def _send_load_key(self, slot: int):
        if slot == 1:
            _send_f1()
        elif slot == 2:
            _send_f2()
        else:
            raise NotImplementedError(
                f"load slot {slot}: only F1/F2 wired (slot 1/2)")

    def load_savestate(self, slot: int = 2, timeout_s: float = 60.0,
                       wait_in_game: bool = True) -> bool:
        """Focus Dolphin and synthesize the load-slot hotkey for `slot`.

        If `wait_in_game` (the default), polls until the P1 entity pointer
        becomes a valid MEM1 address (indicating in-game state) and retries
        the hotkey on a longer cadence. Set False when reloading a savestate
        whose game state is already in place (e.g. a slot we just saved
        ourselves) -- we then just sleep briefly to let the load apply.

        Requires Dolphin's Hotkey "Device" set to `Quartz/0/Keyboard & Mouse`
        in Hotkeys.ini so synthetic CGEvents reach the hotkey reader.
        """
        if self._proc is None:
            raise RuntimeError("Dolphin not launched")

        # Make sure Melee is past initial boot -- hotkeys are ignored before
        # the CPU is running.
        self._wait_for_cpu_alive(timeout_s=min(timeout_s, 30.0))
        time.sleep(1.0)

        if not wait_in_game:
            # Caller already validated in-game state; just send the key once
            # and give the savestate machinery a moment to apply.
            if not _focus_pid(self._proc.pid):
                _log("WARNING: could not focus Dolphin for load")
            time.sleep(0.3)
            self._send_load_key(slot)
            _log(f"sent synthetic load-slot-{slot} key")
            time.sleep(0.5)
            return True

        deadline = time.time() + timeout_s
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            if not _focus_pid(self._proc.pid):
                _log(f"WARNING: could not focus Dolphin (attempt {attempt})")
            time.sleep(0.3)
            self._send_load_key(slot)
            _log(f"sent synthetic load-slot-{slot} key (attempt {attempt})")
            for _ in range(50):
                time.sleep(0.1)
                try:
                    p1 = self.read_word(P1_ENTITY_PTR)
                except RuntimeError:
                    # dme sometimes detaches transiently right after savestate
                    # load (24MB MEM1 reload). Re-hook and retry.
                    try:
                        dme.un_hook()
                    except Exception:
                        pass
                    try:
                        dme.hook()
                    except Exception:
                        continue
                    continue
                if _is_valid_mem1_ptr(p1):
                    _log(f"in-game detected (P1 entity ptr 0x{p1:08X})")
                    return True
        raise TimeoutError(f"load_savestate slot {slot}: P1 entity never "
                           f"became valid after {attempt} attempts")

    # --- online netplay entry ----------------------------------------------
    def robust_scene(self, samples: int = 21, gap: float = 0.01):
        """Majority-vote the scene id to defeat torn reads during rollback.
        Returns (top_value, count, samples, Counter). Lifted from the
        copy-pasted helper in the online_*.py scripts."""
        vals = []
        for _ in range(samples):
            try:
                vals.append(minor_major(self.read_word(SCENE_WORD)))
            except Exception:
                vals.append(-1)
            time.sleep(gap)
        c = collections.Counter(vals)
        top, n = c.most_common(1)[0]
        return top, n, samples, c

    def send_online_key(self, vkey: int):
        """Focus Dolphin and synthesize a single keypress. Online entry uses
        F4 (_F4_VKEY = load slot-4 direct-connect savestate) then Return
        (_RETURN_VKEY = search/connect)."""
        if self._proc is None:
            raise RuntimeError("Dolphin not launched")
        _focus_pid(self._proc.pid)
        time.sleep(0.3)
        _send_key(vkey)

    def enter_online(self, peer=None, max_attempts: int = 12,
                     attempt_window_s: float = 9.0,
                     confirm_samples: int = 21,
                     restart_peer_after=None,
                     restart_recovery_s: float = 40.0) -> bool:
        """Drive this Mac's F4+Enter in lockstep with the Windows peer's
        F1+Enter and retry until the local scene reads SCENE_ONLINE_IN_GAME
        (0x0208).

        `peer` is a peer.Peer (or None for the legacy single-machine flow where
        the user drives the Windows box by hand). Duck-typed: peer needs
        enter_online() (and, for recovery, restart()), each returning (ok,status)
        or None -- no import of the peer module here.

        Per docs/WAVEDASH_ONLINE_RESULTS.md, re-fire BOTH sides each attempt
        (reloading the direct-connect savestate fresh); never blind-Enter at the
        CSS -- it drifts into offline VS (0x0202). Two confirmation signals: the
        peer's own status (ok = it focused + sent F1/Enter) and the Mac's scene
        (0x0208 = actually in a match). peer ok but scene stuck => the fault is
        savestate/timing/network, not the peer (logged as a diagnostic).

        The peer's `enter` task is self-sufficient (it launches Slippi + waits
        for the window before its own F1/Enter), so we don't pre-launch here --
        that would race the task's launch and could start two Slippi instances.
        On a cold peer the first confirmed enter blocks while it boots.

        Auto-recovery: if `peer` is given and we still aren't online after
        `restart_peer_after` attempts (default max_attempts // 2) AND the peer is
        not reporting healthy, force-restart its Slippi once (peer.restart(),
        which blocks until the window is back). If the restart can't be confirmed,
        fall back to waiting `restart_recovery_s`. Pass restart_peer_after=0 to
        disable. (A peer that reports healthy is never restarted -- that wouldn't
        fix a savestate/network problem.)
        """
        if peer is not None and restart_peer_after is None:
            restart_peer_after = max(1, max_attempts // 2)
        peer_restarted = False
        last_peer_ok = None   # peer's self-reported status from its last `enter`

        for attempt in range(1, max_attempts + 1):
            # Auto-recovery: a wedged peer (Slippi crashed / hotkeys dead) keeps
            # us out of 0x0208 forever; restart its Slippi once and let it boot.
            # Only restart if the peer is NOT reporting healthy -- if it reports
            # ok and we're still out, the fault is savestate/timing/network and a
            # restart just wastes time.
            if (peer is not None and not peer_restarted and restart_peer_after
                    and attempt == restart_peer_after + 1):
                if last_peer_ok is True:
                    _log("enter_online: peer reports healthy but we're not in a "
                         "match -- likely savestate/timing/network, NOT a wedge; "
                         "skipping the restart")
                else:
                    _log(f"enter_online: not online after {restart_peer_after} "
                         f"attempts and peer is not healthy -- restarting its "
                         f"Slippi (recovery)")
                    restart_ok = None
                    try:
                        res = peer.restart()  # confirmed: blocks until back up
                        restart_ok = res[0] if isinstance(res, tuple) else None
                    except Exception as e:
                        _log(f"enter_online: peer.restart failed: {e}")
                    if restart_ok is None:
                        # unconfirmed -> give it a fixed window to come back
                        time.sleep(restart_recovery_s)
                peer_restarted = True

            if peer is not None:
                # Fire the Windows side first so it is already searching when our
                # Enter lands. peer.enter_online() ensures running + F1 + Enter and
                # (confirmed) returns (ok, status) once the peer reports its result.
                try:
                    res = peer.enter_online()
                    last_peer_ok = res[0] if isinstance(res, tuple) else None
                except Exception as e:
                    _log(f"enter_online: peer.enter_online failed "
                         f"(attempt {attempt}): {e}")
                    last_peer_ok = None
            # Mac side: F4 (load slot-4 direct-connect) -> +3s -> Enter (connect).
            self.send_online_key(_F4_VKEY)
            time.sleep(3.0)
            self.send_online_key(_RETURN_VKEY)
            time.sleep(attempt_window_s)

            top, n, total, dist = self.robust_scene(samples=confirm_samples)
            _log(f"enter_online attempt {attempt}/{max_attempts}: scene majority "
                 f"0x{top:04X} ({n}/{total}) dist={dict(dist)}")
            if top == SCENE_ONLINE_IN_GAME and n >= total * 0.6:
                _log("enter_online: confirmed online in-game (0x0208)")
                return True
            # Diagnostic split (handoff §8): which side is the problem?
            if peer is not None and last_peer_ok is True:
                _log("  peer plumbing OK (focused + F1/Enter sent) but no match "
                     "yet -- savestate/timing/network, not the peer")
            elif peer is not None and last_peer_ok is False:
                _log("  peer reported a FAILURE (locked desktop / crashed Slippi "
                     "/ keystroke rejected) -- see its peer_status.json")
            elif peer is not None:
                _log("  no fresh peer status -- peer slow or unreachable")
        _log(f"enter_online: could NOT confirm online in-game after "
             f"{max_attempts} attempts")
        return False

    def save_savestate(self, slot: int = 1):
        """Trigger 'Emulation > Save State > Slot <N>' via AppleScript menu
        click. Captures whatever's currently in MEM1 -- including any dme
        writes we've done -- into the slot file on disk.

        Synthetic Shift+F<N> via CGEventPost does NOT reach Dolphin's hotkey
        reader on this system (proven experimentally) even though plain F2
        does. Menu-click goes through the AX/accessibility path and works
        reliably; the trade-off is needing GUI scripting permission.
        """
        if self._proc is None:
            raise RuntimeError("Dolphin not launched")
        if not 1 <= slot <= 10:
            raise NotImplementedError(f"save slot {slot}: must be 1..10")
        if not _focus_pid(self._proc.pid):
            _log("WARNING: could not focus Dolphin for save")
        time.sleep(0.3)
        script = (
            'tell application "System Events"\n'
            '    tell process "Dolphin"\n'
            f'        click (first menu item of menu 1 of menu item "Save State" '
            f'of menu 1 of menu bar item "Emulation" of menu bar 1 '
            f'whose name begins with "Slot {slot}")\n'
            '    end tell\n'
            'end tell\n'
        )
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"save_savestate(slot={slot}) osascript failed: "
                f"{result.stderr!r}")
        _log(f"clicked Emulation > Save State > Slot {slot}")
        # Save is async; let Dolphin finish writing the .sNN file.
        time.sleep(1.0)

    # --- snapshot-based reset ---------------------------------------------
    def _resolve_cave_from_hook(self, hook_addr: int):
        """Given a hook address whose word has been patched to a `b cave`
        branch by Slippi's codehandler, return the cave address (or None if
        the hook word doesn't look like an unconditional branch)."""
        word = self.read_word(hook_addr)
        if (word & 0xFC000000) != 0x48000000:
            return None
        disp = word & 0x03FFFFFC
        if disp & 0x02000000:           # sign-extend the 26-bit LI
            disp -= 0x04000000
        return (hook_addr + disp) & 0xFFFFFFFF

    def _persist_geckos_through_savestate(self, timeout_s: float):
        """Make boot-installed geckos survive a slot-2 savestate load.

        Sequence:
          1. Capture (cave_addr, hook_word) for each staged gecko by reading
             the hook word and following the branch to its cave.
          2. F2 (load slot 2 -- wipes all gecko caves + restores the hook
             words to their pre-patch originals).
          3. dme write each gecko body back into its cave + re-patch the hook
             with `b cave`. These writes land in MEM1 but the JIT cache still
             holds stale compiled blocks for the hook region, so the gecko
             does NOT execute yet.
          4. Save slot 1 via the Emulation > Save State > Slot 1 menu click.
             This captures the dme-overlaid MEM1 to disk (Dolphin's save path
             reads from the emulated memory directly, not from the JIT cache).
          5. F1 (load slot 1) -- triggers Dolphin's full savestate-load path,
             which flushes the JIT cache. Now the CPU recompiles the hook
             region from the overlaid MEM1, sees the branch into the cave,
             and the gecko runs.

        This is the only mechanism we've found that survives savestate load
        on Slippi Dolphin. Tested in diag_save_overlay_load.py.
        """
        # Slippi's codehandler patches hooks during early boot; we have to
        # wait until the CPU has actually run that code before reading the
        # hook words.
        self._wait_for_cpu_alive(timeout_s=min(timeout_s, 30.0))
        # Give the codehandler a moment beyond just-CPU-live to finish its
        # installs and reach the menu scene.
        time.sleep(1.0)

        # Step 1: resolve caves from the boot-installed hook patches.
        regions = []
        for c in self._gecko_codes:
            cave_addr = self._resolve_cave_from_hook(c["hook"])
            if cave_addr is None:
                raise RuntimeError(
                    f"gecko '{c['name']}' not installed at boot: hook "
                    f"0x{c['hook']:08X} is not a branch -- check the INI")
            first = self.read_word(cave_addr)
            if first != c["logic"][0]:
                raise RuntimeError(
                    f"gecko '{c['name']}' cave 0x{cave_addr:08X} first word "
                    f"0x{first:08X} doesn't match LOGIC[0] 0x{c['logic'][0]:08X} "
                    "-- codehandler placed something else here")
            body = list(c["logic"]) + [c["displaced"]]
            branch_back_src = cave_addr + len(body) * 4
            body.append(make_branch(branch_back_src, c["hook"] + 4))
            regions.append(dict(name=c["name"], cave_addr=cave_addr,
                                body=body, hook_addr=c["hook"]))
            _log(f"gecko '{c['name']}' cave @ 0x{cave_addr:08X} "
                 f"({len(body)} words)")

        # Step 2: F2 load slot 2 -- wipes geckos, restores scenario state.
        self.load_savestate(slot=2, timeout_s=timeout_s)

        # Step 3: dme overlay each gecko body + repatch its hook.
        for r in regions:
            self.write_words(r["cave_addr"], r["body"])
            self.write_words(r["hook_addr"],
                             [make_branch(r["hook_addr"], r["cave_addr"])])
        _log(f"dme-overlaid {len(regions)} gecko body/hook pair(s)")

        # Step 4: menu-save slot 1 to capture the overlay.
        self.save_savestate(slot=1)

        # Step 5: F1 load slot 1 to flush the JIT cache; gecko now runs.
        self.load_savestate(slot=1, wait_in_game=False)
        _log("save+load round-trip complete -- geckos should now execute")

    def seed_snapshot(self, timeout_s: float = 60.0, auto_load: bool = True):
        """Capture the reset point: a full MEM1 snapshot of the scenario state.

        With no geckos staged: F2 loads slot 2, then we snapshot.
        With geckos staged (boot-installed by Slippi's codehandler):
        seed_snapshot orchestrates a save+overlay+load round-trip so the
        geckos survive slot 2's MEM1 wipe and actually execute against the
        scenario state. See _persist_geckos_through_savestate for details.
        """
        if not dme.is_hooked():
            raise RuntimeError("seed_snapshot requires dme to be hooked first")

        if self._gecko_codes and auto_load:
            self._persist_geckos_through_savestate(timeout_s=timeout_s)
        else:
            loaded = False
            if auto_load:
                try:
                    _log("auto-loading savestate via synthetic F2")
                    self.load_savestate(slot=2, timeout_s=timeout_s)
                    loaded = True
                except TimeoutError as e:
                    _log(f"auto-load failed: {e}; falling back to manual prompt")

            if not loaded:
                print("\n" + "=" * 64, flush=True)
                print("ACTION NEEDED -- in the Dolphin window, use the menu:",
                      flush=True)
                print("    Emulation > Load State > Load State Slot 2",
                      flush=True)
                print("Waiting for an in-game state...", flush=True)
                print("=" * 64, flush=True)
                with _deadline(timeout_s, "seed_snapshot wait for in-game"):
                    while True:
                        p1 = self.read_word(P1_ENTITY_PTR)
                        if _is_valid_mem1_ptr(p1):
                            _log(f"in-game detected (P1 entity ptr "
                                 f"0x{p1:08X})")
                            break
                        time.sleep(0.1)

        time.sleep(0.2)             # let the loaded state settle a few frames
        self._snapshot = dme.read_bytes(MEM1_BASE, MEM1_SIZE)
        _log(f"snapshotted MEM1 ({len(self._snapshot)} bytes)")
        self._frame_addr = self._pick_frame_counter()
        if self._frame_addr is not None:
            _log(f"frame counter live at 0x{self._frame_addr:08X}")
        else:
            _log("WARNING: no advancing frame counter found -- wait_frames() "
                 "will fall back to wall-clock timing")

    def restore_snapshot(self, settle_frames: int = 3):
        """Reset to the seeded scenario by writing the MEM1 snapshot back.

        DEPRECATED for the gecko path: a 24 MiB MEM1 write via dme leaves the
        emulated CPU paused (power-on counter stops ticking) on Slippi
        Dolphin, AND occasionally detaches the dme task port. Use `reset()`
        instead, which sends an F1 (Load State Slot 1) hotkey -- Dolphin's
        savestate path restores MEM1 + CPU registers and unpauses cleanly.

        Kept for callers that haven't migrated.
        """
        if self._snapshot is None:
            raise RuntimeError("no snapshot -- call seed_snapshot() first")
        dme.write_bytes(MEM1_BASE, self._snapshot)
        try:
            dme.read_word(POWERON_COUNT)
        except Exception as e:
            _log(f"dme broken after MEM1 write ({e}); re-hooking")
            try:
                dme.un_hook()
            except Exception:
                pass
            for _ in range(20):
                dme.hook()
                try:
                    dme.read_word(POWERON_COUNT)
                    break
                except Exception:
                    time.sleep(0.1)
            else:
                raise RuntimeError("dme re-hook after restore failed")
        try:
            self.wait_frames(settle_frames)
        except TimeoutError:
            time.sleep(settle_frames / 60.0)
        _log("restored MEM1 snapshot")

    def reset(self, settle_frames: int = 3):
        """Per-iteration reset: F1 (load slot 1). Slot 1 was created by
        seed_snapshot's save+overlay+load round-trip; it has the gecko
        installed AND the scenario state in-game. Loading it via the
        savestate path restores MEM1 + CPU registers + unpauses cleanly.
        """
        self.load_savestate(slot=1, wait_in_game=False)
        try:
            self.wait_frames(settle_frames)
        except TimeoutError:
            time.sleep(settle_frames / 60.0)

    def resnapshot(self):
        """Re-capture MEM1 in place of the previous snapshot. Use when the
        caller has prepared a known-good state post-seed (e.g. zeroing a
        scratch byte that the gated gecko reads) and wants future restores
        to return to THAT state rather than the immediate post-seed state."""
        if not dme.is_hooked():
            raise RuntimeError("resnapshot requires dme to be hooked")
        self._snapshot = dme.read_bytes(MEM1_BASE, MEM1_SIZE)
        _log(f"re-snapshotted MEM1 ({len(self._snapshot)} bytes)")

    # --- dme read/write ----------------------------------------------------
    def read_word(self, addr: int) -> int:
        return dme.read_word(addr) & 0xFFFFFFFF

    def write_words(self, addr: int, words):
        for i, w in enumerate(words):
            dme.write_word(addr + i * 4, w & 0xFFFFFFFF)

    def read_bytes(self, addr: int, n: int) -> bytes:
        return dme.read_bytes(addr, n)

    def write_bytes(self, addr: int, data: bytes):
        dme.write_bytes(addr, data)

    # --- observation -------------------------------------------------------
    def entity_ptr(self, port: int) -> int:
        """Read the GObj pointer for a 1-indexed port. See OFF_PLAYER_DATA --
        callers usually want player_data_ptr() instead."""
        return self.read_word(P1_ENTITY_PTR + (port - 1) * ENTITY_PTR_STRIDE)

    def player_data_ptr(self, port: int) -> int:
        """Resolve the actual Player Data pointer: GObj + 0x2C. This is the
        base for char_id / action_state / port_id / buttons offsets."""
        gobj = self.entity_ptr(port)
        if not _is_valid_mem1_ptr(gobj):
            return -1
        pd = self.read_word(gobj + OFF_PLAYER_DATA)
        return pd if _is_valid_mem1_ptr(pd) else -1

    def action_state(self, port: int) -> int:
        pd = self.player_data_ptr(port)
        if pd == -1:
            return -1
        return self.read_word(pd + OFF_ACTION_STATE)

    def char_id(self, port: int) -> int:
        """Read internal character id (Fox=0x01, Marth=0x12)."""
        pd = self.player_data_ptr(port)
        if pd == -1:
            return -1
        return self.read_word(pd + OFF_CHAR_ID)

    def port_id(self, port: int) -> int:
        """Read the port/slot id byte from player data (0-indexed)."""
        pd = self.player_data_ptr(port)
        if pd == -1:
            return -1
        return self.read_bytes(pd + OFF_PORT_ID, 1)[0]

    # --- scenario driving --------------------------------------------------
    def set_digital_buttons(self, port: int, mask: int):
        """OR `mask` into a port's processed digital-data word (0x804C1FAC +
        0x44*(port-1)). Used to drive the scenario.

        NOTE: this races Dolphin's input pipeline, which rewrites the controller
        region every poll. For frame-exact driving the planned upgrade is a
        small persistent input-driver gecko reading a dme-owned scratch flag.
        """
        addr = CONTROLLER_DIGITAL + (port - 1) * CONTROLLER_STRIDE
        cur = struct.unpack(">I", self.read_bytes(addr, 4))[0]
        self.write_bytes(addr, struct.pack(">I", (cur | mask) & 0xFFFFFFFF))

    # --- dme injection -----------------------------------------------------
    def validate_cave(self, addr: int, n_words: int, samples: int = 5) -> bool:
        """Check the cave region reads as a stable value across frames.

        A region that changes frame-to-frame is live game data -- not safe to
        overwrite with code.
        """
        first = None
        for _ in range(samples):
            snap = tuple(self.read_word(addr + i * 4) for i in range(n_words))
            if first is None:
                first = snap
            elif snap != first:
                return False
            self.wait_frames(1)
        return True

    def inject(self, c: Candidate):
        """Write the candidate's payload into its cave and patch the hook.

        Safety: refuses to patch unless hook_addr currently holds the expected
        original instruction. Payload is written first (dormant -- nothing
        branches to it yet); the hook branch is written last to activate it.
        """
        found = self.read_word(c.hook_addr)
        if found != c.expected_orig:
            raise RuntimeError(
                f"hook 0x{c.hook_addr:08X} holds 0x{found:08X}, "
                f"expected 0x{c.expected_orig:08X} -- wrong address or already patched")
        self.write_words(c.cave_addr, c.payload)
        self.write_words(c.hook_addr, [make_branch(c.hook_addr, c.cave_addr)])
