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
    phase: int = 0
    last_phase: int | None = None
    node_last_hb: dict[str, float] = field(default_factory=dict)
    node_last_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[ScheduledAction] = field(default_factory=list)
    current_run: CurrentRun | None = None
    completed_phase_events: set[str] = field(default_factory=set)

    def to_game_state_payload(self) -> dict[str, Any]:
        payload = {"phase": self.phase}
        if self.last_phase is not None:
            payload["last_phase"] = self.last_phase
        run = self.current_run
        if run is not None:
            elapsed_s = 0
            timer_running = False
            if run.ended_at is not None and run.duration_s is not None:
                elapsed_s = int(round(run.duration_s))
            elif run.started_monotonic:
                elapsed_s = max(0, int(time.monotonic() - run.started_monotonic))
                timer_running = self.phase not in {0, 1, 2, 14}
            payload["run"] = {
                "players": list(run.players),
                "started_at": run.started_at,
                "ended_at": run.ended_at,
                "duration_s": run.duration_s,
                "elapsed_s": elapsed_s,
                "timer_running": timer_running,
                "riddle_timings": {
                    node: {
                        "activated_at": timing.activated_at,
                        "solved_at": timing.solved_at,
                        "solve_time_from_run_start_s": timing.solve_time_from_run_start_s,
                        "solve_time_from_activation_s": timing.solve_time_from_activation_s,
                        "solved": timing.solved,
                    }
                    for node, timing in run.riddle_timings.items()
                },
            }
        else:
            payload["run"] = {
                "players": [],
                "started_at": None,
                "ended_at": None,
                "duration_s": None,
                "elapsed_s": 0,
                "timer_running": False,
                "riddle_timings": {},
            }
        return payload

    def mark_hb(self, node_id: str) -> None:
        self.node_last_hb[node_id] = time.monotonic()
