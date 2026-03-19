
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
BROKER_HOST = os.getenv("ER1_MQTT_HOST", "192.168.0.10")
BROKER_PORT = int(os.getenv("ER1_MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("ER1_DASHBOARD_CLIENT_ID", "er1_dashboard")
HINTS_PATH = BASE_DIR / 'dashboard_hints.json'

TOPIC_GAME_STATE = "game/state"
TOPIC_GAME_CMD = "game/cmd"
TOPIC_LIGHTING_CMD = "lighting/cmd"
TOPIC_MAGLOCK_CMD = "maglock/cmd"
TOPIC_STAR_SKY_CMD = "star_sky/sys/cmd"

RIDDLES = [
    {"id": "images", "label": "Images", "node_id": "images_piano", "manual": False},
    {"id": "piano", "label": "Piano", "node_id": "images_piano", "manual": False},
    {"id": "chains", "label": "Chains", "node_id": None, "manual": True},
    {"id": "tangram", "label": "Tangram", "node_id": None, "manual": True},
    {"id": "magnet", "label": "Magnet", "node_id": None, "manual": True},
    {"id": "chess", "label": "Chess", "node_id": "chess", "manual": False},
    {"id": "knocking", "label": "Knocking", "node_id": "knocking", "manual": False},
    {"id": "candles", "label": "Candles", "node_id": "candles", "manual": False},
    {"id": "star_slider", "label": "Star Slider", "node_id": "star_slider", "manual": False},
    {"id": "free_sissi", "label": "Free Sissi", "node_id": None, "manual": True},
]

LOCKS = [
    {"id": "r2", "label": "r2", "kind": "toggle"},
    {"id": "r3", "label": "r3", "kind": "toggle"},
    {"id": "images", "label": "images", "kind": "open"},
    {"id": "knocking", "label": "knocking", "kind": "open"},
    {"id": "slider", "label": "slider", "kind": "open"},
]

LIGHT_GROUPS = {
    "entrance": {"label": "entrance", "lights": ["torch_stiege"], "dimmable": False},
    "r1": {"label": "r1", "lights": ["r1_stuen", "r1_bild"], "dimmable": False},
    "r2": {"label": "r2", "lights": ["r2_chess", "r2_schronk", "torch_r2"], "dimmable": False},
    "r3": {"label": "r3", "lights": ["r3_cage", "r3_slider", "torch_r2r3"], "dimmable": False},
    "star_sky": {"label": "star sky", "lights": ["r3_uv"], "dimmable": False, "special": "star_sky"},
}

LIGHT_NAME_BY_ID = {
    "1": "r2_chess",
    "2": "r2_schronk",
    "3": "r1_bild",
    "4": "r1_stuen",
    "5": "r3_slider",
    "6": "r3_cage",
    "7": "torch_stiege",
    "8": "torch_r2r3",
    "9": "torch_r2",
    "10": "r3_uv",
}

MODE_OPTIONS = [
    "MODE_STANDBY",
    "MODE_PREPARE",
    "MODE_INGAME",
    "MODE_MAINTENANCE",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def load_hint_store() -> dict[str, list[dict[str, Any]]]:
    if not HINTS_PATH.exists():
        return {}
    try:
        data = json.loads(HINTS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_hint_store(data: dict[str, list[dict[str, Any]]]) -> None:
    HINTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


@dataclass
class DashboardStore:
    lock: threading.RLock = field(default_factory=threading.RLock)
    game_state: dict[str, Any] = field(default_factory=lambda: {"mode": "MODE_STANDBY", "active": [], "solved": [], "seq": 0})
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_state_ts: dict[str, float] = field(default_factory=dict)
    node_last_hb: dict[str, float] = field(default_factory=dict)
    locks: dict[str, dict[str, Any]] = field(default_factory=dict)
    lights: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_hints: dict[str, list[dict[str, Any]]] = field(default_factory=load_hint_store)

    def update_game_state(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.game_state = payload

    def update_node_hb(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.node_last_hb[node_id] = time.monotonic()
            if node_id not in self.node_states:
                self.node_states[node_id] = {}
            self.node_states[node_id]["hb"] = payload

    def update_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.node_states[node_id] = payload
            self.node_state_ts[node_id] = time.monotonic()

    def update_lock_state(self, lock_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.locks[lock_id] = payload

    def update_light_state(self, light_name: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.lights[light_name] = payload

    def add_hint(self, riddle_id: str, hint_text: str) -> list[dict[str, Any]]:
        hint = {"id": f"{int(time.time()*1000)}", "text": hint_text.strip()}
        with self.lock:
            items = self.local_hints.setdefault(riddle_id, [])
            items.append(hint)
            save_hint_store(self.local_hints)
            return list(items)

    def remove_hint(self, riddle_id: str, hint_id: str) -> list[dict[str, Any]]:
        with self.lock:
            items = self.local_hints.setdefault(riddle_id, [])
            self.local_hints[riddle_id] = [x for x in items if str(x.get('id')) != str(hint_id)]
            save_hint_store(self.local_hints)
            return list(self.local_hints[riddle_id])

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            game = json.loads(json.dumps(self.game_state))
            node_states = json.loads(json.dumps(self.node_states))
            locks = json.loads(json.dumps(self.locks))
            lights = json.loads(json.dumps(self.lights))
            node_last_hb = dict(self.node_last_hb)
            local_hints = json.loads(json.dumps(self.local_hints))

        return {
            "game": self._build_game_summary(game),
            "controllers": self._build_controller_summary(node_last_hb, node_states),
            "locks": self._build_lock_summary(locks),
            "lights": self._build_light_summary(lights, node_states),
            "riddles": self._build_riddle_summary(game, node_states, node_last_hb, local_hints),
            "meta": {
                "broker": BROKER_HOST,
                "updated_at": now_utc().isoformat(),
            },
        }

    def _build_game_summary(self, game: dict[str, Any]) -> dict[str, Any]:
        run = game.get("run") or {}
        started_at = parse_iso(run.get("started_at"))
        ended_at = parse_iso(run.get("ended_at"))
        elapsed_s = 0
        if started_at is not None:
            end_ref = ended_at or now_utc()
            elapsed_s = max(0, int((end_ref - started_at).total_seconds()))
        return {
            "mode": game.get("mode", "MODE_STANDBY"),
            "players": list(run.get("players") or []),
            "started_at": run.get("started_at"),
            "ended_at": run.get("ended_at"),
            "elapsed_s": elapsed_s,
            "active": list(game.get("active") or []),
            "solved": list(game.get("solved") or []),
            "seq": game.get("seq", 0),
        }

    def _build_controller_summary(self, node_last_hb: dict[str, float], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        out = []
        for node_id, label in [("lighting", "Lighting Controller"), ("maglock", "Maglock Controller")]:
            last = node_last_hb.get(node_id)
            online = (last is not None) and (now_mono - last <= 8.0)
            hb = node_states.get(node_id, {}).get("hb", {})
            out.append({
                "id": node_id,
                "label": label,
                "online": online,
                "fw": hb.get("fw") if isinstance(hb, dict) else None,
                "up": hb.get("up") if isinstance(hb, dict) else None,
            })
        return out

    def _build_lock_summary(self, locks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in LOCKS:
            payload = locks.get(item['id'], {})
            state = str(payload.get('state', '')).upper()
            is_open = None
            if state == 'OPEN':
                is_open = True
            elif state == 'CLOSED':
                is_open = False
            out.append({
                'id': item['id'],
                'label': item['label'],
                'kind': item['kind'],
                'is_open': is_open,
                'button': 'Close' if item['kind'] == 'toggle' and is_open else 'Open',
            })
        return out

    def _build_light_summary(self, lights: dict[str, dict[str, Any]], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        star_sky_state = node_states.get('star_sky', {})
        star_enabled = bool(star_sky_state.get('enabled') or star_sky_state.get('moduleEnabled') or star_sky_state.get('module_enabled'))
        for key, cfg in LIGHT_GROUPS.items():
            group_entries = [lights.get(name, {}) for name in cfg['lights']]
            known = [x for x in group_entries if x]
            any_on = any(bool(x.get('on', False)) for x in known)
            if key == 'star_sky':
                any_on = any_on or star_enabled
            out.append({
                'id': key,
                'label': cfg['label'],
                'on': any_on,
                'button': 'Off' if any_on else 'On',
            })
        return out

    def _build_riddle_summary(self, game: dict[str, Any], node_states: dict[str, dict[str, Any]], node_last_hb: dict[str, float], local_hints: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        solved = set(game.get('solved') or [])
        active = set(game.get('active') or [])
        timings = ((game.get('run') or {}).get('riddle_timings') or {})
        now_mono = time.monotonic()
        out = []
        for row in RIDDLES:
            riddle_id = row['id']
            node_id = row['node_id']
            state_payload = node_states.get(node_id, {}) if node_id else {}
            if row['manual']:
                hb_state = 'manual'
            else:
                hb_age = None
                if node_id in node_last_hb:
                    hb_age = round(now_mono - node_last_hb[node_id], 1)
                hb_state = 'offline'
                if hb_age is not None and hb_age <= 15.0:
                    hb_state = f'online ({hb_age}s)'
            if riddle_id in solved:
                lifecycle = 'solved'
                solved_text = 'true'
            elif riddle_id in active:
                lifecycle = 'ingame'
                solved_text = 'false'
            else:
                lifecycle = 'standby'
                solved_text = 'false'
            solve_time = ''
            timing = timings.get(riddle_id) if isinstance(timings, dict) else None
            if isinstance(timing, dict) and timing.get('solve_time_from_run_start_s') is not None:
                solve_time = self._fmt_seconds(timing['solve_time_from_run_start_s'])
            tries = self._extract_tries(riddle_id, state_payload)
            info = self._extract_info(riddle_id, state_payload)
            out.append({
                'id': riddle_id,
                'label': row['label'],
                'hb_state': f'{hb_state} / {lifecycle}',
                'solved': solved_text,
                'solve_time': solve_time,
                'tries': tries,
                'info': info,
                'hints': list(local_hints.get(riddle_id, [])),
            })
        return out

    @staticmethod
    def _fmt_seconds(value: float | int) -> str:
        total = int(round(float(value)))
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _extract_tries(node_id: str, state_payload: dict[str, Any]) -> str:
        if not state_payload or node_id == 'piano':
            return ''
        for key in ['tries', 'attempt', 'attempts', 'attempt_idx', 'attemptIndex']:
            if key in state_payload:
                return str(state_payload.get(key, ''))
        return ''

    @staticmethod
    def _extract_info(node_id: str, state_payload: dict[str, Any]) -> str:
        if not state_payload:
            return ''
        if node_id == 'images':
            parts = []
            for key in ['jesus', 'flowers', 'nature', 'doll']:
                if key in state_payload:
                    parts.append(f'{key}: {state_payload.get(key)}')
            return '   '.join(parts)
        if node_id == 'piano':
            parts = []
            for key in ['seq', 'sequence', 'decoded', 'top_predictions', 'decoded_predictions']:
                if key in state_payload:
                    parts.append(f'{key}: {state_payload.get(key)}')
            return '   '.join(parts)
        if node_id == 'chess':
            parts = []
            for key in ['king', 'queen', 'rook', 'knight']:
                if key in state_payload:
                    parts.append(f'{key}: {state_payload.get(key)}')
            return '   '.join(parts)
        if node_id == 'star_slider':
            parts = []
            for key in ['left', 'middle', 'right', 'attempted_star_signs']:
                if key in state_payload:
                    parts.append(f'{key}: {state_payload.get(key)}')
            return '   '.join(parts)
        parts = []
        for key, value in state_payload.items():
            if key in {'id', 'fw', 'up', 'ts', 'time_valid'}:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            parts.append(f'{key}: {value}')
            if len(parts) >= 4:
                break
        return '   '.join(parts)


store = DashboardStore()
app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'), static_folder=str(BASE_DIR / 'static'))

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)


def parse_json_payload(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload.decode('utf-8', errors='ignore'))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
    subs = [
        (TOPIC_GAME_STATE, 0),
        ('+/state', 0),
        ('+/hb', 0),
        ('maglock/lock/+/state', 0),
        ('lighting/mosfet/+/state', 0),
    ]
    for topic, qos in subs:
        client.subscribe(topic, qos=qos)


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    topic = msg.topic
    data = parse_json_payload(msg.payload)
    if data is None:
        return
    if topic == TOPIC_GAME_STATE:
        store.update_game_state(data)
        return
    if topic.endswith('/hb'):
        node_id = topic.split('/', 1)[0]
        store.update_node_hb(node_id, data)
        return
    if topic.startswith('maglock/lock/') and topic.endswith('/state'):
        parts = topic.split('/')
        if len(parts) >= 4:
            store.update_lock_state(parts[2], data)
        return
    if topic.startswith('lighting/mosfet/') and topic.endswith('/state'):
        parts = topic.split('/')
        if len(parts) >= 4:
            light_name = data.get('light') or LIGHT_NAME_BY_ID.get(parts[2]) or parts[2]
            store.update_light_state(str(light_name), data)
        return
    if topic.endswith('/state'):
        node_id = topic.split('/', 1)[0]
        store.update_node_state(node_id, data)
        return


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
mqtt_client.loop_start()


def mqtt_publish(topic: str, payload: dict[str, Any] | str) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    mqtt_client.publish(topic, body, qos=0, retain=False)


@app.get('/')
def index() -> str:
    return render_template('index.html')


@app.get('/api/state')
def api_state() -> Any:
    return jsonify(store.snapshot())


@app.post('/api/mode')
def api_mode() -> Any:
    data = request.get_json(force=True)
    mode = str(data.get('mode', '')).strip().upper()
    aliases = {
        'STANDBY': 'MODE_STANDBY',
        'PREPARE': 'MODE_PREPARE',
        'INGAME': 'MODE_INGAME',
        'MAINTENANCE': 'MODE_MAINTENANCE',
    }
    mode = aliases.get(mode, mode)
    if mode not in MODE_OPTIONS:
        return jsonify({'ok': False, 'error': 'invalid mode'}), 400
    mqtt_publish(TOPIC_GAME_CMD, {'cmd': 'set_mode', 'mode': mode})
    return jsonify({'ok': True})


@app.post('/api/players')
def api_players() -> Any:
    data = request.get_json(force=True)
    players = data.get('players') or []
    if not isinstance(players, list):
        return jsonify({'ok': False, 'error': 'players must be a list'}), 400
    cleaned = [str(x).strip() for x in players if str(x).strip()]
    mqtt_publish(TOPIC_GAME_CMD, {'cmd': 'set_players', 'players': cleaned})
    return jsonify({'ok': True, 'players': cleaned})


@app.post('/api/solve')
def api_solve() -> Any:
    data = request.get_json(force=True)
    node = str(data.get('node', '')).strip()
    if not node:
        return jsonify({'ok': False, 'error': 'node required'}), 400
    if node == 'free_sissi':
        mqtt_publish(TOPIC_GAME_CMD, {'cmd': 'set_mode', 'mode': 'MODE_STANDBY'})
        return jsonify({'ok': True, 'finished': True})
    mqtt_publish(TOPIC_GAME_CMD, {'cmd': 'solve', 'node': node})
    return jsonify({'ok': True})


@app.post('/api/lock')
def api_lock() -> Any:
    data = request.get_json(force=True)
    lock_id = str(data.get('lock', '')).strip()
    action = str(data.get('action', '')).strip().lower()
    if lock_id not in {item['id'] for item in LOCKS}:
        return jsonify({'ok': False, 'error': 'invalid lock'}), 400
    if action not in {'open', 'close'}:
        return jsonify({'ok': False, 'error': 'invalid action'}), 400
    mqtt_publish(TOPIC_MAGLOCK_CMD, {'cmd': action, 'lock': lock_id})
    return jsonify({'ok': True})


@app.post('/api/light')
def api_light() -> Any:
    data = request.get_json(force=True)
    group_id = str(data.get('group', '')).strip()
    action = str(data.get('action', '')).strip().lower()
    cfg = LIGHT_GROUPS.get(group_id)
    if cfg is None:
        return jsonify({'ok': False, 'error': 'invalid group'}), 400

    if group_id == 'star_sky':
        if action not in {'on', 'off'}:
            return jsonify({'ok': False, 'error': 'star sky supports only on/off'}), 400
        mqtt_publish(TOPIC_STAR_SKY_CMD, 'SOLVE' if action == 'on' else 'DISABLE')
        mqtt_publish(TOPIC_LIGHTING_CMD, {'cmd': 'turn_on' if action == 'on' else 'turn_off', 'light': 'r3_uv'})
        return jsonify({'ok': True})

    for light_name in cfg['lights']:
        mqtt_publish(TOPIC_LIGHTING_CMD, {'cmd': 'turn_on' if action == 'on' else 'turn_off', 'light': light_name})
    return jsonify({'ok': True})


@app.post('/api/hints')
def api_add_hint() -> Any:
    data = request.get_json(force=True)
    riddle = str(data.get('riddle', '')).strip()
    hint_text = str(data.get('text', '')).strip()
    if not riddle or not hint_text:
        return jsonify({'ok': False, 'error': 'riddle and text required'}), 400
    mqtt_publish(TOPIC_GAME_CMD, {'cmd': 'add_hint', 'riddle': riddle, 'hint_text': hint_text})
    items = store.add_hint(riddle, hint_text)
    return jsonify({'ok': True, 'hints': items})


@app.delete('/api/hints/<riddle>/<hint_id>')
def api_delete_hint(riddle: str, hint_id: str) -> Any:
    items = store.remove_hint(riddle, hint_id)
    return jsonify({'ok': True, 'hints': items})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('ER1_DASHBOARD_PORT', '8080')), debug=False)
