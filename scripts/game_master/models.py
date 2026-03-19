from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time


class GameMode(str, Enum):
    MODE_MAINTENANCE = "MODE_MAINTENANCE"
    MODE_STANDBY = "MODE_STANDBY"
    MODE_PREPARE = "MODE_PREPARE"
    MODE_INGAME = "MODE_INGAME"


class NodeState(str, Enum):
    NODE_SLEEPING = "NODE_SLEEPING"
    NODE_WAITING = "NODE_WAITING"
    NODE_ACTIVE = "NODE_ACTIVE"
    NODE_SOLVED = "NODE_SOLVED"
    NODE_FAULT = "NODE_FAULT"


@dataclass(slots=True)
class ScheduledAction:
    due_monotonic: float
    kind: str
    payload: dict[str, Any]


@dataclass(slots=True)
class RiddleTiming:
    node: str
    source: str
    activated_at: str | None = None
    solved_at: str | None = None
    solve_time_from_run_start_s: float | None = None
    solve_time_from_activation_s: float | None = None
    solved: bool = False


@dataclass(slots=True)
class CurrentRun:
    run_id: str
    date: str
    started_at: str
    started_monotonic: float
    players: list[str] = field(default_factory=list)
    hints: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    riddle_timings: dict[str, RiddleTiming] = field(default_factory=dict)
    ended_at: str | None = None
    duration_s: float | None = None

    def hint_count(self) -> int:
        return len(self.hints)


@dataclass(slots=True)
class RuntimeState:
    mode: GameMode = GameMode.MODE_STANDBY
    active: list[str] = field(default_factory=list)
    solved: list[str] = field(default_factory=list)
    seq: int = 0
    node_last_hb: dict[str, float] = field(default_factory=dict)
    node_last_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[ScheduledAction] = field(default_factory=list)
    current_run: CurrentRun | None = None

    def to_game_state_payload(self) -> dict[str, Any]:
        payload = {
            "mode": self.mode.value,
            "active": list(self.active),
            "solved": list(self.solved),
            "seq": self.seq,
        }
        if self.current_run is not None:
            payload["run"] = {
                "run_id": self.current_run.run_id,
                "date": self.current_run.date,
                "started_at": self.current_run.started_at,
                "players": list(self.current_run.players),
                "hint_count": self.current_run.hint_count(),
            }
        return payload

    def mark_hb(self, node_id: str) -> None:
        self.node_last_hb[node_id] = time.monotonic()
