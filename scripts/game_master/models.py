from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time

@dataclass(slots=True)
class ScheduledAction:
    due_monotonic: float
    kind: str
    payload: dict[str, Any]

@dataclass(slots=True)
class RiddleTiming:
    riddle_key: str
    solve_time_s: float = 0.0
    hint_count: int = 0
    hints: str = ""
    skipped: bool = False
    not_solved: bool = False
    segment_started_monotonic: float | None = None

    def is_final(self) -> bool:
        return bool(self.skipped or self.not_solved or float(self.solve_time_s or 0) > 0)

    def status(self) -> str:
        if self.skipped:
            return "skipped"
        if self.not_solved:
            return "not_solved"
        if float(self.solve_time_s or 0) > 0:
            return "solved"
        if self.segment_started_monotonic is not None:
            return "active"
        return "pending"

@dataclass(slots=True)
class CurrentRun:
    run_id: str
    date: str
    started_at: str
    started_monotonic: float
    players_count: int = 0
    leaderboard_code: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    riddle_timings: dict[str, RiddleTiming] = field(default_factory=dict)
    ended_at: str | None = None
    duration_s: float | None = None

    def hint_count(self) -> int:
        return sum(max(0, int(t.hint_count or 0)) for t in self.riddle_timings.values())

@dataclass(slots=True)
class RuntimeState:
    phase: int = 0
    lighting_phase: int = 0
    last_phase: int | None = None
    game_started_at: str | None = None
    last_riddle_solved_at: str | None = None
    node_last_hb: dict[str, float] = field(default_factory=dict)
    node_last_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[ScheduledAction] = field(default_factory=list)
    current_run: CurrentRun | None = None
    completed_phase_events: set[str] = field(default_factory=set)

    def to_game_state_payload(self) -> dict[str, Any]:
        payload = {"phase": self.phase, "lighting_phase": self.lighting_phase}
        if self.last_phase is not None:
            payload["last_phase"] = self.last_phase
        if self.game_started_at:
            payload["game_started_at"] = self.game_started_at
        if self.last_riddle_solved_at:
            payload["last_riddle_solved_at"] = self.last_riddle_solved_at
        payload["timer_running"] = self.game_started_at is not None and self.phase >= 3 and self.phase < 14
        return payload

    def mark_hb(self, node_id: str) -> None:
        self.node_last_hb[node_id] = time.monotonic()
