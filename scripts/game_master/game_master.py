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
from models import CurrentRun, GameMode, RiddleTiming, RuntimeState, ScheduledAction

LOG = logging.getLogger("game_master")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


class GameMaster:
    def __init__(self) -> None:
        self.state = RuntimeState(mode=config.DEFAULT_MODE)
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
        return obj

    def _publish_json(self, topic: str, payload: dict[str, Any], retained: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        info = self._client.publish(topic, body, qos=0, retain=retained)
        info.wait_for_publish(timeout=2.0)
        LOG.info("PUB %s %s", topic, body)

    def publish_game_state(self) -> None:
        with self._lock:
            self.state.seq += 1
            payload = self.state.to_game_state_payload()
        self._publish_json(config.TOPIC_GAME_STATE, payload, retained=True)

    def publish_lighting_cmd(self, payload: dict[str, Any]) -> None:
        self._publish_json(config.TOPIC_LIGHTING_CMD, payload, retained=False)

    def publish_maglock_cmd(self, payload: dict[str, Any]) -> None:
        self._publish_json(config.TOPIC_MAGLOCK_CMD, payload, retained=False)

    def publish_debug(self, msg: str, d: dict[str, Any] | None = None) -> None:
        payload = {"ts": iso_now(), "msg": msg}
        if d:
            payload["d"] = d
        self._publish_json(config.TOPIC_GAME_MASTER_DEBUG, payload, retained=False)

    def set_mode(self, new_mode: GameMode) -> None:
        previous_mode = self.state.mode
        if previous_mode == new_mode:
            self.publish_game_state()
            return

        if previous_mode == GameMode.MODE_INGAME and new_mode == GameMode.MODE_STANDBY:
            self._finalize_current_run()

        if new_mode == GameMode.MODE_PREPARE:
            self._prepare_new_run()
        elif new_mode == GameMode.MODE_INGAME:
            self._start_run_timer()

        with self._lock:
            self.state.mode = new_mode
            self.state.pending.clear()

            if new_mode == GameMode.MODE_MAINTENANCE:
                self.state.active = list(config.RIDDLES)
                self.state.solved = []
            elif new_mode == GameMode.MODE_STANDBY:
                self.state.active = []
                self.state.solved = []
            elif new_mode == GameMode.MODE_PREPARE:
                self.state.active = []
                self.state.solved = []
            elif new_mode == GameMode.MODE_INGAME:
                self.state.active = list(config.INITIAL_ACTIVE)
                self.state.solved = []
                self._mark_activations(self.state.active)
            else:
                raise ValueError(f"Unsupported mode: {new_mode}")

        self._record_event("mode_changed", {"mode": new_mode.value})
        self._apply_controller_defaults_for_mode(new_mode)
        self.publish_game_state()

    def _apply_controller_defaults_for_mode(self, mode: GameMode) -> None:
        if mode == GameMode.MODE_MAINTENANCE:
            self.publish_lighting_cmd({"cmd": "all_on"})
            self.publish_maglock_cmd({"cmd": "set_fail_safe", "locks": ["r2", "r3"], "enabled": False})
        elif mode == GameMode.MODE_STANDBY:
            self.publish_lighting_cmd({"cmd": "all_on"})
            self.publish_maglock_cmd({"cmd": "set_mode", "mode": mode.value})
        elif mode == GameMode.MODE_PREPARE:
            self.publish_lighting_cmd({"cmd": "all_on"})
            self.publish_maglock_cmd({"cmd": "set_fail_safe", "locks": ["r2", "r3"], "enabled": True})
        elif mode == GameMode.MODE_INGAME:
            self.publish_lighting_cmd({"cmd": "all_off"})
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": list(config.INGAME_START_LIGHTS_ON)})
            self.publish_maglock_cmd({"cmd": "set_fail_safe", "locks": ["r2", "r3"], "enabled": True})

    def _new_run_shell(self, players: list[str] | None = None) -> CurrentRun:
        now = utc_now()
        run = CurrentRun(
            run_id=f"run_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}",
            date=now.date().isoformat(),
            started_at=now.isoformat(timespec="seconds"),
            started_monotonic=time.monotonic(),
            players=list(players or []),
        )
        for node in config.RIDDLES:
            source = "manual" if node in config.MANUAL_RIDDLES else "node"
            run.riddle_timings[node] = RiddleTiming(node=node, source=source)
        return run

    def _prepare_new_run(self) -> None:
        with self._lock:
            players = list(self.state.current_run.players) if self.state.current_run is not None else []
            self.state.current_run = self._new_run_shell(players)

    def _start_run_timer(self) -> None:
        with self._lock:
            if self.state.current_run is None:
                self.state.current_run = self._new_run_shell()
            run = self.state.current_run
            now = utc_now()
            run.date = now.date().isoformat()
            run.started_at = now.isoformat(timespec="seconds")
            run.started_monotonic = time.monotonic()
            run.ended_at = None
            run.duration_s = None
            run.hints.clear()
            run.events.clear()
            run.riddle_timings.clear()
            for node in config.RIDDLES:
                source = "manual" if node in config.MANUAL_RIDDLES else "node"
                run.riddle_timings[node] = RiddleTiming(node=node, source=source)

    def _finalize_current_run(self) -> None:
        with self._lock:
            run = self.state.current_run
        if run is None:
            return

        run.ended_at = iso_now()
        run.duration_s = round(time.monotonic() - run.started_monotonic, 3)
        self.db.save_completed_run(run)
        self._write_run_json(run)

        with self._lock:
            self.state.current_run = None

    def _write_run_json(self, run: CurrentRun) -> None:
        payload = {
            "run_id": run.run_id,
            "date": run.date,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "duration_s": run.duration_s,
            "players": list(run.players),
            "hint_count": run.hint_count(),
            "hints": list(run.hints),
            "riddle_timings": {
                node: {
                    "node": timing.node,
                    "source": timing.source,
                    "activated_at": timing.activated_at,
                    "solved_at": timing.solved_at,
                    "solve_time_from_run_start_s": timing.solve_time_from_run_start_s,
                    "solve_time_from_activation_s": timing.solve_time_from_activation_s,
                    "solved": timing.solved,
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

    def _mark_activations(self, nodes: list[str]) -> None:
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            now = iso_now()
            for node in nodes:
                timing = run.riddle_timings.get(node)
                if timing and timing.activated_at is None:
                    timing.activated_at = now
                    run.events.append({"ts": now, "event": "activated", "node": node})

    def handle_heartbeat(self, node_id: str) -> None:
        with self._lock:
            self.state.mark_hb(node_id)

    def handle_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.state.node_last_state[node_id] = payload

    def handle_game_event(self, payload: dict[str, Any]) -> None:
        node = str(payload.get("node", "")).strip()
        event = str(payload.get("event", "")).strip()
        if not node or not event:
            raise ValueError("game/event requires node and event")
        self._record_event("game_event", payload)
        if event == "solved":
            self.mark_solved(node, source="node")

    def handle_game_cmd(self, payload: dict[str, Any]) -> None:
        cmd = str(payload.get("cmd", "")).strip()
        if not cmd:
            raise ValueError("game/cmd requires cmd")

        if cmd == "set_mode":
            self.set_mode(GameMode(str(payload["mode"])))
            return
        if cmd == "start_game":
            self.set_mode(GameMode.MODE_INGAME)
            return
        if cmd == "set_players":
            players = payload.get("players", [])
            if not isinstance(players, list):
                raise ValueError("set_players requires players as list")
            self.set_players([str(p) for p in players])
            return
        if cmd == "add_hint":
            self.add_hint(str(payload.get("riddle", "")).strip(), str(payload.get("hint_text", "")).strip())
            return
        if cmd == "solve":
            self.mark_solved(str(payload.get("node", "")).strip(), source="manual")
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
        if cmd == "list_games":
            self.publish_debug("GAMES", {"games": self.db.list_games()})
            return
        raise ValueError(f"Unknown command: {cmd}")

    def set_players(self, players: list[str]) -> None:
        cleaned = [p.strip() for p in players if p.strip()]
        with self._lock:
            if self.state.current_run is None:
                self.state.current_run = self._new_run_shell(cleaned)
            else:
                self.state.current_run.players = cleaned
        self._record_event("players_updated", {"players": cleaned})
        self.publish_game_state()

    def add_hint(self, riddle: str, hint_text: str) -> None:
        if not riddle or not hint_text:
            raise ValueError("add_hint requires riddle and hint_text")
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            run.hints.append({"at": iso_now(), "riddle": riddle, "hint_text": hint_text})
        self._record_event("hint_added", {"riddle": riddle, "hint_text": hint_text})
        self.publish_game_state()

    def mark_solved(self, node: str, source: str) -> None:
        if node not in config.RIDDLES:
            raise ValueError(f"Unknown riddle node: {node}")

        with self._lock:
            if self.state.mode != GameMode.MODE_INGAME:
                self.publish_debug("SOLVE_IGNORED_NOT_INGAME", {"node": node, "source": source, "mode": self.state.mode.value})
                return
            if node in self.state.solved:
                self.publish_debug("SOLVE_DUPLICATE_IGNORED", {"node": node, "source": source})
                return

            self.state.solved.append(node)
            if node in self.state.active:
                self.state.active.remove(node)

            self._update_riddle_timing_on_solve(node, source)

            newly_active = self._compute_newly_active(node)
            for next_node in newly_active:
                if next_node not in self.state.active and next_node not in self.state.solved:
                    self.state.active.append(next_node)
            self._mark_activations(newly_active)

        self._record_event("solved", {"node": node, "source": source})
        self._apply_progression_side_effects(node)
        self.publish_game_state()

    def _compute_newly_active(self, solved_node: str) -> list[str]:
        candidates = list(config.UNLOCKS.get(solved_node, []))
        newly_active = [node for node in candidates if node not in self.state.solved]

        solved_set = set(self.state.solved)
        for gate in config.GATED_UNLOCKS:
            required = set(gate.get("requires_all", []))
            unlock = [str(x) for x in gate.get("unlock", [])]
            if solved_node in required and required.issubset(solved_set):
                for node in unlock:
                    if node not in newly_active and node not in self.state.solved:
                        newly_active.append(node)

        return newly_active

    def _update_riddle_timing_on_solve(self, node: str, source: str) -> None:
        run = self.state.current_run
        if run is None:
            return
        timing = run.riddle_timings.get(node)
        if timing is None:
            return
        now_iso = iso_now()
        timing.solved = True
        timing.solved_at = now_iso
        timing.source = source
        timing.solve_time_from_run_start_s = round(time.monotonic() - run.started_monotonic, 3)
        if timing.activated_at:
            activated_dt = datetime.fromisoformat(timing.activated_at)
            solved_dt = datetime.fromisoformat(now_iso)
            timing.solve_time_from_activation_s = round((solved_dt - activated_dt).total_seconds(), 3)

    def _apply_progression_side_effects(self, solved_node: str) -> None:
        if solved_node == "piano":
            self.publish_maglock_cmd({"cmd": "open", "lock": "r2"})
            self.schedule_in(10.0, "lighting_batch", {
                "commands": [
                    {"cmd": "turn_on", "light": "torch_r2"},
                    {"cmd": "fade_in", "lights": ["r2_chess", "r2_schronk"], "duration_ms": 10000},
                ]
            })
        elif solved_node == "chess":
            self.publish_maglock_cmd({"cmd": "open", "lock": "r3"})
            self.schedule_in(5.0, "lighting_batch", {
                "commands": [
                    {"cmd": "turn_on", "light": "torch_r2r3"},
                    {"cmd": "fade_in", "lights": ["r3_slider", "r3_cage"], "duration_ms": 10000},
                ]
            })
        elif solved_node == "knocking":
            self.publish_maglock_cmd({"cmd": "open", "lock": "knocking"})
        elif solved_node == "candles":
            self.publish_lighting_cmd({"cmd": "turn_on", "light": "r3_uv"})
            self.publish_lighting_cmd({
                "cmd": "fade_to",
                "lights": ["r3_slider", "r3_cage"],
                "pct": 25,
                "duration_ms": 7000,
            })
        elif solved_node == "star_slider":
            self.publish_maglock_cmd({"cmd": "open", "lock": "slider"})

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
        due = []
        with self._lock:
            keep = []
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
                    if isinstance(command, dict):
                        self.publish_lighting_cmd(command)
            return
        raise ValueError(f"Unknown scheduled action kind: {action.kind}")
