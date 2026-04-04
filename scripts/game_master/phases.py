from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PhaseId = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
RiddleId = Literal[
    "images",
    "piano",
    "open_prison",
    "mount_wheel",
    "rope_paths",
    "tangram",
    "magnet",
    "chess",
    "knocking",
    "candles",
    "star_slider",
    "sissi",
]
LightId = Literal[
    "torch_stiege",
    "r1_bild",
    "r1_stuen",
    "r2_chess",
    "r2_schronk",
    "torch_r2",
    "torch_r2r3",
    "r3_cage",
    "r3_slider",
    "r3_uv",
    "star_sky",
]
PersistentLockId = Literal["r2", "r3"]

ALL_RIDDLES: tuple[RiddleId, ...] = (
    "images",
    "piano",
    "open_prison",
    "mount_wheel",
    "rope_paths",
    "tangram",
    "magnet",
    "chess",
    "knocking",
    "candles",
    "star_slider",
    "sissi",
)
ALL_LIGHTS: tuple[LightId, ...] = (
    "torch_stiege",
    "r1_bild",
    "r1_stuen",
    "r2_chess",
    "r2_schronk",
    "torch_r2",
    "torch_r2r3",
    "r3_cage",
    "r3_slider",
    "r3_uv",
    "star_sky",
)

@dataclass(frozen=True)
class TransitionAction:
    kind: str
    payload: dict = field(default_factory=dict)

@dataclass(frozen=True)
class PhaseSpec:
    phase: PhaseId
    name: str
    active_riddles: tuple[RiddleId, ...]
    solved_riddles: tuple[RiddleId, ...]
    persistent_locks: dict[PersistentLockId, Literal["open", "closed"]]
    lights: dict[LightId, int]  # steady-state pct 0..100
    required_events: tuple[str, ...]
    next_phase: int | None
    lighting_phase_on_enter: int | None = None
    on_enter: tuple[TransitionAction, ...] = field(default_factory=tuple)
    timer_action: str | None = None
    game_data_action: str | None = None

def scene(**values: int) -> dict[LightId, int]:
    out = {light: 0 for light in ALL_LIGHTS}
    out.update(values)
    return out

# Fail-secure pulse locks are NOT stable phase state.
# They are always manually openable by direct command and must never exceed 1000 ms.
PULSE_LOCKS = {
    "images": {"kind": "fail_secure_pulse", "max_open_ms": 1000, "manual_direct_command_always_allowed": True},
    "knocking": {"kind": "fail_secure_pulse", "max_open_ms": 1000, "manual_direct_command_always_allowed": True},
    "slider": {"kind": "fail_secure_pulse", "max_open_ms": 1000, "manual_direct_command_always_allowed": True},
}

PHASES: dict[PhaseId, PhaseSpec] = {
    0: PhaseSpec(
        phase=0,
        name="standby",
        active_riddles=(),
        solved_riddles=(),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(),
        required_events=(),
        next_phase=None,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r2": "open", "r3": "open"}),
            TransitionAction("set_all_lights", {"pct": 0}),
        ),
        timer_action="stopped",
        game_data_action="admin only transition target",
    ),
    1: PhaseSpec(
        phase=1,
        name="maintenance",
        active_riddles=ALL_RIDDLES,
        solved_riddles=(),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(**{light: 100 for light in ALL_LIGHTS}),
        required_events=(),
        next_phase=None,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r2": "open", "r3": "open"}),
            TransitionAction("set_all_lights", {"pct": 100}),
        ),
        timer_action="unchanged",
        game_data_action="maintenance solves auto-unsolve after 10s",
    ),
    2: PhaseSpec(
        phase=2,
        name="prepare",
        active_riddles=(),
        solved_riddles=(),
        persistent_locks={"r2": "closed", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r3_cage=100,
            r3_slider=100,
        ),
        required_events=("admin_start",),
        next_phase=3,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r2": "closed", "r3": "closed"}),
            TransitionAction("set_lights_scene_prepare", {}),
            TransitionAction("new_game_init", {}),
        ),
        timer_action="reset_to_zero",
        game_data_action="initialize run; players_count can be adjusted",
    ),
    3: PhaseSpec(
        phase=3,
        name="start",
        active_riddles=("images",),
        solved_riddles=(),
        persistent_locks={"r2": "closed", "r3": "closed"},
        lights=scene(torch_stiege=100, r1_bild=100, r1_stuen=100),
        required_events=("images_solved",),
        next_phase=4,
        on_enter=(
            TransitionAction("set_lights_scene_ingame_start", {}),
            TransitionAction("timer_start", {}),
        ),
        timer_action="running",
    ),
    4: PhaseSpec(
        phase=4,
        name="piano",
        active_riddles=("piano",),
        solved_riddles=("images",),
        persistent_locks={"r2": "closed", "r3": "closed"},
        lights=scene(torch_stiege=100, r1_bild=100, r1_stuen=100),
        required_events=("piano_solved",),
        next_phase=5,
        on_enter=(
            TransitionAction("pulse_open", {"lock": "images", "max_open_ms": 1000}),
            TransitionAction("log_solve_time", {"riddle": "images"}),
        ),
    ),
    5: PhaseSpec(
        phase=5,
        name="prison",
        active_riddles=("open_prison",),
        solved_riddles=("images", "piano"),
        persistent_locks={"r2": "open", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
        ),
        required_events=("open_prison_solved",),
        next_phase=6,
        lighting_phase_on_enter=4,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r2": "open"}),
            TransitionAction("log_solve_time", {"riddle": "piano"}),
            TransitionAction("delay", {
                "seconds": 7,
                "then": [
                    {"kind": "lighting_turn_on", "payload": {"light": "torch_r2"}},
                    {"kind": "lighting_fade_to", "payload": {"lights": ["r2_chess", "r2_schronk"], "pct": 100, "duration_ms": 8000}},
                ],
            }),
            TransitionAction("delay", {
                "seconds": 15,
                "then": [
                    {"kind": "set_lighting_phase", "payload": {"phase": 5}},
                ],
            }),
        ),
    ),
    6: PhaseSpec(
        phase=6,
        name="wheel",
        active_riddles=("mount_wheel",),
        solved_riddles=("images", "piano", "open_prison"),
        persistent_locks={"r2": "open", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
        ),
        required_events=("mount_wheel_solved",),
        next_phase=7,
        on_enter=(
            TransitionAction("log_solve_time", {"riddle": "open_prison"}),
        ),
    ),
    7: PhaseSpec(
        phase=7,
        name="rope",
        active_riddles=("rope_paths",),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel"),
        persistent_locks={"r2": "open", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
        ),
        required_events=("rope_paths_solved",),
        next_phase=8,
        on_enter=(
            TransitionAction("log_solve_time", {"riddle": "mount_wheel"}),
        ),
    ),
    8: PhaseSpec(
        phase=8,
        name="tangram_magnet",
        active_riddles=("tangram", "magnet"),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths"),
        persistent_locks={"r2": "open", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
        ),
        required_events=("tangram_solved", "magnet_solved"),
        next_phase=9,
        on_enter=(
            TransitionAction("log_solve_time", {"riddle": "rope_paths"}),
        ),
    ),
    9: PhaseSpec(
        phase=9,
        name="chess",
        active_riddles=("chess",),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet"),
        persistent_locks={"r2": "open", "r3": "closed"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
        ),
        required_events=("chess_solved",),
        next_phase=10,
        on_enter=(
            TransitionAction("log_solve_time", {"riddle": "tangram"}),
            TransitionAction("log_solve_time", {"riddle": "magnet"}),
        ),
    ),
    10: PhaseSpec(
        phase=10,
        name="knocking",
        active_riddles=("knocking", "candles"),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess"),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
            torch_r2r3=100,
            r3_cage=100,
            r3_slider=100,
        ),
        required_events=("knocking_solved",),
        next_phase=11,
        lighting_phase_on_enter=9,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r3": "open"}),
            TransitionAction("log_solve_time", {"riddle": "chess"}),
            TransitionAction("delay", {
                "seconds": 5,
                "then": [
                    {"kind": "lighting_turn_on", "payload": {"light": "torch_r2r3"}},
                    {"kind": "lighting_fade_to", "payload": {"lights": ["r3_cage", "r3_slider"], "pct": 100, "duration_ms": 7000}},
                ],
            }),
            TransitionAction("delay", {
                "seconds": 12,
                "then": [
                    {"kind": "set_lighting_phase", "payload": {"phase": 10}},
                ],
            }),
            TransitionAction("candles_solve_enabled", {"enabled": False}),
        ),
    ),
    11: PhaseSpec(
        phase=11,
        name="candles",
        active_riddles=("candles",),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking"),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
            torch_r2r3=100,
            r3_cage=100,
            r3_slider=100,
        ),
        required_events=("candles_solved",),
        next_phase=12,
        on_enter=(
            TransitionAction("pulse_open", {"lock": "knocking", "max_open_ms": 1000}),
            TransitionAction("log_solve_time", {"riddle": "knocking"}),
            TransitionAction("candles_solve_enabled", {"enabled": True}),
        ),
    ),
    12: PhaseSpec(
        phase=12,
        name="stars",
        active_riddles=("star_slider",),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles"),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
            torch_r2r3=100,
            r3_cage=25,
            r3_slider=25,
            r3_uv=100,
            star_sky=100,
        ),
        required_events=("star_slider_solved",),
        next_phase=13,
        on_enter=(
            TransitionAction("log_solve_time", {"riddle": "candles"}),
            TransitionAction("lighting_fade_to", {"lights": ["r3_cage", "r3_slider"], "pct": 25, "duration_ms": 7000}),
            TransitionAction("lighting_turn_on", {"light": "r3_uv"}),
            TransitionAction("star_sky_on", {}),
        ),
    ),
    13: PhaseSpec(
        phase=13,
        name="sissi",
        active_riddles=("sissi",),
        solved_riddles=("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider"),
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(
            torch_stiege=100,
            r1_bild=100,
            r1_stuen=100,
            r2_chess=100,
            r2_schronk=100,
            torch_r2=100,
            torch_r2r3=100,
            r3_cage=100,
            r3_slider=100,
            r3_uv=100,
            star_sky=100,
        ),
        required_events=("sissi_solved",),
        next_phase=14,
        lighting_phase_on_enter=12,
        on_enter=(
            TransitionAction("pulse_open", {"lock": "slider", "max_open_ms": 1000}),
            TransitionAction("log_solve_time", {"riddle": "star_slider"}),
            TransitionAction("lighting_fade_to", {"lights": ["r3_cage", "r3_slider"], "pct": 100, "duration_ms": 5000}),
            TransitionAction("delay", {
                "seconds": 5,
                "then": [
                    {"kind": "set_lighting_phase", "payload": {"phase": 13}},
                ],
            }),
        ),
    ),
    14: PhaseSpec(
        phase=14,
        name="finished",
        active_riddles=(),
        solved_riddles=ALL_RIDDLES,
        persistent_locks={"r2": "open", "r3": "open"},
        lights=scene(**{light: 100 for light in ALL_LIGHTS}),
        required_events=(),
        next_phase=None,
        on_enter=(
            TransitionAction("set_persistent_locks", {"r2": "open", "r3": "open"}),
            TransitionAction("set_all_lights", {"pct": 100}),
            TransitionAction("log_solve_time", {"riddle": "sissi"}),
            TransitionAction("timer_stop", {}),
            TransitionAction("save_game_to_db", {}),
        ),
        timer_action="stopped",
        game_data_action="finalize and persist run",
    ),
}

ADMIN_TARGET_PHASE = {
    "standby": 0,
    "maintenance": 1,
    "prepare": 2,
    "start": 3,
}

RIDDLE_SOLVE_EVENTS = {
    "images": "images_solved",
    "piano": "piano_solved",
    "open_prison": "open_prison_solved",
    "mount_wheel": "mount_wheel_solved",
    "rope_paths": "rope_paths_solved",
    "tangram": "tangram_solved",
    "magnet": "magnet_solved",
    "chess": "chess_solved",
    "knocking": "knocking_solved",
    "candles": "candles_solved",
    "star_slider": "star_slider_solved",
    "sissi": "sissi_solved",
}
