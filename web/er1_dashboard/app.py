
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
BROKER_HOST = os.getenv("ER1_MQTT_HOST", "192.168.0.10")
BROKER_PORT = int(os.getenv("ER1_MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("ER1_DASHBOARD_CLIENT_ID", "er1_dashboard")
HINTS_PATH = BASE_DIR / "dashboard_hints.json"
GAME_DB_PATH = Path(
    os.getenv(
        "ER1_GAME_DB_PATH",
        str(BASE_DIR.parent / "scripts" / "game_master" / "data" / "game_master.sqlite3")
    )
).resolve()

REMOVED_GAMES_DIR = GAME_DB_PATH.parent / "removed"
REMOVED_GAME_DB_PATH = REMOVED_GAMES_DIR / GAME_DB_PATH.name
RUN_JSON_DIR = GAME_DB_PATH.parent / "game_runs"


def _normalize_existing_games_db_schema(db_path: Path) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if "games" not in tables:
            return
        existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()]
        needs_rebuild = (
            "player_names_json" in existing_columns
            or "player_names" in existing_columns
            or ("players_count" not in existing_columns and "player_count" in existing_columns)
        )
        if not needs_rebuild:
            if "players_count" not in existing_columns:
                conn.execute("ALTER TABLE games ADD COLUMN players_count INTEGER NOT NULL DEFAULT 0")
            return

        players_expr = "COALESCE(players_count, player_count, 0)" if "player_count" in existing_columns else "COALESCE(players_count, 0)"
        hint_expr = "COALESCE(hint_count, 0)" if "hint_count" in existing_columns else "0"
        leaderboard_expr = "leaderboard_code" if "leaderboard_code" in existing_columns else "NULL"

        conn.executescript(f"""
            ALTER TABLE games RENAME TO games_old;

            CREATE TABLE games (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_s REAL,
                players_count INTEGER NOT NULL DEFAULT 0,
                hint_count INTEGER NOT NULL DEFAULT 0,
                leaderboard_code TEXT
            );

            INSERT INTO games (id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code)
            SELECT id, date, started_at, ended_at, duration_s, {players_expr}, {hint_expr}, {leaderboard_expr}
            FROM games_old;

            DROP TABLE games_old;
        """)


_normalize_existing_games_db_schema(GAME_DB_PATH)

TOPIC_GAME_STATE = "game/state"
TOPIC_GAME_CMD = "game/cmd"
TOPIC_LIGHTING_CMD = "lighting/cmd"
TOPIC_MAGLOCK_CMD = "maglock/cmd"
TOPIC_STAR_SKY_CMD = "star_sky/cmd"

PHASE_META: dict[int, dict[str, Any]] = {
    0: {"name": "standby", "active": (), "solved": ()},
    1: {"name": "maintenance", "active": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider", "sissi"), "solved": ()},
    2: {"name": "prepare", "active": (), "solved": ()},
    3: {"name": "start", "active": ("images",), "solved": ()},
    4: {"name": "piano", "active": ("piano",), "solved": ("images",)},
    5: {"name": "prison", "active": ("open_prison",), "solved": ("images", "piano")},
    6: {"name": "wheel", "active": ("mount_wheel",), "solved": ("images", "piano", "open_prison")},
    7: {"name": "rope", "active": ("rope_paths",), "solved": ("images", "piano", "open_prison", "mount_wheel")},
    8: {"name": "tangram_magnet", "active": ("tangram", "magnet"), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths")},
    9: {"name": "chess", "active": ("chess",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet")},
    10: {"name": "knocking", "active": ("knocking", "candles"), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess")},
    11: {"name": "candles", "active": ("candles",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking")},
    12: {"name": "stars", "active": ("star_slider",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles")},
    13: {"name": "sissi", "active": ("sissi",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider")},
    14: {"name": "finished", "active": (), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider", "sissi")},
}

RIDDLES = [
    {"id": "images", "label": "Images", "node_id": "images_piano", "manual": False},
    {"id": "piano", "label": "Piano", "node_id": "images_piano", "manual": False},
    {"id": "open_prison", "label": "Prison", "node_id": None, "manual": True},
    {"id": "mount_wheel", "label": "Wheel", "node_id": None, "manual": True},
    {"id": "rope_paths", "label": "Rope", "node_id": None, "manual": True},
    {"id": "tangram", "label": "Tangram", "node_id": None, "manual": True},
    {"id": "magnet", "label": "Magnet", "node_id": None, "manual": True},
    {"id": "chess", "label": "Chess", "node_id": "chess", "manual": False},
    {"id": "knocking", "label": "Knocking", "node_id": "knocking", "manual": False},
    {"id": "candles", "label": "Candles", "node_id": "candles", "manual": False},
    {"id": "star_slider", "label": "Stars", "node_id": "star_slider", "manual": False},
    {"id": "sissi", "label": "Sissi", "node_id": None, "manual": True},
]

NODE_LABELS = [
    ("lighting", "Lighting Controller"),
    ("maglock", "Maglock Controller"),
    ("images_piano", "Images / Piano"),
    ("chess", "Chess"),
    ("knocking", "Knocking"),
    ("candles", "Candles"),
    ("star_slider", "Star Slider"),
    ("star_sky", "Star Sky"),
]

LOCKS = [
    {"id": "r2", "label": "r2", "kind": "toggle"},
    {"id": "r3", "label": "r3", "kind": "toggle"},
    {"id": "images", "label": "images", "kind": "pulse"},
    {"id": "knocking", "label": "knocking", "kind": "pulse"},
    {"id": "slider", "label": "slider", "kind": "pulse"},
]

LIGHT_GROUPS = {
    "entrance": {"label": "entrance", "lights": ["torch_stiege"], "dimmable": False},
    "r1": {"label": "r1", "lights": ["r1_stuen", "r1_bild"], "dimmable": False},
    "r2_main": {"label": "r2 chess + kostn", "lights": ["r2_chess", "r2_schronk"], "dimmable": False},
    "r2_torch": {"label": "r2 torch", "lights": ["torch_r2"], "dimmable": False},
    "r3_main": {"label": "r3 slider + cage", "lights": ["r3_slider", "r3_cage"], "dimmable": True},
    "r3_torch": {"label": "r2r3 torch", "lights": ["torch_r2r3"], "dimmable": False},
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


def pretty_phase_name(name: str) -> str:
    text = str(name or "").replace("_", " ").strip()
    if not text:
        return ""
    parts = text.split()
    return " ".join(p.capitalize() for p in parts)


@dataclass
class DashboardStore:
    lock: threading.RLock = field(default_factory=threading.RLock)
    game_state: dict[str, Any] = field(default_factory=lambda: {"phase": 0})
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    riddle_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_last_hb: dict[str, float] = field(default_factory=dict)
    locks: dict[str, dict[str, Any]] = field(default_factory=dict)
    lights: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_hints: dict[str, list[dict[str, Any]]] = field(default_factory=load_hint_store)
    local_players_count_override: int | None = None

    def update_game_state(self, payload: dict[str, Any]) -> None:
        with self.lock:
            next_payload = dict(payload or {})
            if self.local_players_count_override is not None:
                try:
                    incoming_players_count = parse_players_count_input(next_payload.get("players_count", 0))
                except Exception:
                    incoming_players_count = None
                if incoming_players_count == self.local_players_count_override:
                    self.local_players_count_override = None
                else:
                    next_payload["players_count"] = self.local_players_count_override
            self.game_state = next_payload
            try:
                phase = int(next_payload.get("phase", 0))
            except Exception:
                phase = 0
            if phase in {1, 2}:
                self._reset_riddle_display_state_locked()

    def set_local_phase(self, mode: str) -> None:
        phase_by_mode = {"standby": 0, "maintenance": 1, "prepare": 2}
        phase = phase_by_mode.get(str(mode or "").strip().lower())
        if phase is None:
            return
        with self.lock:
            current = dict(self.game_state)
            try:
                prev_phase = int(current.get("phase", 0))
            except Exception:
                prev_phase = 0
            current["last_phase"] = prev_phase
            current["phase"] = phase
            current["timer_running"] = False
            current["current_riddle_name"] = ""
            current["current_riddle_started_at"] = None
            self.game_state = current
            if phase in {1, 2}:
                self._reset_riddle_display_state_locked()

    def set_local_players_count(self, players_count: int) -> None:
        with self.lock:
            current = dict(self.game_state)
            current["players_count"] = int(players_count)
            self.local_players_count_override = int(players_count)
            self.game_state = current

    def _clear_node_payload_locked(self, node_id: str) -> None:
        existing = self.node_states.get(node_id, {}) or {}
        hb = existing.get("hb") if isinstance(existing, dict) else None
        self.node_states[node_id] = {"hb": hb} if hb is not None else {}

    def _reset_riddle_display_state_locked(self) -> None:
        for node_id in ["images_piano", "chess", "knocking", "candles", "star_slider"]:
            self._clear_node_payload_locked(node_id)
        self.riddle_states["images"] = {"id": "images", "buttons": {}}
        self.riddle_states["piano"] = {"id": "piano", "played_notes": []}
        self.riddle_states["chess"] = {"id": "chess", "reader_labels": {}}
        self.riddle_states["knocking"] = {"id": "knocking", "tries": 0, "attempted_sequences": []}
        self.riddle_states["candles"] = {"id": "candles", "tries": 0, "attempted_sequences": []}
        self.riddle_states["star_slider"] = {"id": "star_slider", "tries": 0, "attempted_star_signs": [], "reader_positions": {}}

    def update_node_hb(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.node_last_hb[node_id] = time.monotonic()
            self.node_states.setdefault(node_id, {})["hb"] = payload

    def update_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            previous_node = self.node_states.get(node_id, {}) if isinstance(self.node_states.get(node_id), dict) else {}
            merged_node = dict(previous_node)
            merged_node.update(payload)
            self.node_states[node_id] = merged_node

            if node_id == "images_piano":
                if self._is_images_payload(payload):
                    prev = self.riddle_states.get("images", {}) if isinstance(self.riddle_states.get("images"), dict) else {}
                    merged = dict(prev)
                    merged.update(payload)
                    merged["id"] = "images"
                    self.riddle_states["images"] = merged
                    return
                if self._is_piano_payload(payload):
                    prev = self.riddle_states.get("piano", {}) if isinstance(self.riddle_states.get("piano"), dict) else {}
                    merged = dict(prev)
                    merged.update(payload)
                    merged["id"] = "piano"
                    played_notes = list(prev.get("played_notes") or [])
                    encoded = str(payload.get("encoded") or "").strip()
                    if encoded:
                        played_notes.append({
                            "encoded": encoded,
                            "accepted": bool(payload.get("accepted", False)),
                        })
                    merged["played_notes"] = played_notes[-40:]
                    self.riddle_states["piano"] = merged
                    return

            riddle_id = str(payload.get("id", "")).strip()
            if riddle_id:
                prev = self.riddle_states.get(riddle_id, {}) if isinstance(self.riddle_states.get(riddle_id), dict) else {}
                merged = dict(prev)
                merged.update(payload)
                if riddle_id in {"knocking", "candles"}:
                    attempts = list(merged.get("attempted_sequences") or [])
                    last_attempt = str(payload.get("last_attempt") or "").strip()
                    if last_attempt and (not attempts or attempts[-1] != last_attempt):
                        attempts.append(last_attempt)
                    merged["attempted_sequences"] = attempts
                elif riddle_id == "star_slider":
                    attempts = list(merged.get("attempted_star_signs") or [])
                    last_positions = payload.get("last_attempt_positions")
                    if isinstance(last_positions, dict) and (not attempts or attempts[-1] != last_positions):
                        attempts.append(last_positions)
                    merged["attempted_star_signs"] = attempts
                self.riddle_states[riddle_id] = merged

    @staticmethod
    def _is_images_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if "buttons" in payload and isinstance(payload.get("buttons"), dict):
            return True
        return any(key in payload for key in ["jesus", "blumen", "flowers", "natur", "nature", "puppe", "doll"])

    @staticmethod
    def _is_piano_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(key in payload for key in ["encoded", "note", "accepted", "top3", "margins"])

    def update_lock_state(self, lock_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.locks[lock_id] = payload

    def update_light_state(self, light_name: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.lights[light_name] = payload

    def add_hint(self, riddle_id: str, hint_text: str) -> list[dict[str, Any]]:
        hint = {"id": f"{int(time.time() * 1000)}", "text": hint_text.strip()}
        with self.lock:
            items = self.local_hints.setdefault(riddle_id, [])
            items.append(hint)
            save_hint_store(self.local_hints)
            return list(items)

    def remove_hint(self, riddle_id: str, hint_id: str) -> list[dict[str, Any]]:
        with self.lock:
            items = self.local_hints.setdefault(riddle_id, [])
            self.local_hints[riddle_id] = [x for x in items if str(x.get("id")) != str(hint_id)]
            save_hint_store(self.local_hints)
            return list(self.local_hints[riddle_id])

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            game = json.loads(json.dumps(self.game_state))
            node_states = json.loads(json.dumps(self.node_states))
            locks = json.loads(json.dumps(self.locks))
            lights = json.loads(json.dumps(self.lights))
            riddle_states = json.loads(json.dumps(self.riddle_states))
            node_last_hb = dict(self.node_last_hb)
            local_hints = json.loads(json.dumps(self.local_hints))

        return {
            "game": self._build_game_summary(game),
            "nodes": self._build_node_summary(node_last_hb, node_states),
            "locks": self._build_lock_summary(locks),
            "lights": self._build_light_summary(lights, node_states),
            "riddles": self._build_riddle_summary(game, node_states, riddle_states, node_last_hb, local_hints),
            "meta": {"broker": BROKER_HOST},
        }

    def _build_game_summary(self, game: dict[str, Any]) -> dict[str, Any]:
        phase = int(game.get("phase", 1))
        last_phase = game.get("last_phase")
        phase_meta = PHASE_META.get(phase, {"name": f"phase_{phase}", "active": (), "solved": ()})
        last_name = ""
        if last_phase is not None:
            last_name = PHASE_META.get(int(last_phase), {"name": f"phase_{last_phase}"}).get("name", "")
        phase_name_pretty = pretty_phase_name(phase_meta["name"])
        last_name_pretty = pretty_phase_name(last_name)
        timer_running = bool(game.get("timer_running", False))
        started_at = game.get("game_started_at") or game.get("started_at")
        last_riddle_solved_at = game.get("last_riddle_solved_at")
        current_riddle_name = ""
        active_riddles = tuple(phase_meta.get("active", ()) or ())
        if timer_running and active_riddles:
            current_riddle_name = str(active_riddles[0])
        current_riddle_started_at = None
        if current_riddle_name:
            current_riddle_started_at = last_riddle_solved_at or started_at

        def _seconds_since(value: Any) -> int:
            if not value:
                return 0
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
            except Exception:
                return 0

        return {
            "phase": phase,
            "phase_name": phase_meta["name"],
            "phase_name_pretty": phase_name_pretty,
            "phase_display": f"{phase}: {phase_name_pretty}",
            "last_phase": last_phase,
            "last_phase_name": last_name,
            "last_phase_name_pretty": last_name_pretty,
            "players_count": int(game.get("players_count") or 0),
            "timer_running": timer_running,
            "elapsed_s": _seconds_since(started_at) if timer_running else 0,
            "started_at": started_at,
            "last_riddle_solved_at": last_riddle_solved_at,
            "current_riddle_elapsed_s": _seconds_since(current_riddle_started_at) if timer_running else 0,
            "current_riddle_name": current_riddle_name,
            "current_riddle_started_at": current_riddle_started_at,
        }

    def _build_node_summary(self, node_last_hb: dict[str, float], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        out = []
        for node_id, label in NODE_LABELS:
            last = node_last_hb.get(node_id)
            online = (last is not None) and (now_mono - last <= 15.0)
            hb = node_states.get(node_id, {}).get("hb", {})
            uptime = hb.get("up") if isinstance(hb, dict) else None
            status = "online" if online else "offline"
            if online and isinstance(uptime, (int, float)):
                status = f"online ({int(uptime)}s)"
            out.append({"id": node_id, "label": label, "online": online, "status": status})
        return out

    def _build_lock_summary(self, locks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in LOCKS:
            payload = locks.get(item["id"], {})
            state = str(payload.get("state", "")).upper()
            if state == "OPEN":
                is_open = True
                state_label = "open"
            elif state == "CLOSED":
                is_open = False
                state_label = "closed"
            else:
                is_open = None
                state_label = "unknown"
            action = "close" if item["kind"] == "toggle" and is_open else "open"
            out.append({
                "id": item["id"],
                "label": item["label"],
                "kind": item["kind"],
                "is_open": is_open,
                "state_label": state_label,
                "button": "Close" if action == "close" else "Open",
                "state_class": "is-open" if is_open else ("is-closed" if is_open is False else "is-unknown"),
            })
        return out

    def _build_light_summary(self, lights: dict[str, dict[str, Any]], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        star_sky_state = node_states.get("star_sky", {})
        star_enabled = bool(star_sky_state.get("enabled") or star_sky_state.get("moduleEnabled") or star_sky_state.get("module_enabled"))
        for key, cfg in LIGHT_GROUPS.items():
            entries = [lights.get(name, {}) for name in cfg["lights"]]
            known = [x for x in entries if x]
            any_on = any(bool(x.get("on", False)) for x in known)
            pct_values = []
            for x in known:
                try:
                    pct_values.append(int(x.get("pct", 0)))
                except Exception:
                    pass
            pct = max(pct_values) if pct_values else (100 if any_on else 0)
            if key == "star_sky":
                any_on = any_on or star_enabled
                pct = 100 if any_on else 0
            out.append({
                "id": key,
                "label": cfg["label"],
                "on": any_on,
                "pct": pct,
                "button": "Off" if any_on else "On",
                "dimmable": bool(cfg.get("dimmable")),
                "state_label": f"{'on' if any_on else 'off'} ({pct}%)" if cfg.get("dimmable") else ("on" if any_on else "off"),
                "state_class": "is-on" if any_on else "is-off",
            })
        return out

    def _build_riddle_summary(self, game: dict[str, Any], node_states: dict[str, dict[str, Any]], riddle_states: dict[str, dict[str, Any]], node_last_hb: dict[str, float], local_hints: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        phase = int(game.get("phase", 1))
        meta = PHASE_META.get(phase, {"active": (), "solved": ()})
        active = set(meta.get("active", ()))
        solved = set(meta.get("solved", ()))
        run = game.get("run") or {}
        riddle_timings = run.get("riddle_timings") or {}
        now_mono = time.monotonic()
        out = []
        for row in RIDDLES:
            riddle_id = row["id"]
            node_id = row["node_id"]
            state_payload = riddle_states.get(riddle_id) or (node_states.get(node_id, {}) if node_id else {})

            timing = riddle_timings.get(riddle_id) or {}
            if riddle_id in solved:
                phase_state = "solved"
            elif riddle_id in active and bool(timing.get("solved")):
                phase_state = "solved_pending"
            elif bool(timing.get("solved")):
                phase_state = "solved"
            elif riddle_id in active:
                phase_state = "active"
            else:
                phase_state = "pending"

            if row["manual"]:
                node_status = "manual"
                online = None
            else:
                last = node_last_hb.get(node_id or "")
                online = (last is not None) and (now_mono - last <= 15.0)
                hb = node_states.get(node_id or "", {}).get("hb", {})
                uptime = hb.get("up") if isinstance(hb, dict) else None
                node_status = "online" if online else "offline"
                if online and isinstance(uptime, (int, float)):
                    node_status = f"online ({int(uptime)}s)"

            phase_state_label = "solved" if phase_state == "solved_pending" else phase_state
            out.append({
                "id": riddle_id,
                "label": row["label"],
                "manual": row["manual"],
                "phase_state": phase_state,
                "phase_state_label": phase_state_label,
                "phase_state_class": f"phase-{phase_state}",
                "node_status": node_status,
                "node_status_class": "node-manual" if row["manual"] else ("node-online" if online else "node-offline"),
                "tries": self._extract_tries(state_payload),
                "info": self._extract_info(riddle_id, state_payload),
                "images_buttons": self._extract_images_buttons(riddle_id, state_payload),
                "chess_slots": self._extract_chess_slots(riddle_id, state_payload),
                "attempts_summary": self._extract_attempts_summary(riddle_id, state_payload),
                "star_slider_summary": self._extract_star_slider_summary(riddle_id, state_payload),
                "piano_summary": self._extract_piano_summary(riddle_id, state_payload),
                "hints": list(local_hints.get(riddle_id, [])),
            })
        return out

    @staticmethod
    def _extract_tries(state_payload: dict[str, Any]) -> str:
        if not state_payload:
            return ""
        for key in ["tries", "attempt", "attempts", "attempt_idx", "attemptIndex"]:
            if key in state_payload:
                return str(state_payload.get(key, ""))
        return ""

    @staticmethod
    def _extract_images_buttons(riddle_id: str, state_payload: dict[str, Any]) -> dict[str, bool] | None:
        if riddle_id != "images" or not state_payload:
            return None
        buttons = state_payload.get("buttons")
        if isinstance(buttons, dict):
            return {
                "jesus": bool(buttons.get("jesus", False)),
                "blumen": bool(buttons.get("blumen", buttons.get("flowers", False))),
                "natur": bool(buttons.get("natur", buttons.get("nature", False))),
                "puppe": bool(buttons.get("puppe", buttons.get("doll", False))),
            }
        return {
            "jesus": bool(state_payload.get("jesus", False)),
            "blumen": bool(state_payload.get("blumen", state_payload.get("flowers", False))),
            "natur": bool(state_payload.get("natur", state_payload.get("nature", False))),
            "puppe": bool(state_payload.get("puppe", state_payload.get("doll", False))),
        }

    @staticmethod
    def _stringify_scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip()

    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [cls._stringify_scalar(x) for x in value if cls._stringify_scalar(x)]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("[") and text.endswith("]"):
                try:
                    loaded = json.loads(text)
                    if isinstance(loaded, list):
                        return [cls._stringify_scalar(x) for x in loaded if cls._stringify_scalar(x)]
                except Exception:
                    pass
            if any(sep in text for sep in [",", " "]):
                return [part for part in re.split(r"[\s,]+", text) if part]
            return [text]
        return [cls._stringify_scalar(value)]

    @classmethod
    def _normalize_knocking_attempt(cls, value: Any) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ""
            if raw.isdigit():
                return raw
            tokens = [part for part in re.split(r"[^A-Za-z0-9]+", raw) if part]
            return "".join(tokens)
        return "".join(cls._string_list(value))

    @classmethod
    def _normalize_flat_attempt(cls, value: Any) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ""
            if raw.isdigit():
                return raw
            tokens = [part for part in re.split(r"[^A-Za-z0-9]+", raw) if part]
            return "".join(tokens)
        return "".join(cls._string_list(value))

    @staticmethod
    def _extract_chess_slots(riddle_id: str, state_payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if riddle_id != "chess" or not state_payload:
            return None
        expected = {
            "queen": "QUEEN",
            "knight": "HORSE",
            "rook": "ROOK",
            "king": "KING",
        }
        raw_labels = state_payload.get("reader_labels") or state_payload.get("reader_label") or state_payload
        labels: dict[str, Any] = {}
        if isinstance(raw_labels, dict):
            labels = {str(key).strip().lower(): value for key, value in raw_labels.items()}
        elif isinstance(raw_labels, (list, tuple)):
            ordered = list(raw_labels)[:4]
            labels = {slot: ordered[idx] if idx < len(ordered) else "EMPTY" for idx, slot in enumerate(["queen", "knight", "rook", "king"])}

        out: list[dict[str, Any]] = []
        for slot, target in expected.items():
            value = str(labels.get(slot, "EMPTY")).strip().upper() or "EMPTY"
            out.append({
                "slot": slot.capitalize(),
                "value": value,
                "correct": value == target,
            })
        return out

    @classmethod
    def _extract_attempts_summary(cls, riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id not in {"knocking", "candles"} or not state_payload:
            return None
        tries = cls._extract_tries(state_payload)
        attempts_raw = state_payload.get("attempted_sequences")
        attempts: list[str] = []
        items = attempts_raw if isinstance(attempts_raw, list) else ([attempts_raw] if attempts_raw is not None else [])
        for item in items:
            text = cls._normalize_knocking_attempt(item) if riddle_id == "knocking" else cls._normalize_flat_attempt(item)
            if text:
                attempts.append(text)
        return {"tries": tries, "attempts": attempts}

    @classmethod
    def _extract_star_slider_values(cls, value: Any) -> list[str]:
        order = ["r2", "r1", "r0"]
        if isinstance(value, dict):
            return [str(value.get(key, "none") or "none").strip() for key in order]
        if isinstance(value, (list, tuple)):
            raw = [str(x).strip() or "none" for x in list(value)[:3]]
            while len(raw) < 3:
                raw.append("none")
            return [raw[2], raw[1], raw[0]]
        return []

    @classmethod
    def _extract_star_slider_summary(cls, riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id != "star_slider" or not state_payload:
            return None
        positions = state_payload.get("reader_positions") or {}
        current = cls._extract_star_slider_values(positions)
        if not current:
            current = cls._extract_star_slider_values(state_payload.get("reader_labels"))
        attempts = []
        for item in state_payload.get("attempted_star_signs") or []:
            if not isinstance(item, dict):
                continue
            vals = cls._extract_star_slider_values(item.get("positions") or item)
            if vals:
                attempts.append(vals)
        return {"current": current, "attempts": attempts}

    @staticmethod
    def _extract_piano_summary(riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id != "piano" or not state_payload:
            return None
        played_notes = []
        for item in state_payload.get("played_notes") or []:
            if not isinstance(item, dict):
                continue
            encoded = str(item.get("encoded") or "").strip()
            if not encoded:
                continue
            played_notes.append({
                "encoded": encoded,
                "accepted": bool(item.get("accepted", False)),
            })
        return {"played_notes": played_notes}

    @staticmethod
    def _extract_info(riddle_id: str, state_payload: dict[str, Any]) -> str:
        if not state_payload or riddle_id in {"images", "piano", "chess", "knocking", "candles", "star_slider"}:
            return ""
        generic = []
        for key, value in state_payload.items():
            if key in {"id", "fw", "up", "ts", "time_valid", "buttons"}:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            generic.append(f"{key}: {value}")
            if len(generic) >= 4:
                break
        return "   ".join(generic)



store = DashboardStore()
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)




def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    raise TypeError(f"Unsupported row type for dict conversion: {type(row)!r}")


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[:1] not in '[{':
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _parse_dt(value: Any) -> Any:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        try:
            from datetime import datetime
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(normalized)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def format_datetime_readable(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return str(value or "—")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_mmss(value: Any) -> str:
    if value in {None, ""}:
        return "—"
    try:
        total_seconds = int(round(float(value)))
    except Exception:
        return str(value)
    minutes, seconds = divmod(max(total_seconds, 0), 60)
    return f"{minutes:02d}:{seconds:02d}"


def serialize_db_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def display_players_count(value: Any) -> str:
    try:
        return str(max(int(float(str(value).strip() or "0")), 0))
    except Exception:
        return "0"


def parse_players_count_input(value: Any) -> int:
    try:
        return max(int(float(str(value).strip() or "0")), 0)
    except Exception as exc:
        raise ValueError("players_count must be a non-negative integer") from exc


def parse_mmss_input(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "—":
        return None
    if re.fullmatch(r"\d{1,2}:\d{1,2}(?::\d{1,2})?", text):
        chunks = [int(part) for part in text.split(":")]
        if len(chunks) == 2:
            minutes, seconds = chunks
            return float(minutes * 60 + seconds)
        hours, minutes, seconds = chunks
        return float(hours * 3600 + minutes * 60 + seconds)
    return float(text)


def _seconds_or_none(value: Any) -> float | None:
    if value in {None, "", "—"}:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _riddle_anchor_seconds(riddle_name: str, solve_by_name: dict[str, float | None], sequential_previous_solve: float) -> float:
    name = str(riddle_name or "").strip()
    if name in {"tangram", "magnet"}:
        rope_solve = solve_by_name.get("rope_paths")
        if rope_solve is not None:
            return float(rope_solve)
    if name == "chess":
        later_parallel = [solve_by_name.get("tangram"), solve_by_name.get("magnet")]
        solved = [float(item) for item in later_parallel if item is not None]
        if solved:
            return max(solved)
    return float(sequential_previous_solve or 0.0)


def _calculate_riddle_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    solve_by_name: dict[str, float | None] = {}
    sequential_previous_solve = 0.0
    calculated: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        solve_seconds = _seconds_or_none(row.get("solve_time_from_run_start_s"))
        anchor_seconds = _riddle_anchor_seconds(str(row.get("riddle") or ""), solve_by_name, sequential_previous_solve)
        if solve_seconds is None:
            riddle_seconds = None
        else:
            riddle_seconds = max(0.0, solve_seconds - anchor_seconds)
            sequential_previous_solve = solve_seconds
        solve_by_name[str(row.get("riddle") or "").strip()] = solve_seconds
        row["_anchor_seconds"] = anchor_seconds
        row["_solve_seconds"] = solve_seconds
        row["_riddle_seconds"] = riddle_seconds
        calculated.append(row)
    return calculated


def _recalculate_game_riddle_solve_times(
    conn: sqlite3.Connection,
    game_id: str,
    *,
    row_name_overrides: dict[int, str] | None = None,
    solve_overrides: dict[int, float | None] | None = None,
    duration_overrides: dict[int, float | None] | None = None,
) -> list[dict[str, Any]]:
    row_name_overrides = dict(row_name_overrides or {})
    solve_overrides = dict(solve_overrides or {})
    duration_overrides = dict(duration_overrides or {})

    rows = [
        _row_to_dict(row) or {}
        for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()
    ]
    if not rows:
        return []

    for row in rows:
        rowid = int(row.get("_rowid_") or 0)
        if rowid in row_name_overrides:
            row["riddle"] = row_name_overrides[rowid]

    current_rows = _calculate_riddle_timing_rows(rows)
    current_durations = {
        int(row.get("_rowid_") or 0): row.get("_riddle_seconds")
        for row in current_rows
    }

    solve_by_name: dict[str, float | None] = {}
    sequential_previous_solve = 0.0
    recalculated: list[dict[str, Any]] = []
    for row in rows:
        rowid = int(row.get("_rowid_") or 0)
        row_name = str(row.get("riddle") or "").strip()
        anchor_seconds = _riddle_anchor_seconds(row_name, solve_by_name, sequential_previous_solve)

        has_solve_override = rowid in solve_overrides
        if has_solve_override:
            solve_seconds = solve_overrides[rowid]
            riddle_seconds = None if solve_seconds is None else max(0.0, float(solve_seconds) - anchor_seconds)
        else:
            riddle_seconds = duration_overrides.get(rowid, current_durations.get(rowid))
            solve_seconds = None if riddle_seconds is None else max(0.0, anchor_seconds + float(riddle_seconds))

        row["solve_time_from_run_start_s"] = solve_seconds
        row["_anchor_seconds"] = anchor_seconds
        row["_riddle_seconds"] = riddle_seconds
        row["_solve_seconds"] = solve_seconds
        recalculated.append(row)

        if solve_seconds is not None:
            sequential_previous_solve = float(solve_seconds)
        solve_by_name[row_name] = solve_seconds

    for row in recalculated:
        conn.execute(
            "UPDATE game_riddles SET riddle = ?, solve_time_from_run_start_s = ? WHERE rowid = ?",
            (row.get("riddle"), row.get("solve_time_from_run_start_s"), int(row.get("_rowid_") or 0)),
        )

    final_solve = max((float(row.get("_solve_seconds")) for row in recalculated if row.get("_solve_seconds") is not None), default=None)
    try:
        _refresh_game_duration_and_end(conn, game_id, final_solve)
    except Exception:
        pass

    return recalculated


def _refresh_game_hint_count(conn: sqlite3.Connection, game_id: str) -> int:
    total_hints = conn.execute("SELECT COUNT(*) FROM game_hints WHERE game_id = ?", (game_id,)).fetchone()[0]
    conn.execute("UPDATE games SET hint_count = ? WHERE id = ?", (int(total_hints or 0), game_id))
    return int(total_hints or 0)


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _refresh_game_duration_and_end(conn: sqlite3.Connection, game_id: str, explicit_duration_s: float | None = None) -> float | None:
    if explicit_duration_s is None:
        row = conn.execute(
            "SELECT MAX(solve_time_from_run_start_s) FROM game_riddles WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        duration_s = None if not row or row[0] is None else float(row[0])
    else:
        duration_s = float(explicit_duration_s)

    game_row = conn.execute("SELECT started_at, ended_at FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return duration_s

    started_at = _parse_iso_datetime(game_row[0])
    ended_at_value = game_row[1]
    if duration_s is None:
        conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (None, ended_at_value, game_id))
        return None

    next_ended_at = ended_at_value
    if started_at is not None:
        next_ended_at = (started_at + timedelta(seconds=max(0.0, duration_s))).isoformat(timespec='seconds')

    conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (duration_s, next_ended_at, game_id))
    return duration_s


def update_hint_rows_for_riddle(conn: sqlite3.Connection, game_id: str, riddle: str, target_count: int) -> None:
    target_count = max(int(target_count), 0)
    rows = conn.execute(
        "SELECT id, at, hint_text FROM game_hints WHERE game_id = ? AND riddle = ? ORDER BY id ASC",
        (game_id, riddle),
    ).fetchall()
    current_count = len(rows)
    if target_count == current_count:
        return
    if target_count < current_count:
        to_delete = [row[0] for row in rows[target_count:]]
        conn.executemany("DELETE FROM game_hints WHERE id = ?", [(item,) for item in to_delete])
        return

    base_at = None
    if rows:
        base_at = rows[-1][1]
    if not base_at:
        base_at = conn.execute(
            "SELECT solved_at FROM game_riddles WHERE game_id = ? AND riddle = ? ORDER BY rowid ASC LIMIT 1",
            (game_id, riddle),
        ).fetchone()
        base_at = base_at[0] if base_at and base_at[0] else None
    if not base_at:
        base_at = conn.execute(
            "SELECT started_at FROM games WHERE id = ?",
            (game_id,),
        ).fetchone()
        base_at = base_at[0] if base_at and base_at[0] else datetime.now(timezone.utc).isoformat()

    missing = target_count - current_count
    conn.executemany(
        "INSERT INTO game_hints (game_id, at, riddle, hint_text) VALUES (?, ?, ?, ?)",
        [(game_id, base_at, riddle, "") for _ in range(missing)],
    )


def first_existing_key(row: dict[str, Any] | None, candidates: list[str]) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in candidates:
        if key in row:
            return key
    return None


def build_editable_columns(rows: list[dict[str, Any]], *, exclude: set[str] | None = None, preferred: list[str] | None = None) -> list[str]:
    exclude = set(exclude or set()) | {"_rowid_"}
    ordered: list[str] = []
    preferred = preferred or []
    for name in preferred:
        if name not in exclude and any(name in (row or {}) for row in rows):
            ordered.append(name)
    for row in rows:
        for key in (row or {}).keys():
            if key in exclude or key in ordered:
                continue
            ordered.append(key)
    return ordered


def list_games_from_db() -> list[dict[str, Any]]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rowid AS _rowid_, * FROM games ORDER BY COALESCE(started_at, date, id) DESC, id DESC"
        ).fetchall()

    games: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row) or {}
        item["started_at_display"] = format_datetime_readable(item.get("started_at") or item.get("game_started_at") or item.get("date"))
        item["ended_at_display"] = format_datetime_readable(item.get("ended_at"))
        item["date_display"] = str(item.get("date") or item.get("started_at_display")[:10] or "—")
        item["duration_mmss"] = format_mmss(item.get("duration_s"))
        item["players_count_display"] = display_players_count(item.get("players_count"))
        item["hint_count_display"] = int(item.get("hint_count") or 0)
        item["leaderboard_code_display"] = serialize_db_value(item.get("leaderboard_code")) or ""
        games.append(item)
    return games


def build_game_view_state(game_id: str) -> dict[str, Any]:
    loaded = load_game_from_db(game_id)
    game = loaded["game"]
    riddles = loaded["riddles"]
    hints = loaded["hints"]
    if game is None:
        return {"game": None, "riddles": [], "hints": [], "hint_columns": [], "raw_rows": []}

    game["started_at_display"] = format_datetime_readable(game.get("started_at") or game.get("game_started_at") or game.get("date"))
    game["ended_at_display"] = format_datetime_readable(game.get("ended_at"))
    game["duration_mmss"] = format_mmss(game.get("duration_s"))
    game["players_count_display"] = display_players_count(game.get("players_count"))
    game["hint_count_display"] = int(game.get("hint_count") or 0)
    game["leaderboard_code_display"] = serialize_db_value(game.get("leaderboard_code")) or ""

    riddle_hint_counts: dict[str, int] = {}
    for hint in hints:
        name = str(hint.get("riddle") or "").strip()
        if name:
            riddle_hint_counts[name] = riddle_hint_counts.get(name, 0) + 1

    rendered_riddles: list[dict[str, Any]] = []
    for row in _calculate_riddle_timing_rows(riddles):
        rendered = dict(row)
        rendered["solve_time_mmss"] = format_mmss(rendered.get("_solve_seconds"))
        rendered["riddle_time_mmss"] = format_mmss(rendered.get("_riddle_seconds"))
        rendered["hint_count_display"] = int(riddle_hint_counts.get(str(rendered.get("riddle") or ""), 0))
        rendered_riddles.append(rendered)

    hint_columns = build_editable_columns(hints, preferred=["at", "riddle", "hint_text"])
    raw_rows = [
        {"table": "games", "rowid": game.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in game.items() if not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
    ]
    raw_rows.extend(
        {"table": "game_riddles", "rowid": row.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in row.items() if not str(k).startswith("_") and not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
        for row in rendered_riddles
    )
    raw_rows.extend(
        {"table": "game_hints", "rowid": row.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in row.items() if k != "_rowid_"}, ensure_ascii=False, indent=2, default=str)}
        for row in hints
    )
    return {
        "game": game,
        "riddles": rendered_riddles,
        "hints": hints,
        "hint_columns": hint_columns,
        "raw_rows": raw_rows,
    }


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or "TEXT"),
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5]),
        }
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]


def _ensure_missing_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> None:
    src_columns = _table_columns(src, table_name)
    dst_column_names = {item["name"] for item in _table_columns(dst, table_name)}
    for column in src_columns:
        name = column["name"]
        if name in dst_column_names:
            continue
        coltype = column["type"] or "TEXT"
        if name == "players_count":
            dst.execute(f"ALTER TABLE {table_name} ADD COLUMN players_count INTEGER NOT NULL DEFAULT 0")
        else:
            dst.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {coltype}")


def _intersecting_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> list[str]:
    src_names = [item["name"] for item in _table_columns(src, table_name)]
    dst_names = {item["name"] for item in _table_columns(dst, table_name)}
    return [name for name in src_names if name in dst_names]


def _fallback_value_for_missing_column(column: dict[str, Any]) -> Any:
    name = str(column.get("name") or "")
    if name in {"player_names_json", "player_names"}:
        return "[]" if name.endswith("_json") else ""
    if name in {"team_name", "display_mode", "leaderboard_code"}:
        return ""
    coltype = str(column.get("type") or "").upper()
    if "INT" in coltype or "REAL" in coltype or "NUM" in coltype:
        return 0
    return ""


def _build_insert_payload_for_dst(src_row: dict[str, Any], dst: sqlite3.Connection, table_name: str) -> tuple[list[str], list[Any]]:
    columns = _table_columns(dst, table_name)
    names: list[str] = []
    values: list[Any] = []
    for column in columns:
        name = column["name"]
        if name in src_row:
            value = src_row.get(name)
        elif column.get("default") is not None:
            continue
        elif column.get("notnull"):
            value = _fallback_value_for_missing_column(column)
        else:
            value = None
        names.append(name)
        values.append(value)
    return names, values


def _run_json_path(game_id: str) -> Path:
    return RUN_JSON_DIR / f"{game_id}.json"


def _restore_game_from_run_json_backup(conn: sqlite3.Connection, game_id: str) -> bool:
    backup_path = _run_json_path(game_id)
    if not backup_path.exists():
        return False
    try:
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    game_row = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return False

    game_columns = {item["name"] for item in _table_columns(conn, "games")}
    game_updates: dict[str, Any] = {}
    if "players_count" in game_columns:
        game_updates["players_count"] = int(payload.get("players_count") or 0)
    if "leaderboard_code" in game_columns:
        code = str(payload.get("leaderboard_code") or "").strip()
        game_updates["leaderboard_code"] = code or None
    if "hint_count" in game_columns:
        hints_payload = payload.get("hints") if isinstance(payload.get("hints"), list) else []
        game_updates["hint_count"] = len(hints_payload)
    if game_updates:
        set_clause = ", ".join(f"{name} = ?" for name in game_updates.keys())
        conn.execute(f"UPDATE games SET {set_clause} WHERE id = ?", [*game_updates.values(), game_id])

    riddle_timings = payload.get("riddle_timings")
    restored_any = False
    if isinstance(riddle_timings, dict):
        conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        for riddle_name, timing in riddle_timings.items():
            timing = timing if isinstance(timing, dict) else {}
            conn.execute(
                """
                INSERT INTO game_riddles (
                    game_id, riddle, source, activated_at, solved_at,
                    solve_time_from_run_start_s, solve_time_from_activation_s, solved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    str(timing.get("node") or riddle_name),
                    str(timing.get("source") or "manual"),
                    timing.get("activated_at"),
                    timing.get("solved_at"),
                    timing.get("solve_time_from_run_start_s"),
                    timing.get("solve_time_from_activation_s"),
                    1 if bool(timing.get("solved")) else 0,
                ),
            )
            restored_any = True

    hints_payload = payload.get("hints")
    if isinstance(hints_payload, list):
        conn.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
        for hint in hints_payload:
            hint = hint if isinstance(hint, dict) else {}
            conn.execute(
                "INSERT INTO game_hints (game_id, at, riddle, hint_text) VALUES (?, ?, ?, ?)",
                (
                    game_id,
                    hint.get("at") or payload.get("ended_at") or payload.get("started_at") or payload.get("date") or "",
                    str(hint.get("riddle") or ""),
                    str(hint.get("hint_text") or ""),
                ),
            )
            restored_any = True

    return restored_any


def _restore_game_from_backup_db(conn: sqlite3.Connection, game_id: str) -> bool:
    candidates = []
    for path in sorted(GAME_DB_PATH.parent.glob("*.sqlite3*")):
        if path.resolve() != GAME_DB_PATH.resolve():
            candidates.append(path)
    if REMOVED_GAME_DB_PATH.exists():
        candidates.append(REMOVED_GAME_DB_PATH)
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen or not path.exists() or path.is_dir():
            continue
        seen.add(key)
        try:
            with sqlite3.connect(path) as backup:
                backup.row_factory = sqlite3.Row
                if not _table_exists(backup, "games"):
                    continue
                row = backup.execute("SELECT id FROM games WHERE id = ?", (game_id,)).fetchone()
                if row is None:
                    continue
                if _table_exists(backup, "game_riddles"):
                    riddles = backup.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
                    if riddles:
                        conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
                        cols = _intersecting_columns(backup, conn, "game_riddles")
                        for row in riddles:
                            values = dict(row)
                            conn.execute(
                                f"INSERT INTO game_riddles ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                                tuple(values.get(col) for col in cols),
                            )
                if _table_exists(backup, "game_hints"):
                    hints = backup.execute("SELECT * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
                    if hints:
                        conn.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
                        cols = _intersecting_columns(backup, conn, "game_hints")
                        for row in hints:
                            values = dict(row)
                            conn.execute(
                                f"INSERT INTO game_hints ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                                tuple(values.get(col) for col in cols),
                            )
                game_cols = {item["name"] for item in _table_columns(conn, "games")}
                backup_game = dict(backup.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())
                updates = {}
                for name in ("players_count", "hint_count", "leaderboard_code"):
                    if name in game_cols and name in backup_game:
                        updates[name] = backup_game.get(name)
                if updates:
                    set_clause = ", ".join(f"{name} = ?" for name in updates.keys())
                    conn.execute(f"UPDATE games SET {set_clause} WHERE id = ?", [*updates.values(), game_id])
                has_riddles = conn.execute("SELECT 1 FROM game_riddles WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
                has_hints = conn.execute("SELECT 1 FROM game_hints WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
                if has_riddles or has_hints:
                    return True
        except Exception:
            continue
    return False


def _restore_missing_game_details_from_backups(conn: sqlite3.Connection, game_id: str) -> bool:
    restored = _restore_game_from_backup_db(conn, game_id)
    if restored:
        return True
    return _restore_game_from_run_json_backup(conn, game_id)


def _ensure_table_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> None:
    if _table_exists(dst, table_name):
        return
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"Could not read schema for table {table_name}")
    dst.execute(row[0])


def move_game_to_removed(game_id: str) -> None:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")

    REMOVED_GAMES_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(GAME_DB_PATH) as src, sqlite3.connect(REMOVED_GAME_DB_PATH) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row

        game_row = src.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            raise ValueError(f"No game found for id {game_id}.")

        riddle_rows = src.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
        hint_rows = src.execute("SELECT * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()

        for table_name in ("games", "game_riddles", "game_hints"):
            _ensure_table_schema(src, dst, table_name)
            _ensure_missing_columns(src, dst, table_name)

        dst.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        dst.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))

        game_values = dict(game_row)
        game_cols, game_params = _build_insert_payload_for_dst(game_values, dst, "games")
        dst.execute(
            f"INSERT INTO games ({', '.join(game_cols)}) VALUES ({', '.join(['?'] * len(game_cols))})",
            tuple(game_params),
        )

        for rows, table_name in ((riddle_rows, "game_riddles"), (hint_rows, "game_hints")):
            for row in rows:
                values = dict(row)
                cols, params = _build_insert_payload_for_dst(values, dst, table_name)
                dst.execute(
                    f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                    tuple(params),
                )

        src.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM games WHERE id = ?", (game_id,))

        dst.commit()
        src.commit()


def load_game_from_db(game_id: str) -> dict[str, Any]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
        if game is None:
            return {"game": None, "riddles": [], "hints": []}

        riddles = [_row_to_dict(row) for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()]
        hints = [_row_to_dict(row) for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()]

        if not riddles and _restore_missing_game_details_from_backups(conn, game_id):
            game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
            riddles = [_row_to_dict(row) for row in conn.execute(
                "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
                (game_id,),
            ).fetchall()]
            hints = [_row_to_dict(row) for row in conn.execute(
                "SELECT rowid AS _rowid_, * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC",
                (game_id,),
            ).fetchall()]
            conn.commit()

        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())

    if "players_count" in game:
        game["players_count"] = parse_players_count_input(game.get("players_count"))
    for row in riddles:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])
    for row in hints:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])

    return {"game": game, "riddles": riddles, "hints": hints}


EDITABLE_TABLES: dict[str, dict[str, Any]] = {
    "games": {"blocked_columns": {"id"}},
    "game_riddles": {"blocked_columns": {"id", "game_id"}},
    "game_hints": {"blocked_columns": {"id", "game_id"}},
}


def update_db_row(table_name: str, rowid: int, updates: dict[str, Any]) -> None:
    config = EDITABLE_TABLES.get(table_name)
    if config is None:
        raise ValueError(f"Table {table_name} is not editable.")
    if not updates:
        return
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        columns_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if not columns_info:
            raise ValueError(f"Could not read schema for table {table_name}.")

        editable_columns = {
            str(col[1])
            for col in columns_info
            if str(col[1]) not in set(config.get("blocked_columns") or set())
        }

        if table_name == "games":
            current = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Row {rowid} not found in table {table_name}.")
            normalized_updates: dict[str, Any] = {}
            for key, value in updates.items():
                column = str(key or "").strip()
                if column == "players_count_display":
                    normalized_updates["players_count"] = parse_players_count_input(value)
                elif column == "duration_mmss":
                    normalized_updates["duration_s"] = parse_mmss_input(value)
                elif column == "hint_count_display":
                    normalized_updates["hint_count"] = max(int(float(str(value).strip() or "0")), 0)
                elif column in editable_columns:
                    normalized_updates[column] = value
                else:
                    raise ValueError(f"Column {column or key!r} is not editable in table {table_name}.")

            if normalized_updates:
                set_clause = ", ".join(f"{column} = ?" for column in normalized_updates.keys())
                values = [normalized_updates[column] for column in normalized_updates.keys()]
                values.append(rowid)
                conn.execute(f"UPDATE games SET {set_clause} WHERE rowid = ?", values)
            conn.commit()
            return

        if table_name == "game_riddles":
            current = conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Row {rowid} not found in table {table_name}.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get("game_id") or "")
            current_riddle = str(current_row.get("riddle") or "")

            pending_riddle_name = current_riddle
            pending_hint_count = None
            leaderboard_code_value = None
            row_name_overrides: dict[int, str] = {}
            solve_overrides: dict[int, float | None] = {}
            duration_overrides: dict[int, float | None] = {}
            direct_updates: dict[str, Any] = {}

            for key, value in updates.items():
                column = str(key or "").strip()
                if column == "solve_time_mmss":
                    solve_overrides[int(rowid)] = parse_mmss_input(value)
                elif column == "riddle_time_mmss":
                    if "solve_time_mmss" not in updates:
                        duration_overrides[int(rowid)] = parse_mmss_input(value)
                elif column == "hint_count_display":
                    pending_hint_count = max(int(float(str(value).strip() or "0")), 0)
                elif column in {"leaderboard_code", "leaderboard_code_display"}:
                    leaderboard_code_value = str(value or "").strip() or None
                elif column == "riddle":
                    pending_riddle_name = str(value or "").strip()
                    row_name_overrides[int(rowid)] = pending_riddle_name
                elif column in editable_columns:
                    direct_updates[column] = value
                else:
                    raise ValueError(f"Column {column or key!r} is not editable in table {table_name}.")

            if pending_riddle_name != current_riddle:
                conn.execute(
                    "UPDATE game_hints SET riddle = ? WHERE game_id = ? AND riddle = ?",
                    (pending_riddle_name, game_id, current_riddle),
                )

            if direct_updates:
                set_clause = ", ".join(f"{column} = ?" for column in direct_updates.keys())
                values = [direct_updates[column] for column in direct_updates.keys()]
                values.append(rowid)
                conn.execute(f"UPDATE game_riddles SET {set_clause} WHERE rowid = ?", values)

            if row_name_overrides or solve_overrides or duration_overrides:
                _recalculate_game_riddle_solve_times(
                    conn,
                    game_id,
                    row_name_overrides=row_name_overrides,
                    solve_overrides=solve_overrides,
                    duration_overrides=duration_overrides,
                )

            if leaderboard_code_value is not None or any(str(k) in {"leaderboard_code", "leaderboard_code_display"} for k in updates):
                conn.execute("UPDATE games SET leaderboard_code = ? WHERE id = ?", (leaderboard_code_value, game_id))

            if pending_hint_count is not None:
                update_hint_rows_for_riddle(conn, game_id, pending_riddle_name, pending_hint_count)
                _refresh_game_hint_count(conn, game_id)

            conn.commit()
            return

        normalized_updates: dict[str, Any] = {}
        for key, value in updates.items():
            column = str(key or "").strip()
            if not column or column not in editable_columns:
                raise ValueError(f"Column {column or key!r} is not editable in table {table_name}.")
            normalized_updates[column] = value

        if not normalized_updates:
            return

        current = conn.execute(f"SELECT rowid FROM {table_name} WHERE rowid = ?", (rowid,)).fetchone()
        if current is None:
            raise ValueError(f"Row {rowid} not found in table {table_name}.")

        set_clause = ", ".join(f"{column} = ?" for column in normalized_updates.keys())
        values = [normalized_updates[column] for column in normalized_updates.keys()]
        values.append(rowid)
        conn.execute(f"UPDATE {table_name} SET {set_clause} WHERE rowid = ?", values)
        if table_name == "game_hints":
            game_row = conn.execute("SELECT game_id FROM game_hints WHERE rowid = ?", (rowid,)).fetchone()
            if game_row and game_row[0]:
                _refresh_game_hint_count(conn, str(game_row[0]))
        conn.commit()

def parse_json_payload(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("d"), dict):
            inner = dict(data["d"])
            for key in ("t", "ts", "time_valid", "type", "v", "id"):
                if key in data and key not in inner:
                    inner[key] = data[key]
            return inner
        return data
    except Exception:
        return None


def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
    for topic, qos in [
        (TOPIC_GAME_STATE, 0),
        ("+/state", 0),
        ("+/hb", 0),
        ("maglock/lock/+/state", 0),
        ("lighting/mosfet/+/state", 0),
    ]:
        client.subscribe(topic, qos=qos)


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    topic = msg.topic
    data = parse_json_payload(msg.payload)
    if data is None:
        return
    if topic == TOPIC_GAME_STATE:
        store.update_game_state(data)
        return
    if topic.endswith("/hb"):
        node_id = topic.split("/", 1)[0]
        store.update_node_hb(node_id, data)
        return
    if topic.startswith("maglock/lock/") and topic.endswith("/state"):
        parts = topic.split("/")
        if len(parts) >= 4:
            store.update_lock_state(parts[2], data)
        return
    if topic.startswith("lighting/mosfet/") and topic.endswith("/state"):
        parts = topic.split("/")
        if len(parts) >= 4:
            light_name = data.get("light") or LIGHT_NAME_BY_ID.get(parts[2]) or parts[2]
            store.update_light_state(str(light_name), data)
        return
    if topic.endswith("/state"):
        node_id = topic.split("/", 1)[0]
        store.update_node_state(node_id, data)
        return


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
mqtt_client.loop_start()


def mqtt_publish(topic: str, payload: dict[str, Any] | str) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    mqtt_client.publish(topic, body, qos=0, retain=False)


@app.get("/")
def index() -> str:
    return render_template("index.html")




@app.get("/games")
def game_viewer() -> str:
    game_id = str(request.args.get("game_id", "")).strip()
    game = None
    riddles: list[dict[str, Any]] = []
    hints: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    error = str(request.args.get("error", "")).strip()
    message = str(request.args.get("message", "")).strip()
    summary_columns: list[str] = []
    riddle_columns: list[str] = []
    hint_columns: list[str] = []
    raw_rows: list[dict[str, Any]] = []

    try:
        games = list_games_from_db()
    except Exception as exc:
        error = error or str(exc)

    if game_id and not error:
        try:
            state = build_game_view_state(game_id)
            game = state["game"]
            riddles = state["riddles"]
            hints = state["hints"]
            hint_columns = state["hint_columns"]
            raw_rows = state["raw_rows"]
            if game is None:
                error = f"No game found for id {game_id}."
            else:
                summary_columns = [col for col in ["id", "date", "players_count", "hint_count", "leaderboard_code"] if col in game]
                riddle_columns = ["riddle", "riddle_time_mmss", "hint_count_display"]
        except Exception as exc:
            error = str(exc)

    return render_template(
        "game_viewer.html",
        game_id=game_id,
        game=game,
        riddles=riddles,
        hints=hints,
        games=games,
        error=error,
        message=message,
        db_path=str(GAME_DB_PATH),
        removed_db_path=str(REMOVED_GAME_DB_PATH),
        run_json_dir=str(RUN_JSON_DIR),
        summary_columns=summary_columns,
        riddle_columns=riddle_columns,
        hint_columns=hint_columns,
        serialize_db_value=serialize_db_value,
        editable_tables=EDITABLE_TABLES,
        raw_rows=raw_rows,
    )


@app.post("/games/delete/<game_id>")
def delete_game(game_id: str) -> Any:
    game_id = str(game_id or "").strip()
    if not game_id:
        return redirect(url_for("game_viewer", error="Missing game id."))
    try:
        move_game_to_removed(game_id)
        return redirect(url_for("game_viewer", message=f"Moved game {game_id} to {REMOVED_GAMES_DIR}"))
    except Exception as exc:
        return redirect(url_for("game_viewer", error=str(exc), game_id=game_id))

@app.post("/api/db/update")
def api_db_update() -> Any:
    data = request.get_json(force=True) or {}
    table_name = str(data.get("table", "")).strip()
    try:
        rowid = int(data.get("rowid"))
    except Exception:
        return jsonify({"ok": False, "error": "rowid must be an integer"}), 400

    updates = data.get("updates") or {}
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "updates must be an object"}), 400

    try:
        update_db_row(table_name, rowid, updates)
        return jsonify({"ok": True, "table": table_name, "rowid": rowid, "updated_columns": sorted(updates.keys())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/state")
def api_state() -> Any:
    return jsonify(store.snapshot())


@app.post("/api/phase")
def api_phase() -> Any:
    data = request.get_json(force=True)
    action = str(data.get("action", "")).strip().lower()
    if action == "start":
        mqtt_publish(TOPIC_GAME_CMD, {"cmd": "start"})
        return jsonify({"ok": True})
    if action in {"standby", "maintenance", "prepare"}:
        store.set_local_phase(action)
        mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_mode", "mode": action})
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid phase action"}), 400


@app.post("/api/players-count")
def api_players_count() -> Any:
    data = request.get_json(force=True) or {}
    try:
        players_count = parse_players_count_input(data.get("players_count", 0))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    store.set_local_players_count(players_count)
    mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_players_count", "players_count": players_count})
    return jsonify({"ok": True, "players_count": players_count})


@app.post("/api/solve")
def api_solve() -> Any:
    data = request.get_json(force=True)
    node = str(data.get("node", "")).strip()
    if not node:
        return jsonify({"ok": False, "error": "node required"}), 400
    mqtt_publish(TOPIC_GAME_CMD, {"cmd": "solve", "node": node, "riddle": node})
    return jsonify({"ok": True, "node": node})


@app.post("/api/lock")
def api_lock() -> Any:
    data = request.get_json(force=True)
    lock_id = str(data.get("lock", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    if lock_id not in {item["id"] for item in LOCKS}:
        return jsonify({"ok": False, "error": "invalid lock"}), 400
    if action not in {"open", "close"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    mqtt_publish(TOPIC_MAGLOCK_CMD, {"cmd": action, "lock": lock_id})
    return jsonify({"ok": True})


@app.post("/api/light")
def api_light() -> Any:
    data = request.get_json(force=True)
    group_id = str(data.get("group", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    pct_raw = data.get("pct")
    cfg = LIGHT_GROUPS.get(group_id)
    if cfg is None:
        return jsonify({"ok": False, "error": "invalid group"}), 400

    if group_id == "star_sky":
        if action not in {"on", "off"}:
            return jsonify({"ok": False, "error": "star sky supports only on/off"}), 400
        mqtt_publish(TOPIC_STAR_SKY_CMD, {"cmd": "on" if action == "on" else "off"})
        mqtt_publish("star_sky/sys/cmd", "SOLVE" if action == "on" else "DISABLE")
        mqtt_publish(TOPIC_LIGHTING_CMD, {"cmd": "turn_on" if action == "on" else "turn_off", "light": "r3_uv"})
        return jsonify({"ok": True})

    if cfg.get("dimmable"):
        try:
            pct = max(0, min(100, int(pct_raw)))
        except Exception:
            pct = 100
        if action == "off":
            pct = 0
        elif action not in {"on", "off", "set_pct"}:
            return jsonify({"ok": False, "error": "invalid action"}), 400
        for light_name in cfg["lights"]:
            mqtt_publish(TOPIC_LIGHTING_CMD, {"cmd": "set", "light": light_name, "pct": pct})
        return jsonify({"ok": True, "pct": pct})

    if action not in {"on", "off"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    for light_name in cfg["lights"]:
        mqtt_publish(TOPIC_LIGHTING_CMD, {"cmd": "turn_on" if action == "on" else "turn_off", "light": light_name})
    return jsonify({"ok": True})


@app.post("/api/hints")
def api_add_hint() -> Any:
    data = request.get_json(force=True)
    riddle = str(data.get("riddle", "")).strip()
    hint_text = str(data.get("text", "")).strip()
    if not riddle or not hint_text:
        return jsonify({"ok": False, "error": "riddle and text required"}), 400
    mqtt_publish(TOPIC_GAME_CMD, {"cmd": "add_hint", "riddle": riddle, "hint_text": hint_text})
    items = store.add_hint(riddle, hint_text)
    return jsonify({"ok": True, "hints": items})


@app.delete("/api/hints/<riddle>/<hint_id>")
def api_delete_hint(riddle: str, hint_id: str) -> Any:
    items = store.remove_hint(riddle, hint_id)
    return jsonify({"ok": True, "hints": items})


# ---- ER1 v2 dashboard overrides (new game_master DB schema) ----
RIDDLE_ALIASES = {
    "open_prison": "prison",
    "mount_wheel": "wheel",
    "rope_paths": "chains",
    "star_slider": "stars",
    "prison": "prison",
    "wheel": "wheel",
    "chains": "chains",
    "stars": "stars",
}
RIDDLE_ORDER_V2 = [
    "images", "piano", "prison", "wheel", "chains",
    "tangram", "magnet", "chess", "knocking", "candles", "stars", "sissi",
]
RIDDLE_LABELS_V2 = {
    "images": "Images",
    "piano": "Piano",
    "prison": "Prison",
    "wheel": "Wheel",
    "chains": "Chains",
    "tangram": "Tangram",
    "magnet": "Magnet",
    "chess": "Chess",
    "knocking": "Knocking",
    "candles": "Candles",
    "stars": "Stars",
    "sissi": "Sissi",
}

PHASE_META = {
    0: {"name": "standby", "active": (), "solved": ()},
    1: {"name": "maintenance", "active": tuple(RIDDLE_ORDER_V2), "solved": ()},
    2: {"name": "prepare", "active": (), "solved": ()},
    3: {"name": "start", "active": ("images",), "solved": ()},
    4: {"name": "piano", "active": ("piano",), "solved": ("images",)},
    5: {"name": "prison", "active": ("prison",), "solved": ("images", "piano")},
    6: {"name": "wheel", "active": ("wheel",), "solved": ("images", "piano", "prison")},
    7: {"name": "chains", "active": ("chains",), "solved": ("images", "piano", "prison", "wheel")},
    8: {"name": "tangram_magnet", "active": ("tangram", "magnet"), "solved": ("images", "piano", "prison", "wheel", "chains")},
    9: {"name": "chess", "active": ("chess",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet")},
    10: {"name": "knocking", "active": ("knocking",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess")},
    11: {"name": "candles", "active": ("candles",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking")},
    12: {"name": "stars", "active": ("stars",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking", "candles")},
    13: {"name": "sissi", "active": ("sissi",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking", "candles", "stars")},
    14: {"name": "finished", "active": (), "solved": tuple(RIDDLE_ORDER_V2)},
}

RIDDLES = [
    {"id": "images", "label": "Images", "node_id": "images_piano", "manual": False},
    {"id": "piano", "label": "Piano", "node_id": "images_piano", "manual": False},
    {"id": "prison", "label": "Prison", "node_id": None, "manual": True},
    {"id": "wheel", "label": "Wheel", "node_id": None, "manual": True},
    {"id": "chains", "label": "Chains", "node_id": None, "manual": True},
    {"id": "tangram", "label": "Tangram", "node_id": None, "manual": True},
    {"id": "magnet", "label": "Magnet", "node_id": None, "manual": True},
    {"id": "chess", "label": "Chess", "node_id": "chess", "manual": False},
    {"id": "knocking", "label": "Knocking", "node_id": "knocking", "manual": False},
    {"id": "candles", "label": "Candles", "node_id": "candles", "manual": False},
    {"id": "stars", "label": "Stars", "node_id": "stars", "manual": False},
    {"id": "sissi", "label": "Sissi", "node_id": None, "manual": True},
]
NODE_LABELS = [
    ("lighting", "Lighting Controller"),
    ("maglock", "Maglock Controller"),
    ("images_piano", "Images / Piano"),
    ("chess", "Chess"),
    ("knocking", "Knocking"),
    ("candles", "Candles"),
    ("star_slider", "Star Slider"),
    ("star_sky", "Star Sky"),
]

EDITABLE_TABLES = {
    "games": {"blocked_columns": {"id", "ended_at", "duration_s", "hint_count"}},
    "game_riddles": {"blocked_columns": {"id", "game_id", "riddle_key"}},
}


def _canonical_riddle_name(name: Any) -> str:
    return RIDDLE_ALIASES.get(str(name or "").strip(), str(name or "").strip())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _duration_and_progress_from_rows(rows: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    by_key = {_canonical_riddle_name(r.get('riddle_key')): max(0.0, _safe_float(r.get('solve_time_s'))) for r in rows}
    progress = {}
    elapsed = 0.0
    for key in ["images", "piano", "prison", "wheel", "chains"]:
        elapsed += by_key.get(key, 0.0)
        progress[key] = elapsed
    chain_end = progress.get("chains", elapsed)
    tangram_end = chain_end + by_key.get("tangram", 0.0)
    magnet_end = chain_end + by_key.get("magnet", 0.0)
    progress["tangram"] = tangram_end
    progress["magnet"] = magnet_end
    elapsed = max(tangram_end, magnet_end)
    for key in ["chess", "knocking", "candles", "stars", "sissi"]:
        elapsed += by_key.get(key, 0.0)
        progress[key] = elapsed
    return round(max(0.0, elapsed), 3), progress


def _refresh_game_hint_count(conn: sqlite3.Connection, game_id: str) -> int:
    total_hints = conn.execute("SELECT COALESCE(SUM(hint_count), 0) FROM game_riddles WHERE game_id = ?", (game_id,)).fetchone()[0]
    conn.execute("UPDATE games SET hint_count = ? WHERE id = ?", (int(total_hints or 0), game_id))
    return int(total_hints or 0)


def _refresh_game_duration_and_end(conn: sqlite3.Connection, game_id: str, explicit_duration_s: float | None = None) -> float | None:
    rows = [_row_to_dict(r) or {} for r in conn.execute("SELECT riddle_key, solve_time_s FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    duration_s = float(explicit_duration_s) if explicit_duration_s is not None else _duration_and_progress_from_rows(rows)[0]
    game_row = conn.execute("SELECT started_at, ended_at FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return duration_s
    started_at = _parse_iso_datetime(game_row[0])
    ended_at_value = game_row[1]
    next_ended_at = ended_at_value
    if started_at is not None:
        next_ended_at = (started_at + timedelta(seconds=max(0.0, duration_s))).isoformat(timespec='seconds')
        next_date = started_at.date().isoformat()
        conn.execute("UPDATE games SET date = ?, duration_s = ?, ended_at = ? WHERE id = ?", (next_date, duration_s, next_ended_at, game_id))
    else:
        conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (duration_s, next_ended_at, game_id))
    return duration_s


def _calculate_riddle_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = []
    for source_row in rows:
        row = dict(source_row)
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key') or row.get('riddle'))
        canonical.append(row)
    _, progress = _duration_and_progress_from_rows(canonical)
    calculated = []
    for row in canonical:
        key = row.get('riddle_key')
        direct = max(0.0, _safe_float(row.get('solve_time_s')))
        if key in {'tangram', 'magnet'}:
            anchor = progress.get('chains', 0.0)
        elif key == 'chess':
            anchor = max(progress.get('tangram', progress.get('chains', 0.0)), progress.get('magnet', progress.get('chains', 0.0)))
        elif key == 'images':
            anchor = 0.0
        else:
            idx = RIDDLE_ORDER_V2.index(key) if key in RIDDLE_ORDER_V2 else -1
            prev_key = RIDDLE_ORDER_V2[idx - 1] if idx > 0 else None
            anchor = progress.get(prev_key, 0.0) if prev_key else 0.0
        row['_anchor_seconds'] = anchor
        row['_riddle_seconds'] = direct
        row['_solve_seconds'] = progress.get(key, direct)
        calculated.append(row)
    return calculated


def _recalculate_game_riddle_solve_times(conn: sqlite3.Connection, game_id: str, *, row_name_overrides=None, solve_overrides=None, duration_overrides=None):
    row_name_overrides = dict(row_name_overrides or {})
    solve_overrides = dict(solve_overrides or {})
    duration_overrides = dict(duration_overrides or {})
    rows = [_row_to_dict(r) or {} for r in conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    if not rows:
        return []
    for row in rows:
        rid = int(row.get('_rowid_') or 0)
        if rid in row_name_overrides:
            row['riddle_key'] = _canonical_riddle_name(row_name_overrides[rid])
        if rid in solve_overrides:
            row['solve_time_s'] = max(0.0, _safe_float(solve_overrides[rid])) if solve_overrides[rid] is not None else 0.0
        elif rid in duration_overrides:
            row['solve_time_s'] = max(0.0, _safe_float(duration_overrides[rid])) if duration_overrides[rid] is not None else 0.0
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key'))
    for row in rows:
        conn.execute("UPDATE game_riddles SET riddle_key = ?, solve_time_s = ? WHERE rowid = ?", (row.get('riddle_key'), round(max(0.0, _safe_float(row.get('solve_time_s'))), 3), int(row.get('_rowid_') or 0)))
    _refresh_game_duration_and_end(conn, game_id)
    _refresh_game_hint_count(conn, game_id)
    return _calculate_riddle_timing_rows(rows)


def build_game_view_state(game_id: str) -> dict[str, Any]:
    loaded = load_game_from_db(game_id)
    game = loaded['game']
    riddles = loaded['riddles']
    if game is None:
        return {"game": None, "riddles": [], "hints": [], "hint_columns": [], "raw_rows": []}
    game['started_at_display'] = format_datetime_readable(game.get('started_at') or game.get('date'))
    game['ended_at_display'] = format_datetime_readable(game.get('ended_at'))
    game['duration_mmss'] = format_mmss(game.get('duration_s'))
    game['players_count_display'] = display_players_count(game.get('players_count'))
    game['hint_count_display'] = int(game.get('hint_count') or 0)
    game['leaderboard_code_display'] = serialize_db_value(game.get('leaderboard_code')) or ''
    rendered_riddles = []
    for row in _calculate_riddle_timing_rows(riddles):
        rendered = dict(row)
        rendered['riddle_label'] = RIDDLE_LABELS_V2.get(rendered.get('riddle_key'), rendered.get('riddle_key'))
        rendered['riddle_time_mmss'] = format_mmss(rendered.get('_riddle_seconds'))
        rendered['hint_count_display'] = int(rendered.get('hint_count') or 0)
        rendered['hints_display'] = serialize_db_value(rendered.get('hints')) or ''
        rendered_riddles.append(rendered)
    rendered_riddles.sort(key=lambda row: RIDDLE_ORDER_V2.index(row.get('riddle_key')) if row.get('riddle_key') in RIDDLE_ORDER_V2 else 999)
    raw_rows = [
        {"table": "games", "rowid": game.get('_rowid_'), "raw_json": json.dumps({k: v for k, v in game.items() if not str(k).startswith('_') and not str(k).endswith('_display') and not str(k).endswith('_mmss')}, ensure_ascii=False, indent=2, default=str)}
    ] + [
        {"table": "game_riddles", "rowid": row.get('_rowid_'), "raw_json": json.dumps({k: v for k, v in row.items() if not str(k).startswith('_') and not str(k).endswith('_display') and not str(k).endswith('_mmss')}, ensure_ascii=False, indent=2, default=str)}
        for row in rendered_riddles
    ]
    return {"game": game, "riddles": rendered_riddles, "hints": [], "hint_columns": [], "raw_rows": raw_rows}


def move_game_to_removed(game_id: str) -> None:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")
    REMOVED_GAMES_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(GAME_DB_PATH) as src, sqlite3.connect(REMOVED_GAME_DB_PATH) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        game_row = src.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            raise ValueError(f"No game found for id {game_id}.")
        riddle_rows = src.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
        for table_name in ("games", "game_riddles"):
            _ensure_table_schema(src, dst, table_name)
            _ensure_missing_columns(src, dst, table_name)
        dst.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        game_values = dict(game_row)
        game_cols, game_params = _build_insert_payload_for_dst(game_values, dst, "games")
        dst.execute(f"INSERT INTO games ({', '.join(game_cols)}) VALUES ({', '.join(['?'] * len(game_cols))})", tuple(game_params))
        for row in riddle_rows:
            values = dict(row)
            cols, params = _build_insert_payload_for_dst(values, dst, "game_riddles")
            dst.execute(f"INSERT INTO game_riddles ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})", tuple(params))
        src.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.commit()
        src.commit()


def load_game_from_db(game_id: str) -> dict[str, Any]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")
    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
        if game is None:
            return {"game": None, "riddles": [], "hints": []}
        riddles = [_row_to_dict(row) for row in conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    if 'players_count' in game:
        game['players_count'] = parse_players_count_input(game.get('players_count'))
    for row in riddles:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key') or row.get('riddle'))
    return {"game": game, "riddles": riddles, "hints": []}


def update_db_row(table_name: str, rowid: int, updates: dict[str, Any]) -> None:
    config = EDITABLE_TABLES.get(table_name)
    if config is None:
        raise ValueError(f"Table {table_name} is not editable.")
    if not updates:
        return
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {GAME_DB_PATH}")
    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        columns_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        editable_columns = {str(col[1]) for col in columns_info if str(col[1]) not in set(config.get('blocked_columns') or set())}
        if table_name == 'games':
            current = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Row {rowid} not found in table {table_name}.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get('id') or '').strip()
            normalized_updates = {}
            for key, value in updates.items():
                column = str(key or '').strip()
                if column == 'players_count_display':
                    normalized_updates['players_count'] = parse_players_count_input(value)
                elif column in {'leaderboard_code', 'leaderboard_code_display'}:
                    normalized_updates['leaderboard_code'] = str(value or '').strip() or None
                elif column in {'date_display', 'started_at_display'}:
                    raise ValueError(f"Column {column!r} is not editable in table {table_name}.")
                elif column in editable_columns:
                    normalized_updates[column] = value
                else:
                    raise ValueError(f"Column {column!r} is not editable in table {table_name}.")
            if normalized_updates:
                set_clause = ', '.join(f"{column} = ?" for column in normalized_updates.keys())
                values = list(normalized_updates.values()) + [rowid]
                conn.execute(f"UPDATE games SET {set_clause} WHERE rowid = ?", values)
                if 'started_at' in normalized_updates:
                    _refresh_game_duration_and_end(conn, game_id)
            conn.commit()
            return
        if table_name == 'game_riddles':
            current = conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Row {rowid} not found in table {table_name}.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get('game_id') or '').strip()
            direct_updates = {}
            solve_overrides = {}
            for key, value in updates.items():
                column = str(key or '').strip()
                if column in {'riddle_time_mmss', 'solve_time_s'}:
                    solve_overrides[int(rowid)] = parse_mmss_input(value) if column == 'riddle_time_mmss' else max(0.0, _safe_float(value))
                elif column in {'hint_count_display', 'hint_count'}:
                    direct_updates['hint_count'] = max(0, _safe_int(value))
                elif column == 'hints':
                    direct_updates['hints'] = str(value or '')
                elif column in editable_columns:
                    direct_updates[column] = value
                else:
                    raise ValueError(f"Column {column!r} is not editable in table {table_name}.")
            if direct_updates:
                set_clause = ', '.join(f"{column} = ?" for column in direct_updates.keys())
                values = list(direct_updates.values()) + [rowid]
                conn.execute(f"UPDATE game_riddles SET {set_clause} WHERE rowid = ?", values)
            if solve_overrides:
                _recalculate_game_riddle_solve_times(conn, game_id, solve_overrides=solve_overrides)
            else:
                _refresh_game_duration_and_end(conn, game_id)
                _refresh_game_hint_count(conn, game_id)
            conn.commit()
            return
        raise ValueError(f"Table {table_name} is not editable.")


def _reset_riddle_display_state_locked_v2(self):
    for node_id in ["images_piano", "chess", "knocking", "candles", "stars"]:
        self._clear_node_payload_locked(node_id)
    self.riddle_states["images"] = {"id": "images", "buttons": {}}
    self.riddle_states["piano"] = {"id": "piano", "played_notes": []}
    self.riddle_states["chess"] = {"id": "chess", "reader_labels": {}}
    self.riddle_states["knocking"] = {"id": "knocking", "tries": 0, "attempted_sequences": []}
    self.riddle_states["candles"] = {"id": "candles", "tries": 0, "attempted_sequences": []}
    self.riddle_states["stars"] = {"id": "stars", "tries": 0, "attempted_star_signs": [], "reader_positions": {}}


def _update_node_state_v2(self, node_id: str, payload: dict[str, Any]) -> None:
    with self.lock:
        previous_node = self.node_states.get(node_id, {}) if isinstance(self.node_states.get(node_id), dict) else {}
        merged_node = dict(previous_node)
        merged_node.update(payload)
        self.node_states[node_id] = merged_node
        if node_id == 'images_piano':
            if self._is_images_payload(payload):
                prev = self.riddle_states.get('images', {}) if isinstance(self.riddle_states.get('images'), dict) else {}
                merged = dict(prev); merged.update(payload); merged['id'] = 'images'; self.riddle_states['images'] = merged; return
            if self._is_piano_payload(payload):
                prev = self.riddle_states.get('piano', {}) if isinstance(self.riddle_states.get('piano'), dict) else {}
                merged = dict(prev); merged.update(payload); merged['id'] = 'piano'
                played_notes = list(prev.get('played_notes') or [])
                encoded = str(payload.get('encoded') or '').strip()
                if encoded:
                    played_notes.append({'encoded': encoded, 'accepted': bool(payload.get('accepted', False))})
                merged['played_notes'] = played_notes[-40:]
                self.riddle_states['piano'] = merged; return
        riddle_id = _canonical_riddle_name(payload.get('id') or node_id)
        if riddle_id:
            prev = self.riddle_states.get(riddle_id, {}) if isinstance(self.riddle_states.get(riddle_id), dict) else {}
            merged = dict(prev); merged.update(payload); merged['id'] = riddle_id
            if riddle_id in {'knocking', 'candles'}:
                attempts = list(merged.get('attempted_sequences') or [])
                last_attempt = str(payload.get('last_attempt') or '').strip()
                if last_attempt and (not attempts or attempts[-1] != last_attempt):
                    attempts.append(last_attempt)
                merged['attempted_sequences'] = attempts
            elif riddle_id == 'stars':
                attempts = list(merged.get('attempted_star_signs') or [])
                last_positions = payload.get('last_attempt_positions')
                if isinstance(last_positions, dict) and (not attempts or attempts[-1] != last_positions):
                    attempts.append(last_positions)
                merged['attempted_star_signs'] = attempts
            self.riddle_states[riddle_id] = merged


DashboardStore._reset_riddle_display_state_locked = _reset_riddle_display_state_locked_v2
DashboardStore.update_node_state = _update_node_state_v2

@classmethod
def _extract_star_slider_summary_any(cls, riddle_id: str, state_payload: dict[str, Any]):
    if riddle_id not in {'stars', 'star_slider'} or not state_payload:
        return None
    positions = state_payload.get('reader_positions') or {}
    current = cls._extract_star_slider_values(positions)
    if not current:
        current = cls._extract_star_slider_values(state_payload.get('reader_labels'))
    attempts = []
    for item in state_payload.get('attempted_star_signs') or []:
        if not isinstance(item, dict):
            continue
        vals = cls._extract_star_slider_values(item.get('positions') or item)
        if vals:
            attempts.append(vals)
    return {'current': current, 'attempts': attempts}
DashboardStore._extract_star_slider_summary = _extract_star_slider_summary_any

@staticmethod
def _extract_info_any(riddle_id: str, state_payload: dict[str, Any]) -> str:
    if not state_payload or riddle_id in {'images', 'piano', 'chess', 'knocking', 'candles', 'stars', 'star_slider'}:
        return ''
    generic = []
    for key, value in state_payload.items():
        if key in {'id', 'fw', 'up', 'ts', 'time_valid', 'buttons'}:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        generic.append(f"{key}: {value}")
        if len(generic) >= 4:
            break
    return '   '.join(generic)
DashboardStore._extract_info = _extract_info_any


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("ER1_DASHBOARD_PORT", "8080")), debug=False)
