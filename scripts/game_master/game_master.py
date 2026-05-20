from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

import config
from db import Database
from models import CurrentRun, RiddleTiming, RuntimeState, ScheduledAction
from phases import PHASES, ADMIN_TARGET_PHASE, RIDDLE_SOLVE_EVENTS

LOG = logging.getLogger("game_master")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


class GameMaster:
    def __init__(self) -> None:
        self.state = RuntimeState(phase=config.DEFAULT_PHASE)
        self.db = Database(config.DB_PATH)
        self.runs_dir = Path(config.RUNS_DIR)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="game-master")
        self._client.enable_logger(LOG)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)

    def start(self) -> None:
        self._client.connect(config.MQTT_HOST, config.MQTT_PORT, config.MQTT_KEEPALIVE)
        self._client.loop_start()
        self._scheduler_thread.start()
        self._enter_phase(self.state.phase, "boot_settle")

    def stop(self) -> None:
        self._stop.set()
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        LOG.info("MQTT connected rc=%s", reason_code)
        client.subscribe(config.TOPIC_GAME_EVENT)
        client.subscribe(config.TOPIC_GAME_CMD)
        client.subscribe(config.TOPIC_HB_WILDCARD)
        client.subscribe(config.TOPIC_NODE_STATE_WILDCARD)
        self.publish_game_state()

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOG.warning("MQTT disconnected rc=%s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        payload_text = message.payload.decode("utf-8", errors="replace")
        try:
            if topic == config.TOPIC_GAME_EVENT:
                self.handle_game_event(self._decode_json(payload_text, topic))
                return
            if topic == config.TOPIC_GAME_CMD:
                self.handle_game_cmd(self._decode_json(payload_text, topic))
                return
            if topic.endswith("/hb"):
                self.handle_heartbeat(topic[:-3])
                return
            if topic.endswith("/state") and topic != config.TOPIC_GAME_STATE:
                self.handle_node_state(topic[:-6], self._decode_json(payload_text, topic))
                return
        except Exception:
            LOG.exception("Failed handling topic=%s payload=%s", topic, payload_text)

    @staticmethod
    def _decode_json(payload_text: str, topic: str) -> dict[str, Any]:
        obj = json.loads(payload_text)
        if not isinstance(obj, dict):
            raise ValueError(f"expected object payload on {topic}")
        if isinstance(obj.get("d"), dict):
            inner = dict(obj["d"])
            for key in ("t", "ts", "time_valid", "type", "v", "id"):
                if key in obj and key not in inner:
                    inner[key] = obj[key]
            return inner
        return obj

    def _publish_json(self, topic: str, payload: dict[str, Any], retained: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        self._client.publish(topic, body, qos=0, retain=retained)
        LOG.info("PUB %s %s", topic, body)

    def publish_game_state(self) -> None:
        with self._lock:
            payload = self.state.to_game_state_payload()
            self._enrich_game_state_payload_locked(payload)
        self._publish_json(config.TOPIC_GAME_STATE, payload, retained=True)

    def _enrich_game_state_payload_locked(self, payload: dict[str, Any]) -> None:
        run = self.state.current_run
        if run is None:
            payload.setdefault("players_count", 0)
            payload["run"] = None
            return

        now_mono = time.monotonic()
        timer_running = bool(payload.get("timer_running"))
        riddle_payloads: dict[str, dict[str, Any]] = {}
        for key, timing in run.riddle_timings.items():
            status = timing.status()
            final_time = float(timing.solve_time_s or 0)
            if status == "active" and timing.segment_started_monotonic is not None:
                live_time = round(max(0.0, now_mono - timing.segment_started_monotonic), 3)
            else:
                live_time = round(max(0.0, final_time), 3)
            riddle_payloads[key] = {
                "riddle_key": timing.riddle_key,
                "solve_time_s": round(max(0.0, final_time), 3),
                "live_time_s": live_time,
                "display_time_s": live_time if status == "active" else round(max(0.0, final_time), 3),
                "hint_count": int(timing.hint_count or 0),
                "hints": timing.hints or "",
                "skipped": bool(timing.skipped),
                "not_solved": bool(timing.not_solved),
                "status": status,
                "solved": status == "solved",
                "final": status in {"solved", "skipped", "not_solved"},
                "active": status == "active",
            }

        active_riddles = tuple(PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE]).active_riddles or ())
        current_riddle_name = ""
        current_riddle_elapsed_s = 0.0
        for key in active_riddles:
            item = riddle_payloads.get(key)
            if item and item.get("status") == "active":
                current_riddle_name = key
                current_riddle_elapsed_s = float(item.get("live_time_s") or 0)
                break

        live_duration_s = self._compute_live_effective_duration_s(riddle_payloads)
        payload["players_count"] = int(run.players_count or 0)
        payload["current_riddle_name"] = current_riddle_name
        payload["current_riddle_elapsed_s"] = round(max(0.0, current_riddle_elapsed_s), 3)
        payload["run"] = {
            "id": run.run_id,
            "run_id": run.run_id,
            "date": run.date,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "duration_s": run.duration_s if run.duration_s is not None else live_duration_s,
            "live_duration_s": live_duration_s,
            "players_count": int(run.players_count or 0),
            "leaderboard_code": run.leaderboard_code,
            "hint_count": run.hint_count(),
            "riddle_timings": riddle_payloads,
        }

    @staticmethod
    def _compute_live_effective_duration_s(riddle_payloads: dict[str, dict[str, Any]]) -> float:
        order = [
            "images", "piano", "prison", "wheel", "chains",
            "tangram", "magnet", "chess", "knocking", "candles", "stars", "sissi",
        ]
        times = {key: float((riddle_payloads.get(key) or {}).get("display_time_s") or 0) for key in order}
        serial_before_parallel = times["images"] + times["piano"] + times["prison"] + times["wheel"] + times["chains"]
        duration_s = (
            serial_before_parallel
            + max(times["tangram"], times["magnet"])
            + times["chess"]
            + times["knocking"]
            + times["candles"]
            + times["stars"]
            + times["sissi"]
        )
        return round(max(0.0, duration_s), 3)

    def publish_lighting_cmd(self, payload: dict[str, Any]) -> None:
        self._publish_json(config.TOPIC_LIGHTING_CMD, payload, retained=False)

    def publish_maglock_cmd(self, payload: dict[str, Any]) -> None:
        self._publish_json(config.TOPIC_MAGLOCK_CMD, payload, retained=False)

    def publish_debug(self, msg: str, d: dict[str, Any] | None = None) -> None:
        payload = {"ts": iso_now(), "msg": msg}
        if d:
            payload["d"] = d
        self._publish_json(config.TOPIC_GAME_MASTER_DEBUG, payload, retained=False)

    def _new_run_shell(self, players_count: int = 0) -> CurrentRun:
        now = utc_now()
        run = CurrentRun(
            run_id=f"run_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}",
            date=now.date().isoformat(),
            started_at=now.isoformat(timespec="seconds"),
            started_monotonic=time.monotonic(),
            players_count=max(0, int(players_count or 0)),
        )
        for node in config.RIDDLES:
            run.riddle_timings[node] = RiddleTiming(riddle_key=node)
        return run

    def _prepare_new_run(self) -> None:
        with self._lock:
            players_count = int(self.state.current_run.players_count) if self.state.current_run is not None else 0
            self.state.current_run = self._new_run_shell(players_count)
            self.state.completed_phase_events.clear()
            self.state.game_started_at = None
            self.state.last_riddle_solved_at = None

    def _start_run_timer(self) -> None:
        with self._lock:
            if self.state.current_run is None:
                self.state.current_run = self._new_run_shell()
            run = self.state.current_run
            now = utc_now()
            started_at = now.isoformat(timespec="seconds")
            run.date = now.date().isoformat()
            run.started_at = started_at
            run.started_monotonic = time.monotonic()
            run.ended_at = None
            run.duration_s = None
            run.events.clear()
            run.riddle_timings.clear()
            self.state.completed_phase_events.clear()
            self.state.game_started_at = started_at
            self.state.last_riddle_solved_at = None
            for node in config.RIDDLES:
                run.riddle_timings[node] = RiddleTiming(riddle_key=node)
            for node in PHASES.get(self.state.phase, PHASES[3]).active_riddles:
                if node in run.riddle_timings:
                    run.riddle_timings[node].segment_started_monotonic = run.started_monotonic
        self.publish_game_state()

    def _finalize_current_run(self) -> None:
        with self._lock:
            run = self.state.current_run
        if run is None:
            return
        self.db.recalc_run(run)
        self.db.save_completed_run(run)
        self._write_run_json(run)
        self.publish_game_state()

    def _write_run_json(self, run: CurrentRun) -> None:
        payload = {
            "run_id": run.run_id,
            "date": run.date,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "duration_s": run.duration_s,
            "players_count": int(run.players_count or 0),
            "leaderboard_code": run.leaderboard_code,
            "hint_count": run.hint_count(),
            "riddle_timings": {
                node: {
                    "riddle_key": timing.riddle_key,
                    "solve_time_s": timing.solve_time_s,
                    "hint_count": timing.hint_count,
                    "hints": timing.hints or "",
                    "skipped": bool(timing.skipped),
                    "not_solved": bool(timing.not_solved),
                    "status": timing.status(),
                }
                for node, timing in run.riddle_timings.items()
            },
            "events": list(run.events),
        }
        (self.runs_dir / f"{run.run_id}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _record_event(self, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            run.events.append({"ts": iso_now(), "event": event, **payload})

    def _mark_activations(self, nodes: tuple[str, ...]) -> None:
        if not nodes:
            return
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            now = time.monotonic()
            current_phase = int(self.state.phase)
            for node in nodes:
                if node == "candles" and current_phase < 11:
                    continue
                timing = run.riddle_timings.get(node)
                if timing is None:
                    continue
                if timing.is_final():
                    continue
                if timing.segment_started_monotonic is None:
                    timing.segment_started_monotonic = now

    def _mark_solved(self, node: str, source: str, outcome: str = "solved") -> None:
        outcome = str(outcome or "solved").strip().lower()
        if outcome not in {"solved", "skipped", "not_solved"}:
            outcome = "solved"
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            timing = run.riddle_timings.get(node)
            if timing is None:
                return
            if source == "phase" and timing.is_final():
                return

            now_mono = time.monotonic()
            segment_start = timing.segment_started_monotonic
            if segment_start is None:
                segment_start = run.started_monotonic

            if not timing.is_final() or float(timing.solve_time_s or 0) <= 0:
                timing.solve_time_s = round(max(0.0, now_mono - segment_start), 3)
            timing.segment_started_monotonic = None
            timing.skipped = outcome == "skipped"
            timing.not_solved = outcome == "not_solved"
            if timing.skipped and timing.not_solved:
                timing.not_solved = False
            self.state.last_riddle_solved_at = iso_now()

        self._record_event(outcome, {"node": node, "source": source})
        self.publish_game_state()

    @staticmethod
    def _merge_riddle_state(previous: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(previous or {})
        merged.update(payload)
        rid = str(payload.get("id") or "")
        if rid in {"knocking", "candles"}:
            attempts = list(merged.get("attempted_sequences") or [])
            last_attempt = str(payload.get("last_attempt") or "").strip()
            if last_attempt and (not attempts or attempts[-1] != last_attempt):
                attempts.append(last_attempt)
            merged["attempted_sequences"] = attempts
        elif rid in {"stars", "star_slider"}:
            attempts = list(merged.get("attempted_star_signs") or [])
            last_positions = payload.get("last_attempt_positions")
            if isinstance(last_positions, dict) and (not attempts or attempts[-1] != last_positions):
                attempts.append(last_positions)
            merged["attempted_star_signs"] = attempts
        return merged

    def handle_heartbeat(self, node_id: str) -> None:
        with self._lock:
            self.state.mark_hb(node_id)

    @staticmethod
    def _canonical_riddle_name(name: str) -> str:
        return "stars" if str(name).strip() == "star_slider" else str(name).strip()

    def handle_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            previous = self.state.node_last_state.get(node_id)
            self.state.node_last_state[node_id] = self._merge_riddle_state(previous, payload)

    def handle_game_event(self, payload: dict[str, Any]) -> None:
        node = self._canonical_riddle_name(str(payload.get("node", "")).strip())
        event = str(payload.get("event", "")).strip().lower()
        if not node or not event:
            raise ValueError("game/event requires node and event")
        self._record_event("game_event", payload)
        if event == "solved":
            self.handle_solve(node, source="node")

    def handle_game_cmd(self, payload: dict[str, Any]) -> None:
        cmd = str(payload.get("cmd", "")).strip().lower()
        if not cmd:
            raise ValueError("game/cmd requires cmd")
        if cmd in {"set_phase", "phase"}:
            self._enter_phase(int(payload["phase"]), "admin_set_phase")
            return
        if cmd in {"set_mode", "mode"}:
            target = str(payload["mode"]).strip().lower()
            if target == "start":
                if self.state.phase != 2:
                    self.publish_debug("START_IGNORED_NOT_PREPARE", {"phase": self.state.phase})
                    return
                self._enter_phase(3, "admin_start")
                return
            if target not in ADMIN_TARGET_PHASE:
                raise ValueError(f"Unknown admin target: {target}")
            self._enter_phase(ADMIN_TARGET_PHASE[target], f"admin_{target}")
            return
        if cmd in {"start", "start_game"}:
            if self.state.phase != 2:
                self.publish_debug("START_IGNORED_NOT_PREPARE", {"phase": self.state.phase})
                return
            self._enter_phase(3, "admin_start")
            return
        if cmd == "set_players_count":
            players_count = payload.get("players_count", 0)
            try:
                count = int(players_count)
            except (TypeError, ValueError):
                raise ValueError("set_players_count requires integer players_count")
            self.set_players_count(count)
            return
        if cmd == "add_hint":
            self.add_hint(str(payload.get("riddle", "")).strip(), str(payload.get("hint_text", "")).strip())
            return
        if cmd == "solve":
            riddle = self._canonical_riddle_name(str(payload.get("node", payload.get("riddle", ""))).strip())
            self.handle_solve(riddle, source="manual")
            return
        if cmd == "open_lock":
            self.publish_maglock_cmd({"cmd": "open", "lock": str(payload.get("lock", "")).strip()})
            return
        if cmd == "close_lock":
            self.publish_maglock_cmd({"cmd": "close", "lock": str(payload.get("lock", "")).strip()})
            return
        if cmd == "lighting":
            action = str(payload.get("action", "")).strip()
            out = {"cmd": action}
            for k, v in payload.items():
                if k not in {"cmd", "action"}:
                    out[k] = v
            self.publish_lighting_cmd(out)
            return
        if cmd == "maglock":
            action = str(payload.get("action", "")).strip()
            out = {"cmd": action}
            for k, v in payload.items():
                if k not in {"cmd", "action"}:
                    out[k] = v
            self.publish_maglock_cmd(out)
            return
        if cmd == "set_hint_count":
            self.set_hint_count(str(payload.get("riddle", "")).strip(), int(payload.get("count", 0) or 0))
            return
        if cmd in {"set_riddle_time", "set_solve_time"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            raw_seconds = payload.get("solve_time_s", payload.get("time_s", payload.get("seconds", 0)))
            self.set_riddle_time(riddle, float(raw_seconds or 0))
            return
        if cmd in {"set_riddle_outcome", "set_outcome"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            outcome = str(payload.get("outcome", payload.get("status", ""))).strip()
            self.set_riddle_outcome(riddle, outcome, advance=bool(payload.get("advance", False)))
            return
        if cmd in {"skip_riddle", "skip"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            self.set_riddle_outcome(riddle, "skipped", advance=True)
            return
        if cmd in {"mark_not_solved", "not_solved"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            self.set_riddle_outcome(riddle, "not_solved", advance=bool(payload.get("advance", False)))
            return
        if cmd in {"clear_riddle_outcome", "clear_outcome"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            self.set_riddle_outcome(riddle, "clear")
            return
        if cmd == "list_games":
            self.publish_debug("GAMES", {"games": self.db.list_games()})
            return
        raise ValueError(f"Unknown command: {cmd}")

    def set_players_count(self, players_count: int) -> None:
        cleaned_count = max(0, int(players_count or 0))
        with self._lock:
            if self.state.current_run is None:
                self.state.current_run = self._new_run_shell(cleaned_count)
            else:
                self.state.current_run.players_count = cleaned_count
        self._record_event("players_count_updated", {"players_count": cleaned_count})
        self.publish_game_state()

    def add_hint(self, riddle: str, hint_text: str) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if not riddle:
            raise ValueError("add_hint requires riddle")
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            timing.hint_count = int(timing.hint_count or 0) + 1
            if hint_text:
                timing.hints = ((timing.hints + "\n---\n" + hint_text) if timing.hints else hint_text).strip()
        self._record_event("hint_added", {"riddle": riddle, "hint_text": hint_text})
        self.publish_game_state()

    def set_hint_count(self, riddle: str, count: int) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if not riddle:
            raise ValueError("set_hint_count requires riddle")
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            timing.hint_count = max(0, int(count or 0))
        self._record_event("hint_count_set", {"riddle": riddle, "count": max(0, int(count or 0))})
        self.publish_game_state()

    def set_riddle_time(self, riddle: str, solve_time_s: float) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if not riddle:
            raise ValueError("set_riddle_time requires riddle")
        seconds = round(max(0.0, float(solve_time_s or 0)), 3)
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            if timing.status() == "active" and not timing.is_final():
                timing.segment_started_monotonic = time.monotonic() - seconds
                timing.solve_time_s = 0.0
            else:
                timing.solve_time_s = seconds
                if seconds <= 0 and not (timing.skipped or timing.not_solved):
                    spec = PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE])
                    if riddle not in spec.active_riddles:
                        timing.segment_started_monotonic = None
            run.duration_s = self.db._compute_effective_duration_s(run)
        self._record_event("riddle_time_set", {"riddle": riddle, "solve_time_s": seconds})
        self.publish_game_state()

    def set_riddle_outcome(self, riddle: str, outcome: str, *, advance: bool = False) -> None:
        riddle = self._canonical_riddle_name(riddle)
        outcome = str(outcome or "").strip().lower().replace("-", "_")
        if outcome in {"skip", "skipped"}:
            outcome = "skipped"
        elif outcome in {"not_solved", "not solved", "failed", "fail"}:
            outcome = "not_solved"
        elif outcome in {"clear", "reset", "pending"}:
            outcome = "clear"
        elif outcome == "solved":
            outcome = "solved"
        else:
            raise ValueError(f"Unknown riddle outcome: {outcome}")

        if advance and outcome in {"skipped", "not_solved", "solved"}:
            self._complete_active_riddle(riddle, source=f"admin_{outcome}", outcome=outcome)
            return

        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            if outcome == "clear":
                timing.skipped = False
                timing.not_solved = False
                if float(timing.solve_time_s or 0) <= 0:
                    spec = PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE])
                    if riddle in spec.active_riddles and timing.segment_started_monotonic is None:
                        timing.segment_started_monotonic = time.monotonic()
            elif outcome == "solved":
                timing.skipped = False
                timing.not_solved = False
                if float(timing.solve_time_s or 0) <= 0:
                    segment_start = timing.segment_started_monotonic or run.started_monotonic
                    timing.solve_time_s = round(max(0.0, time.monotonic() - segment_start), 3)
                timing.segment_started_monotonic = None
            else:
                if float(timing.solve_time_s or 0) <= 0:
                    segment_start = timing.segment_started_monotonic or run.started_monotonic
                    timing.solve_time_s = round(max(0.0, time.monotonic() - segment_start), 3)
                timing.skipped = outcome == "skipped"
                timing.not_solved = outcome == "not_solved"
                if timing.skipped and timing.not_solved:
                    timing.not_solved = False
                timing.segment_started_monotonic = None
            run.duration_s = self.db._compute_effective_duration_s(run)
        self._record_event("riddle_outcome_set", {"riddle": riddle, "outcome": outcome, "advance": bool(advance)})
        self.publish_game_state()

    def handle_solve(self, riddle: str, source: str) -> None:
        self._complete_active_riddle(riddle, source=source, outcome="solved")

    def _complete_active_riddle(self, riddle: str, source: str, outcome: str) -> None:
        if riddle not in config.RIDDLES:
            raise ValueError(f"Unknown riddle node: {riddle}")
        spec = PHASES[self.state.phase]
        if riddle not in spec.active_riddles:
            self.publish_debug("SOLVE_IGNORED_NOT_ACTIVE", {"phase": self.state.phase, "riddle": riddle, "source": source, "outcome": outcome})
            return
        if self.state.phase == 10 and riddle == "candles":
            self.publish_debug("SOLVE_BLOCKED_CANDLES_NOT_YET_ALLOWED", {"phase": self.state.phase, "outcome": outcome})
            return

        event_name = RIDDLE_SOLVE_EVENTS[riddle]
        self._mark_solved(riddle, source, outcome=outcome)
        with self._lock:
            self.state.completed_phase_events.add(event_name)
            completed = set(self.state.completed_phase_events)

        required = set(spec.required_events)
        if required and required.issubset(completed):
            if spec.next_phase is not None:
                self._enter_phase(spec.next_phase, event_name)

    def schedule_in(self, delay_s: float, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.state.pending.append(ScheduledAction(time.monotonic() + delay_s, kind, payload))
        self._record_event("scheduled", {"kind": kind, "delay_s": delay_s, "payload": payload})

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            self._run_due_actions()
            self._stop.wait(config.SCHEDULER_TICK_MS / 1000.0)

    def _run_due_actions(self) -> None:
        now = time.monotonic()
        due: list[ScheduledAction] = []
        with self._lock:
            keep: list[ScheduledAction] = []
            for action in self.state.pending:
                if action.due_monotonic <= now:
                    due.append(action)
                else:
                    keep.append(action)
            self.state.pending = keep
        for action in due:
            self._execute_action(action)

    def _execute_action(self, action: ScheduledAction) -> None:
        self._record_event("scheduled_executed", {"kind": action.kind, "payload": action.payload})
        if action.kind == "lighting_batch":
            commands = action.payload.get("commands", [])
            if isinstance(commands, list):
                for command in commands:
                    if not isinstance(command, dict):
                        continue
                    kind = command.get("kind")
                    payload = command.get("payload", {})
                    if isinstance(kind, str) and isinstance(payload, dict):
                        class _Action:
                            def __init__(self, kind: str, payload: dict[str, Any]) -> None:
                                self.kind = kind
                                self.payload = payload
                        self._run_transition_action(_Action(kind, payload))
                    else:
                        self.publish_lighting_cmd(command)
            return
    def _apply_phase_stable_scene(self, phase: int, lighting_phase: int | None = None, apply_maglocks: bool = True) -> None:
        spec = PHASES[phase]
        scene_phase = phase if lighting_phase is None else int(lighting_phase)
        scene_spec = PHASES[scene_phase]

        if apply_maglocks:
            self.publish_maglock_cmd({"cmd": "set_phase", "phase": phase})

        self.publish_lighting_cmd({"cmd": "set_phase", "phase": scene_phase})

        if apply_maglocks:
            for lock_id, state in spec.persistent_locks.items():
                self.publish_maglock_cmd({"cmd": "open" if state == "open" else "close", "lock": lock_id})

        star_sky_pct = int(scene_spec.lights.get("star_sky", 0) or 0)

        if all(v == 0 for v in scene_spec.lights.values()):
            self.publish_lighting_cmd({"cmd": "all_off"})
            self._publish_json("star_sky/cmd", {"cmd": "off"})
            return

        if all(v == 100 for v in scene_spec.lights.values()):
            self.publish_lighting_cmd({"cmd": "all_on"})
            self._publish_json("star_sky/cmd", {"cmd": "on"})
            return

        self.publish_lighting_cmd({"cmd": "all_off"})
        on_lights = [light for light, pct in scene_spec.lights.items() if pct == 100 and light != "star_sky"]
        if on_lights:
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": on_lights})
        dim_lights = [(light, pct) for light, pct in scene_spec.lights.items() if 0 < pct < 100 and light != "star_sky"]
        for light, pct in dim_lights:
            self.publish_lighting_cmd({"cmd": "set", "light": light, "pct": pct})

        self._publish_json("star_sky/cmd", {"cmd": "on" if star_sky_pct > 0 else "off"})

    def _set_lighting_phase(self, lighting_phase: int) -> None:
        with self._lock:
            self.state.lighting_phase = int(lighting_phase)
            phase = self.state.phase
        self._record_event("lighting_phase_changed", {"phase": phase, "lighting_phase": int(lighting_phase)})
        self._apply_phase_stable_scene(phase, lighting_phase=int(lighting_phase), apply_maglocks=False)

    def _run_transition_action(self, action) -> None:
        kind = action.kind
        payload = action.payload
        if kind == "set_persistent_locks":
            for lock_id, state in payload.items():
                self.publish_maglock_cmd({"cmd": "open" if state == "open" else "close", "lock": lock_id})
            return
        if kind == "set_all_lights":
            self.publish_lighting_cmd({"cmd": "all_on" if int(payload["pct"]) > 0 else "all_off"})
            return
        if kind == "set_lights_scene_prepare":
            self.publish_lighting_cmd({"cmd": "all_off"})
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": ["torch_stiege", "r1_bild", "r1_stuen", "r3_cage", "r3_slider"]})
            return
        if kind == "set_lights_scene_ingame_start":
            self.publish_lighting_cmd({"cmd": "all_off"})
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": ["torch_stiege", "r1_bild", "r1_stuen"]})
            return
        if kind == "new_game_init":
            self._prepare_new_run()
            return
        if kind == "timer_start":
            self._start_run_timer()
            return
        if kind == "timer_stop":
            self._finalize_current_run()
            return
        if kind == "pulse_open":
            self.publish_maglock_cmd({"cmd": "open", "lock": payload["lock"]})
            return
        if kind == "log_solve_time":
            self._mark_solved(payload["riddle"], source="phase")
            return
        if kind == "lighting_turn_on":
            self.publish_lighting_cmd({"cmd": "turn_on", "light": payload["light"]})
            return
        if kind == "lighting_fade_to":
            self.publish_lighting_cmd({"cmd": "fade_to", **payload})
            return
        if kind == "delay":
            self.schedule_in(float(payload["seconds"]), "lighting_batch", {"commands": payload["then"]})
            return
        if kind == "set_lighting_phase":
            self._set_lighting_phase(int(payload["phase"]))
            return
        if kind == "star_sky_on":
            self.publish_lighting_cmd({"cmd": "turn_on", "light": "r3_uv"})
            self._publish_json("star_sky/cmd", {"cmd": "on"})
            return
        if kind == "candles_solve_enabled":
            self.publish_debug("candles_solve_enabled", payload)
            return
        if kind == "save_game_to_db":
            return
        self.publish_debug("UNKNOWN_TRANSITION_ACTION", {"kind": kind, "payload": payload})

    def _enter_phase(self, new_phase: int, reason: str) -> None:
        spec = PHASES[new_phase]
        with self._lock:
            old_phase = self.state.phase
            self.state.last_phase = old_phase
            self.state.phase = new_phase
            self.state.lighting_phase = int(spec.lighting_phase_on_enter if spec.lighting_phase_on_enter is not None else new_phase)
            self.state.completed_phase_events.clear()
            lighting_phase = self.state.lighting_phase

        self._record_event("phase_changed", {"from": old_phase, "to": new_phase, "reason": reason, "lighting_phase": lighting_phase})
        self._apply_phase_stable_scene(new_phase, lighting_phase=lighting_phase)

        self._mark_activations(spec.active_riddles)
        for action in spec.on_enter:
            self._run_transition_action(action)

        self.publish_game_state()
