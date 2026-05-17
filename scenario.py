"""
Scenario driver + observation oracle for the Frame-1 macro iteration loop.

A "trial" runs one shot of the test scenario:
  1. restore the seeded MEM1 snapshot                  (clean state)
  2. force Marth (P1) into the Catch action state      (the trigger)
  3. record both players' action states per frame      (the observation)
  4. classify the result                                (did Fox react? when?)

The trigger is set via a direct dme write to the Player Data action-state word.
This is more deterministic than driving controller inputs (no PADStatus race,
no character-specific transition prerequisites). It IS a slight abstraction
leak -- a macro that watches button presses rather than action states won't
fire under this trigger -- but the macro in scope for this project
("Fox shines when Marth's action state becomes Catch") reads action state, so
it's faithful to the spec.

The shine action-state ID is character-specific (above 0x155) and not
hardcoded here. Trials record the full state stream; downstream evaluators
identify shine by checking Fox transitioned out of the seeded resting state
(typically 0x0E = Wait) shortly after the trigger frame.
"""
from melee_harness import OFF_ACTION_STATE


# Marth grab action states (Action_State_Reference.csv):
CATCH = 0x00D4              # standing grab startup
CATCH_DASH = 0x00D5         # dash grab
CATCH_TURN = 0x00D6         # pivot grab
GRAB_STATES = (CATCH, CATCH_DASH, CATCH_TURN)

# Resting / default in-air state for both players in the seeded scenario.
WAIT = 0x000E
SQUAT_RUMBLE = 0x0027       # crouch transition (empirically observed)
SQUAT_WAIT = 0x0028         # held crouch

# Fox character-specific action states (≥ 0x155 are character-specific).
# Empirically discovered via verify_candidate_a: when Fox receives B + stick-
# down on the ground, action state transitions from 0x000E to 0x0168 (one
# frame), then to 0x0169 (subsequent frames). 0x0168 / 0x0169 are the ground
# shine startup / loop states for Fox.
FOX_SHINE_GROUND_START = 0x0168
FOX_SHINE_GROUND_LOOP = 0x0169

# Universal action states (< 0x155). KneeBend (0x18) is the standard jumpsquat
# state across characters; verify_candidate_b is expected to confirm Fox enters
# this state when Y is pressed on his pad. JumpF (0x19) / JumpAerialF (0x1B) /
# Fall (0x1D) etc. follow.
KNEE_BEND = 0x0018
JUMP_F = 0x0019
JUMP_B = 0x001A
JUMP_AERIAL_F = 0x001B
JUMP_AERIAL_B = 0x001C
FALL = 0x001D

# Scratch byte the Candidate-A input-driver gecko polls each frame. When this
# byte is non-zero on the port-0 (Marth) pad pass, the gecko ORs the Z bit into
# Marth's raw PADStatus.buttons. Address sits firmly inside the debug-menu
# free-memory region (DEFAULT_CAVE: 0x803FA3E8..0x803FC2EC) but well past the
# gecko code body itself, so it doesn't collide with the installed payload.
MARTH_Z_FLAG_ADDR = 0x803FB000

# Per-frame state counter for Candidate B.2 (jump-cancelled shine). Lives in
# the same debug-menu free-memory region as MARTH_Z_FLAG_ADDR but at a
# different offset so the two macros don't share scratch.
B2_COUNTER_ADDR = 0x803FA424


def force_action_state(h, port: int, state: int):
    """Overwrite a port's action-state word via dme. Returns the address that
    was written so the caller can re-read it if needed."""
    pd = h.player_data_ptr(port)
    if pd == -1:
        raise RuntimeError(f"port {port}: player data pointer invalid")
    h.write_words(pd + OFF_ACTION_STATE, [state])
    return pd + OFF_ACTION_STATE


def record_window(h, n_frames: int, ports=(1, 2)) -> list:
    """Capture per-frame (frame, p1_action, p2_action, ...) for `n_frames`.
    Frame-counter-keyed, so polling rate is decoupled from real time."""
    out = []
    for _ in range(n_frames):
        f = h.frame()
        snap = {"frame": f}
        for p in ports:
            snap[f"p{p}_action"] = h.action_state(p) & 0xFFFF
        out.append(snap)
        h.wait_frames(1)
    return out


def run_grab_trial(h, observe_frames: int = 12) -> dict:
    """One trial: reset to slot 1 (clean state), force Marth into Catch, observe.

    Returns:
      {
        "trigger_frame": <frame number Marth was forced into Catch>,
        "records":       list[per-frame snapshot],
      }
    """
    h.reset()
    # Sample one frame of baseline first, then trigger.
    baseline = record_window(h, 1)
    force_action_state(h, 1, CATCH)
    trigger_frame = h.frame()
    rest = record_window(h, observe_frames)
    return {"trigger_frame": trigger_frame, "records": baseline + rest}


def drive_marth_z(h, held: bool = True):
    """Set/clear the Candidate-A input-driver flag.

    Writes 1 (held) or 0 (released) to MARTH_Z_FLAG_ADDR. The installed gecko
    reads this byte on each port-0 pad pass and, if non-zero, injects Z into
    Marth's raw PADStatus.buttons before the engine consumes the input.
    """
    h.write_bytes(MARTH_Z_FLAG_ADDR, b"\x01" if held else b"\x00")


def reset_b2_counter(h):
    """Zero the Candidate-B.2 state-machine counter byte.

    Call before save_savestate(1) so per-iteration F1 reloads return to a
    counter=0 baseline. The macro itself only re-arms via a savestate load,
    so the counter has to be zeroed in MEM1 BEFORE the slot 1 snapshot is
    taken; otherwise iterations after the first will pick up whatever
    terminal value the previous trial left at 0x803FA424.
    """
    h.write_bytes(B2_COUNTER_ADDR, b"\x00")


def run_z_trigger_trial(h, hold_frames: int = 3, observe_frames: int = 15) -> dict:
    """One trial keyed by the input-driver Z flag rather than action-state force.

    Flow:
      1. reset to slot 1 (clean state with flag=0, gecko installed)
      2. record one baseline frame
      3. set flag, record `hold_frames` frames                (Z held)
      4. clear flag, record remaining frames                  (release + observe)

    The trigger frame is the first frame the flag is held.
    """
    h.reset()
    drive_marth_z(h, False)         # belt-and-suspenders
    baseline = record_window(h, 1)
    drive_marth_z(h, True)
    trigger_frame = h.frame()
    held = record_window(h, hold_frames)
    drive_marth_z(h, False)
    tail_n = max(0, observe_frames - hold_frames)
    tail = record_window(h, tail_n) if tail_n > 0 else []
    return {"trigger_frame": trigger_frame,
            "records": baseline + held + tail}


def classify_trial(trial: dict, baseline_p2_state: int = WAIT) -> dict:
    """Did Fox react, and on which frame relative to the trigger?

    "React" = P2's action state diverged from the baseline (typically 0x0E
    Wait). The first such frame's offset from the trigger frame is the
    reaction latency in frames.
    """
    records = trial["records"]
    trigger = trial["trigger_frame"]
    reaction_frame = None
    for r in records:
        if r["frame"] < trigger:
            continue
        if r["p2_action"] != baseline_p2_state:
            reaction_frame = r["frame"]
            break
    if reaction_frame is None:
        return {"reacted": False, "latency_frames": None, **trial}
    return {
        "reacted": True,
        "latency_frames": reaction_frame - trigger,
        "reaction_state": next(r["p2_action"] for r in records
                               if r["frame"] == reaction_frame),
        **trial,
    }
