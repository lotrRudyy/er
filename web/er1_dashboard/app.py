
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
BROKER_HOST = os.getenv("ER1_MQTT_HOST", "192.168.0.10")
BROKER_PORT = int(os.getenv("ER1_MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("ER1_DASHBOARD_CLIENT_ID", "er1_dashboard")
HINTS_PATH = BASE_DIR / "dashboard_hints.json"

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
            self.node_states[node_id] = payload
            riddle_id = str(payload.get("id", "")).strip()
            if riddle_id:
                self.riddle_states[riddle_id] = payload

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
    def _extract_star_slider_summary(cls, riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id != "star_slider" or not state_payload:
            return None
        tries = cls._extract_tries(state_payload)
        positions = state_payload.get("reader_positions") or {}
        current: list[str] = []
        if isinstance(positions, dict):
            for key in ["r0", "r1", "r2"]:
                current.append(str(positions.get(key, "none") or "none").strip())
        elif isinstance(state_payload.get("reader_labels"), list):
            current = [str(x).strip() or "none" for x in state_payload.get("reader_labels", [])[:3]]
        attempts = []
        for item in state_payload.get("attempted_star_signs") or []:
            if not isinstance(item, dict):
                continue
            pos = item.get("positions") or {}
            if not isinstance(pos, dict):
                continue
            vals = [str(pos.get(key, "none") or "none").strip() for key in ["r0", "r1", "r2"]]
            attempts.append(vals)
        return {"tries": tries, "current": current, "attempts": attempts}

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


def parse_json_payload(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        return data if isinstance(data, dict) else None
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
