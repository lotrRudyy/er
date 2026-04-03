
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
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

    def update_game_state(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.game_state = payload
            try:
                phase = int(payload.get("phase", 0))
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
            "players": [],
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
    return {key: row[key] for key in row.keys()}


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


def display_player_names(value: Any) -> str:
    parsed = _maybe_json(value)
    if isinstance(parsed, list):
        items = [str(item).strip() for item in parsed if str(item).strip()]
        return ", ".join(items) if items else "—"
    text = str(parsed or "").strip()
    return text or "—"


def parse_player_names_input(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "—":
        return json.dumps([], ensure_ascii=False)
    parts = [item.strip() for item in re.split(r"[,;\n]+", text) if item.strip()]
    if not parts:
        parts = [text]
    return json.dumps(parts, ensure_ascii=False)


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
        item["date_display"] = str(item.get("date") or item.get("started_at_display")[:10] or "—")
        item["duration_mmss"] = format_mmss(item.get("duration_s"))
        item["players_display"] = display_player_names(item.get("player_names_json"))
        games.append(item)
    return games


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


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

        dst.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        dst.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))

        game_values = dict(game_row)
        game_cols = list(game_values.keys())
        dst.execute(
            f"INSERT INTO games ({', '.join(game_cols)}) VALUES ({', '.join(['?'] * len(game_cols))})",
            tuple(game_values[col] for col in game_cols),
        )

        for rows, table_name in ((riddle_rows, "game_riddles"), (hint_rows, "game_hints")):
            for row in rows:
                values = dict(row)
                cols = list(values.keys())
                dst.execute(
                    f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                    tuple(values[col] for col in cols),
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

    game["player_names_json"] = _maybe_json(game.get("player_names_json"))
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
                if column == "players_display":
                    normalized_updates["player_names_json"] = parse_player_names_input(value)
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
            game_id = str(current[2])
            current_riddle = str(current[3])
            normalized_updates: dict[str, Any] = {}

            all_rows = conn.execute(
                "SELECT rowid, solve_time_from_run_start_s FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
                (game_id,),
            ).fetchall()
            previous_solve = 0.0
            for item in all_rows:
                if int(item[0]) == int(rowid):
                    break
                try:
                    previous_solve = float(item[1] or 0)
                except Exception:
                    previous_solve = 0.0

            pending_riddle_name = current_riddle
            pending_hint_count = None
            leaderboard_code_value = None

            for key, value in updates.items():
                column = str(key or "").strip()
                if column == "solve_time_mmss":
                    normalized_updates["solve_time_from_run_start_s"] = parse_mmss_input(value)
                elif column == "riddle_time_mmss":
                    if "solve_time_mmss" not in updates:
                        delta = parse_mmss_input(value)
                        normalized_updates["solve_time_from_run_start_s"] = None if delta is None else previous_solve + delta
                elif column == "hint_count_display":
                    pending_hint_count = max(int(float(str(value).strip() or "0")), 0)
                elif column in {"leaderboard_code", "leaderboard_code_display"}:
                    leaderboard_code_value = str(value or "").strip() or None
                elif column == "riddle":
                    pending_riddle_name = str(value or "").strip()
                    normalized_updates["riddle"] = pending_riddle_name
                elif column in editable_columns:
                    normalized_updates[column] = value
                else:
                    raise ValueError(f"Column {column or key!r} is not editable in table {table_name}.")

            if normalized_updates:
                set_clause = ", ".join(f"{column} = ?" for column in normalized_updates.keys())
                values = [normalized_updates[column] for column in normalized_updates.keys()]
                values.append(rowid)
                conn.execute(f"UPDATE game_riddles SET {set_clause} WHERE rowid = ?", values)

            if leaderboard_code_value is not None or any(str(k) in {"leaderboard_code", "leaderboard_code_display"} for k in updates):
                conn.execute("UPDATE games SET leaderboard_code = ? WHERE id = ?", (leaderboard_code_value, game_id))

            if pending_riddle_name != current_riddle:
                conn.execute(
                    "UPDATE game_hints SET riddle = ? WHERE game_id = ? AND riddle = ?",
                    (pending_riddle_name, game_id, current_riddle),
                )

            if pending_hint_count is not None:
                update_hint_rows_for_riddle(conn, game_id, pending_riddle_name, pending_hint_count)
                total_hints = conn.execute("SELECT COUNT(*) FROM game_hints WHERE game_id = ?", (game_id,)).fetchone()[0]
                conn.execute("UPDATE games SET hint_count = ? WHERE id = ?", (int(total_hints or 0), game_id))

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
            loaded = load_game_from_db(game_id)
            game = loaded["game"]
            riddles = loaded["riddles"]
            hints = loaded["hints"]
            if game is None:
                error = f"No game found for id {game_id}."
            else:
                game["started_at_display"] = format_datetime_readable(game.get("started_at") or game.get("game_started_at") or game.get("date"))
                game["ended_at_display"] = format_datetime_readable(game.get("ended_at"))
                game["duration_mmss"] = format_mmss(game.get("duration_s"))
                game["players_display"] = display_player_names(game.get("player_names_json"))
                game["hint_count_display"] = int(game.get("hint_count") or 0)
                game["leaderboard_code_display"] = serialize_db_value(game.get("leaderboard_code")) or ""

                riddle_hint_counts: dict[str, int] = {}
                for hint in hints:
                    name = str(hint.get("riddle") or "").strip()
                    if name:
                        riddle_hint_counts[name] = riddle_hint_counts.get(name, 0) + 1

                previous_solve_time = 0.0
                for row in riddles:
                    current_solve_time = row.get("solve_time_from_run_start_s")
                    row["solve_time_mmss"] = format_mmss(current_solve_time)
                    try:
                        current_seconds = float(current_solve_time)
                    except Exception:
                        current_seconds = None

                    if current_seconds is None:
                        row["riddle_time_mmss"] = "—"
                    else:
                        delta_seconds = max(0.0, current_seconds - previous_solve_time)
                        row["riddle_time_mmss"] = format_mmss(delta_seconds)
                        previous_solve_time = current_seconds

                    row["hint_count_display"] = int(riddle_hint_counts.get(str(row.get("riddle") or ""), 0))

                summary_columns = [col for col in ["id", "date", "player_names_json", "hint_count", "leaderboard_code"] if col in game]
                riddle_columns = ["riddle", "solve_time_mmss", "riddle_time_mmss", "hint_count_display"]
                hint_columns = build_editable_columns(hints, preferred=["at", "riddle", "hint_text"])

                raw_rows = [
                    {"table": "games", "rowid": game.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in game.items() if not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
                ]
                raw_rows.extend(
                    {"table": "game_riddles", "rowid": row.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in row.items() if not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
                    for row in riddles
                )
                raw_rows.extend(
                    {"table": "game_hints", "rowid": row.get("_rowid_"), "raw_json": json.dumps(row, ensure_ascii=False, indent=2, default=str)}
                    for row in hints
                )
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


@app.post("/api/players")
def api_players() -> Any:
    data = request.get_json(force=True)
    players = data.get("players") or []
    if not isinstance(players, list):
        return jsonify({"ok": False, "error": "players must be a list"}), 400
    cleaned = [str(x).strip() for x in players if str(x).strip()]
    mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_players", "players": cleaned})
    return jsonify({"ok": True, "players": cleaned})


@app.post("/api/solve")
def api_solve() -> Any:
    data = request.get_json(force=True)
    node = str(data.get("node", "")).strip()
    if not node:
        return jsonify({"ok": False, "error": "node required"}), 400
    mqtt_publish(TOPIC_GAME_CMD, {"cmd": "solve", "node": node})
    return jsonify({"ok": True})


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("ER1_DASHBOARD_PORT", "8080")), debug=False)
