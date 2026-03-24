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
        return obj

    def _publish_json(self, topic: str, payload: dict[str, Any], retained: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        self._client.publish(topic, body, qos=0, retain=retained)
        LOG.info("PUB %s %s", topic, body)

    def publish_game_state(self) -> None:
        with self._lock:
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
            self.state.completed_phase_events.clear()

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
            self.state.completed_phase_events.clear()
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

    def _mark_activations(self, nodes: tuple[str, ...]) -> None:
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

    def _mark_solved(self, node: str, source: str) -> None:
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            timing = run.riddle_timings.get(node)
            if timing is None or timing.solved:
                return
            now_iso = iso_now()
            timing.solved = True
            timing.solved_at = now_iso
            timing.source = source
            timing.solve_time_from_run_start_s = round(time.monotonic() - run.started_monotonic, 3)
            if timing.activated_at:
                try:
                    activated_dt = datetime.fromisoformat(timing.activated_at)
                    solved_dt = datetime.fromisoformat(now_iso)
                    timing.solve_time_from_activation_s = round((solved_dt - activated_dt).total_seconds(), 3)
                except Exception:
                    timing.solve_time_from_activation_s = None
        self._record_event("solved", {"node": node, "source": source})

    def handle_heartbeat(self, node_id: str) -> None:
        with self._lock:
            self.state.mark_hb(node_id)

    def handle_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.state.node_last_state[node_id] = payload

    def handle_game_event(self, payload: dict[str, Any]) -> None:
        node = str(payload.get("node", "")).strip()
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
            riddle = str(payload.get("node", payload.get("riddle", ""))).strip()
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

    def handle_solve(self, riddle: str, source: str) -> None:
        if riddle not in config.RIDDLES:
            raise ValueError(f"Unknown riddle node: {riddle}")
        spec = PHASES[self.state.phase]
        if riddle not in spec.active_riddles:
            self.publish_debug("SOLVE_IGNORED_NOT_ACTIVE", {"phase": self.state.phase, "riddle": riddle, "source": source})
            return
        if self.state.phase == 10 and riddle == "candles":
            self.publish_debug("SOLVE_BLOCKED_CANDLES_NOT_YET_ALLOWED", {"phase": self.state.phase})
            return

        event_name = RIDDLE_SOLVE_EVENTS[riddle]
        self._mark_solved(riddle, source)
        with self._lock:
            self.state.completed_phase_events.add(event_name)
            completed = set(self.state.completed_phase_events)

        required = set(spec.required_events)
        if required and required.issubset(completed):
            if spec.next_phase is not None:
                self._enter_phase(spec.next_phase, event_name)
            else:
                self.publish_game_state()
        else:
            self.publish_game_state()

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
                    if isinstance(command, dict):
                        self.publish_lighting_cmd(command)
            return
        if action.kind == "maintenance_unsolve":
            return
        raise ValueError(f"Unknown scheduled action kind: {action.kind}")

    def _apply_phase_stable_scene(self, phase: int) -> None:
        spec = PHASES[phase]

        # phase notifications for future phase-based firmware
        self.publish_maglock_cmd({"cmd": "set_phase", "phase": phase})
        self.publish_lighting_cmd({"cmd": "set_phase", "phase": phase})

        # persistent locks only
        for lock_id, state in spec.persistent_locks.items():
            self.publish_maglock_cmd({"cmd": "open" if state == "open" else "close", "lock": lock_id})

        # explicit stable light scene
        if all(v == 0 for v in spec.lights.values()):
            self.publish_lighting_cmd({"cmd": "all_off"})
            return

        if all(v == 100 for v in spec.lights.values()):
            self.publish_lighting_cmd({"cmd": "all_on"})
            return

        self.publish_lighting_cmd({"cmd": "all_off"})
        on_lights = [light for light, pct in spec.lights.items() if pct == 100]
        if on_lights:
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": on_lights})
        dim_lights = [(light, pct) for light, pct in spec.lights.items() if 0 < pct < 100]
        for light, pct in dim_lights:
            self.publish_lighting_cmd({"cmd": "set", "light": light, "pct": pct})

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
            self.publish_lighting_cmd({"cmd": "turn_on_many", "lights": ["torch_stiege", "r1_bild", "r1_stuen", "r2_chess", "r2_schronk", "r3_cage", "r3_slider"]})
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
        with self._lock:
            old_phase = self.state.phase
            self.state.last_phase = old_phase
            self.state.phase = new_phase
            self.state.completed_phase_events.clear()

        self._record_event("phase_changed", {"from": old_phase, "to": new_phase, "reason": reason})
        self._apply_phase_stable_scene(new_phase)

        spec = PHASES[new_phase]
        self._mark_activations(spec.active_riddles)
        for action in spec.on_enter:
            self._run_transition_action(action)

        self.publish_game_state()
