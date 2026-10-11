from __future__ import annotations

import math
from typing import Any, Mapping


RIDDLE_STAGES: tuple[tuple[str, ...], ...] = (
    ("images",),
    ("piano",),
    ("prison",),
    ("wheel",),
    ("chains",),
    ("tangram", "magnet"),
    ("chess",),
    ("knocking",),
    ("candles",),
    ("stars",),
    ("sissi",),
)
FINAL_RIDDLE_STATUSES = frozenset({"solved", "skipped", "not_solved"})


def plan_riddle_time_change(
    riddle: str,
    solve_time_s: Any,
    times_by_riddle: Mapping[str, Any],
    statuses_by_riddle: Mapping[str, Any],
    *,
    max_time_s: float,
) -> dict[str, Any]:
    """Plan a correction without mutating timing state."""
    riddle = str(riddle or "").strip()
    if not riddle:
        raise ValueError("set_riddle_time requires riddle")
    try:
        source_stage_index = next(index for index, stage in enumerate(RIDDLE_STAGES) if riddle in stage)
    except StopIteration as exc:
        raise ValueError(f"Unknown riddle: {riddle}") from exc

    raw_seconds = float(solve_time_s)
    if not math.isfinite(raw_seconds) or not 1.0 <= raw_seconds <= max_time_s:
        raise ValueError("riddle time must be between 1 second and the recoverable limit")
    seconds = round(raw_seconds, 3)
    if seconds < 1.0:
        raise ValueError("riddle time must be at least 1 second")

    times: dict[str, float] = {}
    statuses: dict[str, str] = {}
    for stage in RIDDLE_STAGES:
        for key in stage:
            try:
                elapsed_s = float(times_by_riddle[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Missing or invalid timing for riddle: {key}") from exc
            if not math.isfinite(elapsed_s):
                raise ValueError(f"Missing or invalid timing for riddle: {key}")
            times[key] = elapsed_s
            statuses[key] = str(statuses_by_riddle.get(key) or "pending").strip().lower()

    source_status = statuses[riddle]
    if source_status == "pending":
        raise ValueError(f"Cannot set time for pending riddle: {riddle}")

    source_previous_s = times[riddle]
    source_stage = RIDDLE_STAGES[source_stage_index]
    old_stage_effective_s = max(times[key] for key in source_stage)
    changed_times = dict(times)
    changed_times[riddle] = seconds
    new_stage_effective_s = max(changed_times[key] for key in source_stage)
    stage_delta_s = round(new_stage_effective_s - old_stage_effective_s, 3)

    target_riddle: str | None = None
    target_previous_s: float | None = None
    target_new_s: float | None = None
    if source_status in FINAL_RIDDLE_STATUSES and abs(stage_delta_s) >= 0.0005:
        for target_stage in RIDDLE_STAGES[source_stage_index + 1:]:
            reached = [key for key in target_stage if statuses[key] != "pending"]
            if not reached:
                continue
            target_riddle = max(reached, key=lambda key: times[key])
            target_previous_s = times[target_riddle]
            target_stage_previous_s = max(times[key] for key in reached)
            desired_stage_s = round(target_stage_previous_s - stage_delta_s, 3)
            if desired_stage_s < 1.0:
                raise ValueError(
                    f"Time correction would reduce receiving riddle {target_riddle} below 1 second"
                )
            sibling_floor_s = max(
                (times[key] for key in reached if key != target_riddle),
                default=0.0,
            )
            if desired_stage_s < sibling_floor_s:
                raise ValueError(
                    f"Time correction cannot reduce parallel receiving stage below {sibling_floor_s:.3f} seconds"
                )
            if desired_stage_s > max_time_s:
                raise ValueError("receiving riddle time exceeds the recoverable range")
            target_new_s = desired_stage_s
            break

    return {
        "source_riddle": riddle,
        "source_previous_s": source_previous_s,
        "source_new_s": seconds,
        "source_delta_s": round(seconds - source_previous_s, 3),
        "source_stage_previous_s": old_stage_effective_s,
        "source_stage_new_s": new_stage_effective_s,
        "source_stage_delta_s": stage_delta_s,
        "target_riddle": target_riddle,
        "target_previous_s": target_previous_s,
        "target_new_s": target_new_s,
        "target_delta_s": (
            None
            if target_previous_s is None or target_new_s is None
            else round(target_new_s - target_previous_s, 3)
        ),
    }
