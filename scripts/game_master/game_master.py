from __future__ import annotations

import copy
import json
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

import config
from db import Database
from models import CurrentRun, RiddleTiming, RuntimeState, ScheduledAction
from phases import PHASES, ADMIN_TARGET_PHASE, RIDDLE_SOLVE_EVENTS

LOG = logging.getLogger("game_master")

RESETTABLE_RIDDLES = {"prison", "wheel", "chains", "tangram", "magnet"}
CHECKPOINT_SCHEMA = "er1.game_master.active_run"
CHECKPOINT_VERSION = 2
MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
CHECKPOINT_SIZE_MARGIN_BYTES = 64 * 1024
MAX_RECOVERABLE_ELAPSED_S = 7 * 24 * 60 * 60
MAX_RUN_EVENTS = 2000
MAX_EVENT_BYTES = 65536
MAX_EVENT_HISTORY_BYTES = 256 * 1024
MAX_EVENT_DEPTH = 8
MAX_EVENT_STRING_LENGTH = 4096
MAX_EVENT_KEY_LENGTH = 128
MAX_EVENT_COLLECTION_ITEMS = 100
MAX_EVENT_INTEGER_ABS = (1 << 63) - 1
MAX_HINTS_BYTES = 48 * 1024
MAX_BOOKING_BYTES = 65536
MAX_BOOKING_FIELD_BYTES = 8192
_CONTROL_CHAR_TRANSLATION = {codepoint: None for codepoint in range(32) if codepoint not in {9, 10}}


class RecoveryPersistenceError(RuntimeError):
    pass


class CheckpointPersistenceError(RecoveryPersistenceError):
    pass


def _serialized_mutation(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: "GameMaster", *args: Any, **kwargs: Any) -> Any:
        with self._checkpoint_io_lock:
            if self._shutting_down.is_set():
                raise RuntimeError("game master is shutting down")
            with self._lock:
                previous_state = copy.deepcopy(self.state)
                persistence_revision = self._persistence_revision
            try:
                return method(self, *args, **kwargs)
            except Exception:
                if self._persistence_revision == persistence_revision:
                    with self._lock:
                        self.state = previous_state
                raise
    return wrapped


def _fsync_directory(path: Path) -> None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
    except (AttributeError, OSError):
        if os.name == "posix":
            raise
        return
    try:
        os.fsync(fd)
    except OSError:
        if os.name == "posix":
            raise
    finally:
        os.close(fd)


def _fsync_parent(path: Path) -> None:
    _fsync_directory(path.parent)


def _ensure_directory_durable(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(path)
        return
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if not cursor.exists() or not cursor.is_dir():
        raise NotADirectoryError(cursor)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
        _fsync_directory(directory.parent)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bool:
    """Atomically replace ``path`` and report whether its directory was synced.

    Exceptions are raised only before ``os.replace``. Once the rename succeeds,
    the visible file is the logical commit. A failed final directory fsync means
    a crash may expose either directory state; it cannot safely be represented as
    a rejected write because callers must not roll memory back behind the new file.
    """
    _ensure_directory_durable(path.parent)
    _fsync_parent(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            _fsync_parent(path)
        except Exception:
            LOG.critical(
                "Atomic JSON replace is visible but directory fsync failed; treating write as committed with crash-durability uncertainty path=%s",
                path,
                exc_info=True,
            )
            return False
        return True
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except OSError:
            pass

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GameMaster:
    def __init__(
        self,
        *,
        db: Any | None = None,
        db_path: str | Path | None = None,
        runs_dir: str | Path | None = None,
        checkpoint_path: str | Path | None = None,
        mqtt_client: Any | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        utc_now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._monotonic = monotonic_fn or time.monotonic
        self._utc_now = utc_now_fn or utc_now
        self.state = RuntimeState(phase=config.DEFAULT_PHASE)
        self.db = db if db is not None else Database(str(db_path or config.DB_PATH))
        self.runs_dir = Path(runs_dir or config.RUNS_DIR)
        _ensure_directory_durable(self.runs_dir)
        self.checkpoint_path = Path(checkpoint_path or config.ACTIVE_RUN_CHECKPOINT_PATH)
        self._checkpoint_interval_s = max(1.0, float(config.ACTIVE_RUN_CHECKPOINT_INTERVAL_S))

        self._client = mqtt_client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="game-master")
        self._client.enable_logger(LOG)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        self._lock = threading.RLock()
        self._checkpoint_io_lock = threading.RLock()
        self._stop = threading.Event()
        self._shutting_down = threading.Event()
        self._started = False
        self._last_checkpoint_monotonic: float | None = None
        self._last_checkpoint_attempt_monotonic: float | None = None
        self._last_completion_retry_monotonic: float | None = None
        self._completion_checkpoint_cleanup_pending = False
        self._persistence_revision = 0
        self._checkpoint_writes_blocked = False
        self._hardware_effects_blocked = False
        self._startup_recovery_fault = False
        self._bootstrapped = False
        self._restored_checkpoint = self._load_active_checkpoint()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)

    def start(self) -> None:
        with self._checkpoint_io_lock:
            if self._started:
                return
            if self._shutting_down.is_set():
                raise RuntimeError("game master cannot restart after shutdown")
            self._client.connect(config.MQTT_HOST, config.MQTT_PORT, config.MQTT_KEEPALIVE)
            if self._startup_recovery_fault:
                LOG.critical("Startup remains in safe standby because the checkpoint directory durability barrier failed")
                self.publish_game_state()
            elif self._restored_checkpoint:
                self._reconcile_stable_state("startup_restore")
            else:
                self._enter_phase(self.state.phase, "boot_settle")
            if self.state.phase == 14 and self.state.current_run is not None:
                self._retry_completed_persistence("startup_restore")
            self._bootstrapped = True
            self._started = True
            self._client.loop_start()
            self._scheduler_thread.start()

    def stop(self) -> None:
        if self._shutting_down.is_set():
            return
        self._shutting_down.set()
        self._stop.set()
        self._client.loop_stop()
        if self._scheduler_thread.is_alive() and self._scheduler_thread is not threading.current_thread():
            self._scheduler_thread.join(timeout=2.0)
        with self._checkpoint_io_lock:
            completion_is_durable = (
                self.state.phase == 14
                and self.state.completion_db_saved
                and self.state.completion_json_saved
            )
            if completion_is_durable:
                if self._completion_checkpoint_cleanup_pending or self.checkpoint_path.exists():
                    self._remove_completed_checkpoint()
            elif 2 <= self.state.phase <= 14 and self.state.current_run is not None:
                if not self._checkpoint_now("graceful_stop"):
                    LOG.error("Final graceful-shutdown checkpoint failed; previous checkpoint was preserved")
            self._started = False
        self._client.disconnect()

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if self._shutting_down.is_set():
            return
        LOG.info("MQTT connected rc=%s", reason_code)
        client.subscribe(config.TOPIC_GAME_EVENT)
        client.subscribe(config.TOPIC_GAME_CMD)
        client.subscribe(config.TOPIC_HB_WILDCARD)
        client.subscribe(config.TOPIC_NODE_STATE_WILDCARD)
        if self._startup_recovery_fault:
            self.publish_game_state()
        elif self._bootstrapped:
            self._reconcile_stable_state("mqtt_connect")
        else:
            self.publish_game_state()

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOG.warning("MQTT disconnected rc=%s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        with self._checkpoint_io_lock:
            if self._shutting_down.is_set():
                LOG.info("Ignoring MQTT message during shutdown topic=%s", message.topic)
                return
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

    def _now_iso(self) -> str:
        now = self._utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.isoformat(timespec="seconds")

    @staticmethod
    def _checked_string(value: Any, name: str, *, allow_none: bool = False, max_length: int = 10000) -> str | None:
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or not value or len(value) > max_length or "\x00" in value:
            raise ValueError(f"checkpoint {name} must be a non-empty string")
        return value

    @staticmethod
    def _checked_bool(value: Any, name: str) -> bool:
        if type(value) is not bool:
            raise ValueError(f"checkpoint {name} must be a boolean")
        return value

    @staticmethod
    def _checked_int(value: Any, name: str, minimum: int, maximum: int) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"checkpoint {name} must be an integer in {minimum}..{maximum}")
        return value

    @staticmethod
    def _checked_seconds(value: Any, name: str, *, allow_none: bool = False) -> float | None:
        if value is None and allow_none:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"checkpoint {name} must be a number")
        seconds = float(value)
        if not math.isfinite(seconds) or not 0.0 <= seconds <= MAX_RECOVERABLE_ELAPSED_S:
            raise ValueError(f"checkpoint {name} is outside the recoverable range")
        return seconds

    @classmethod
    def _reject_monotonic_fields(cls, value: Any, location: str = "checkpoint") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"{location} contains a non-string key")
                if "monotonic" in key.lower():
                    raise ValueError(f"{location} contains a reusable monotonic field")
                cls._reject_monotonic_fields(item, f"{location}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._reject_monotonic_fields(item, f"{location}[{index}]")

    @classmethod
    def _validate_json_data(cls, value: Any, location: str, depth: int = 0) -> None:
        if depth > 20:
            raise ValueError(f"checkpoint {location} is nested too deeply")
        if value is None or isinstance(value, (bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"checkpoint {location} contains a non-finite number")
            return
        if isinstance(value, str):
            if len(value) > 1000000 or "\x00" in value:
                raise ValueError(f"checkpoint {location} contains an invalid string")
            return
        if isinstance(value, list):
            if len(value) > 10000:
                raise ValueError(f"checkpoint {location} contains too many items")
            for index, item in enumerate(value):
                cls._validate_json_data(item, f"{location}[{index}]", depth + 1)
            return
        if isinstance(value, dict):
            if len(value) > 10000:
                raise ValueError(f"checkpoint {location} contains too many fields")
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 1000 or "\x00" in key:
                    raise ValueError(f"checkpoint {location} contains an invalid key")
                cls._validate_json_data(item, f"{location}.{key}", depth + 1)
            return
        raise ValueError(f"checkpoint {location} contains unsupported data")

    @staticmethod
    def _validate_iso_datetime(value: str | None, name: str, *, allow_none: bool = False) -> str | None:
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError(f"checkpoint {name} must be an ISO datetime")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"checkpoint {name} must be an ISO datetime") from exc
        return value

    @staticmethod
    def _truncate_utf8(value: Any, max_bytes: int, *, keep_tail: bool = False) -> str:
        text = str(value or "").translate(_CONTROL_CHAR_TRANSLATION)
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= max_bytes:
            return encoded.decode("utf-8")
        chunk = encoded[-max_bytes:] if keep_tail else encoded[:max_bytes]
        return chunk.decode("utf-8", errors="ignore")

    @staticmethod
    def _json_bytes(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _checkpoint_json_bytes(payload: dict[str, Any]) -> int:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        return len(serialized.encode("utf-8"))

    @classmethod
    def _bounded_json_value(
        cls,
        value: Any,
        *,
        max_string_bytes: int,
        depth: int = 0,
    ) -> Any:
        if depth >= MAX_EVENT_DEPTH:
            return "[depth limit]"
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return max(-MAX_EVENT_INTEGER_ABS, min(MAX_EVENT_INTEGER_ABS, value))
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            return cls._truncate_utf8(value, max_string_bytes)
        if isinstance(value, list):
            return [
                cls._bounded_json_value(item, max_string_bytes=max_string_bytes, depth=depth + 1)
                for item in value[:MAX_EVENT_COLLECTION_ITEMS]
            ]
        if isinstance(value, dict):
            bounded: dict[str, Any] = {}
            for raw_key, item in list(value.items())[:MAX_EVENT_COLLECTION_ITEMS]:
                key = cls._truncate_utf8(raw_key, MAX_EVENT_KEY_LENGTH)
                bounded[key] = cls._bounded_json_value(
                    item,
                    max_string_bytes=max_string_bytes,
                    depth=depth + 1,
                )
            return bounded
        return cls._truncate_utf8(value, max_string_bytes)

    @classmethod
    def _bounded_booking(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        bounded = cls._bounded_json_value(value, max_string_bytes=MAX_BOOKING_FIELD_BYTES)
        if not isinstance(bounded, dict):
            return {}
        preferred = (
            "id", "kind", "bookingCode", "booking_code", "date", "slot",
            "players", "players_count", "customerEmail", "customer_email",
            "customerName", "customer_name", "language", "bookingStatus",
            "paymentStatus", "label",
        )
        ordered_keys = [key for key in preferred if key in bounded]
        ordered_keys.extend(key for key in bounded if key not in ordered_keys)
        result: dict[str, Any] = {}
        for key in ordered_keys:
            item = bounded[key]
            candidate = {**result, key: item}
            if cls._json_bytes(candidate) <= MAX_BOOKING_BYTES:
                result[key] = item
                continue
            if not isinstance(item, str):
                continue
            low = 0
            high = len(item.encode("utf-8"))
            best = ""
            while low <= high:
                middle = (low + high) // 2
                truncated = cls._truncate_utf8(item, middle)
                if cls._json_bytes({**result, key: truncated}) <= MAX_BOOKING_BYTES:
                    best = truncated
                    low = middle + 1
                else:
                    high = middle - 1
            if best or cls._json_bytes({**result, key: ""}) <= MAX_BOOKING_BYTES:
                result[key] = best
        return result

    def _checkpoint_payload_locked(self, reason: str) -> dict[str, Any]:
        run = self.state.current_run
        if run is None:
            raise ValueError("cannot checkpoint without a current run")
        now_mono = self._monotonic()
        timing_payloads: dict[str, dict[str, Any]] = {}
        live_payloads: dict[str, dict[str, Any]] = {}
        for riddle in config.RIDDLES:
            timing = run.riddle_timings[riddle]
            first_elapsed = None
            if timing.first_started_monotonic is not None:
                first_elapsed = round(max(0.0, now_mono - timing.first_started_monotonic), 3)
            segment_elapsed = None
            if timing.segment_started_monotonic is not None:
                segment_elapsed = round(max(0.0, now_mono - timing.segment_started_monotonic), 3)
            status = timing.status()
            hints = self._truncate_utf8(timing.hints or "", MAX_HINTS_BYTES, keep_tail=True)
            if hints != timing.hints:
                timing.hints = hints
            timing_payloads[riddle] = {
                "riddle_key": timing.riddle_key,
                "solve_time_s": round(max(0.0, float(timing.solve_time_s or 0)), 3),
                "hint_count": max(0, int(timing.hint_count or 0)),
                "hints": hints,
                "skipped": bool(timing.skipped),
                "not_solved": bool(timing.not_solved),
                "reset_pending": bool(timing.reset_pending),
                "status": status,
                "first_elapsed_s": first_elapsed,
                "segment_elapsed_s": segment_elapsed,
            }
            live_payloads[riddle] = {
                "display_time_s": segment_elapsed if segment_elapsed is not None else float(timing.solve_time_s or 0),
            }

        run_elapsed_s = self._compute_live_effective_duration_s(live_payloads)
        booking = self._bounded_booking(run.booking)
        if booking != run.booking:
            run.booking = copy.deepcopy(booking)
        leaderboard_code = None if run.leaderboard_code is None else self._truncate_utf8(run.leaderboard_code, 128)
        if leaderboard_code != run.leaderboard_code:
            run.leaderboard_code = leaderboard_code
        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "version": CHECKPOINT_VERSION,
            "saved_at": self._now_iso(),
            "reason": self._truncate_utf8(reason or "periodic", 100),
            "state": {
                "phase": int(self.state.phase),
                "last_phase": self.state.last_phase,
                "lighting_phase": int(self.state.phase),
                "phase_generation": int(self.state.phase_generation),
                "game_started_at": self.state.game_started_at,
                "last_riddle_solved_at": self.state.last_riddle_solved_at,
                "timer_running": self.state.game_started_at is not None and 3 <= self.state.phase < 14,
                "completed_phase_events": sorted(self.state.completed_phase_events),
                "completion": {
                    "db_saved": bool(self.state.completion_db_saved),
                    "json_saved": bool(self.state.completion_json_saved),
                },
                "durability_degraded": bool(self.state.recovery_durability_degraded),
            },
            "run": {
                "run_id": run.run_id,
                "date": run.date,
                "started_at": run.started_at,
                "run_elapsed_s": run_elapsed_s,
                "players_count": int(run.players_count or 0),
                "booking": copy.deepcopy(booking),
                "leaderboard_code": leaderboard_code,
                "events": [],
                "ended_at": run.ended_at,
                "duration_s": run.duration_s,
                "riddle_timings": timing_payloads,
            },
        }
        target_bytes = MAX_CHECKPOINT_BYTES - CHECKPOINT_SIZE_MARGIN_BYTES
        core_bytes = self._checkpoint_json_bytes(payload)
        if core_bytes > target_bytes:
            raise ValueError("active-run checkpoint core exceeds its size budget")
        event_budget = min(MAX_EVENT_HISTORY_BYTES, max(2, target_bytes - core_bytes))
        event_history = self._bounded_event_history(run.events, max_bytes=event_budget)
        payload["run"]["events"] = copy.deepcopy(event_history)
        while event_history and self._checkpoint_json_bytes(payload) > target_bytes:
            event_history.pop(0)
            payload["run"]["events"] = copy.deepcopy(event_history)
        if self._checkpoint_json_bytes(payload) > target_bytes:
            raise ValueError("active-run checkpoint exceeds its size budget")
        if event_history != run.events:
            run.events = copy.deepcopy(event_history)
        self._reject_monotonic_fields(payload)
        return payload

    def _checkpoint_now(self, reason: str) -> bool:
        with self._checkpoint_io_lock:
            self._last_checkpoint_attempt_monotonic = self._monotonic()
            if self._checkpoint_writes_blocked:
                return False
            with self._lock:
                if not 2 <= self.state.phase <= 14 or self.state.current_run is None:
                    return False
                if self.state.phase == 14 and self.state.completion_db_saved and self.state.completion_json_saved:
                    return False
                try:
                    payload = self._checkpoint_payload_locked(reason)
                    self._restore_checkpoint_payload(payload)
                    if self._checkpoint_json_bytes(payload) > MAX_CHECKPOINT_BYTES - CHECKPOINT_SIZE_MARGIN_BYTES:
                        raise ValueError("active-run checkpoint exceeds size limit")
                except Exception:
                    LOG.exception("Active-run checkpoint snapshot/validation failed; previous checkpoint preserved")
                    return False
            try:
                directory_synced = _atomic_write_json(self.checkpoint_path, payload)
            except Exception:
                LOG.exception("Active-run checkpoint write failed path=%s; previous checkpoint preserved", self.checkpoint_path)
                return False
            if not directory_synced:
                with self._lock:
                    self.state.recovery_durability_degraded = True
                self._hardware_effects_blocked = True
            self._last_checkpoint_monotonic = self._monotonic()
            self._persistence_revision += 1
            return directory_synced

    def _checkpoint_if_due(self) -> None:
        now_mono = self._monotonic()
        with self._lock:
            checkpoint_pending = (
                2 <= self.state.phase <= 14
                and self.state.current_run is not None
                and not (
                    self.state.phase == 14
                    and self.state.completion_db_saved
                    and self.state.completion_json_saved
                )
            )
            completion_retry_pending = (
                self.state.phase == 14
                and self.state.current_run is not None
                and (
                    not (self.state.completion_db_saved and self.state.completion_json_saved)
                    or self._completion_checkpoint_cleanup_pending
                )
            )
        if not checkpoint_pending and not completion_retry_pending:
            return
        last_attempt = self._last_checkpoint_attempt_monotonic
        if checkpoint_pending and (last_attempt is None or now_mono - last_attempt >= self._checkpoint_interval_s):
            self._checkpoint_now("periodic")
        last_retry = self._last_completion_retry_monotonic
        if completion_retry_pending and (last_retry is None or now_mono - last_retry >= self._checkpoint_interval_s):
            self._retry_completed_persistence("periodic_retry")

    def _checkpoint_after_mutation(self, reason: str) -> None:
        with self._checkpoint_io_lock:
            with self._lock:
                finished = self.state.phase == 14 and self.state.current_run is not None
                if finished:
                    self.state.completion_db_saved = False
                    self.state.completion_json_saved = False
            if not self._checkpoint_now(reason):
                raise CheckpointPersistenceError(f"active-run checkpoint failed after {reason}")
            if finished and not self._retry_completed_persistence(f"{reason}_retry"):
                raise RecoveryPersistenceError(f"completed-run persistence remains pending after {reason}")

    def _quarantine_checkpoint(self, error: Exception) -> None:
        stamp = self._utc_now().strftime("%Y%m%dT%H%M%SZ")
        quarantine = self.checkpoint_path.with_name(
            f"{self.checkpoint_path.name}.invalid.{stamp}.{uuid.uuid4().hex[:8]}"
        )
        try:
            os.replace(self.checkpoint_path, quarantine)
            _fsync_parent(quarantine)
        except Exception:
            self._checkpoint_writes_blocked = True
            LOG.exception(
                "Invalid active-run checkpoint could not be quarantined and will not be overwritten path=%s error=%s",
                self.checkpoint_path,
                error,
            )
            return
        LOG.error(
            "Invalid active-run checkpoint quarantined path=%s quarantine=%s error=%s; starting in safe standby",
            self.checkpoint_path,
            quarantine,
            error,
        )

    def _load_active_checkpoint(self) -> bool:
        if not self.checkpoint_path.exists():
            return False
        try:
            if self.checkpoint_path.stat().st_size > MAX_CHECKPOINT_BYTES - CHECKPOINT_SIZE_MARGIN_BYTES:
                raise ValueError("checkpoint exceeds size limit")
            raw = self.checkpoint_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("checkpoint root must be an object")
            self._reject_monotonic_fields(payload)
            restored = self._restore_checkpoint_payload(payload)
        except Exception as exc:
            self._quarantine_checkpoint(exc)
            self.state = RuntimeState(phase=config.DEFAULT_PHASE)
            return False
        try:
            _fsync_parent(self.checkpoint_path)
        except Exception:
            self._checkpoint_writes_blocked = True
            self._hardware_effects_blocked = True
            self._startup_recovery_fault = True
            self.state = RuntimeState(
                phase=config.DEFAULT_PHASE,
                recovery_durability_degraded=True,
            )
            LOG.critical(
                "Valid checkpoint was not used because its directory durability barrier failed path=%s",
                self.checkpoint_path,
                exc_info=True,
            )
            return False
        self.state = restored
        self._last_checkpoint_monotonic = self._monotonic()
        self._last_checkpoint_attempt_monotonic = self._last_checkpoint_monotonic
        LOG.warning(
            "Restored active run run_id=%s phase=%s from checkpoint=%s; downtime is paused",
            restored.current_run.run_id if restored.current_run else "",
            restored.phase,
            self.checkpoint_path,
        )
        return True

    def _restore_checkpoint_payload(self, payload: dict[str, Any]) -> RuntimeState:
        if payload.get("schema") != CHECKPOINT_SCHEMA:
            raise ValueError("unsupported checkpoint schema")
        if type(payload.get("version")) is not int or payload.get("version") != CHECKPOINT_VERSION:
            raise ValueError("unsupported checkpoint version")
        saved_at = self._validate_iso_datetime(payload.get("saved_at"), "saved_at")
        state_payload = payload.get("state")
        run_payload = payload.get("run")
        if not isinstance(state_payload, dict) or not isinstance(run_payload, dict):
            raise ValueError("checkpoint state and run must be objects")

        phase = self._checked_int(state_payload.get("phase"), "state.phase", 2, 14)
        last_phase_raw = state_payload.get("last_phase")
        last_phase = None if last_phase_raw is None else self._checked_int(last_phase_raw, "state.last_phase", 0, 14)
        lighting_phase = self._checked_int(state_payload.get("lighting_phase"), "state.lighting_phase", 0, 14)
        if lighting_phase != phase:
            raise ValueError("checkpoint must contain the stable lighting phase")
        phase_generation = self._checked_int(
            state_payload.get("phase_generation"), "state.phase_generation", 0, (1 << 63) - 1
        )

        game_started_at = self._validate_iso_datetime(
            state_payload.get("game_started_at"), "state.game_started_at", allow_none=True
        )
        last_riddle_solved_at = self._validate_iso_datetime(
            state_payload.get("last_riddle_solved_at"), "state.last_riddle_solved_at", allow_none=True
        )
        if phase == 2 and game_started_at is not None:
            raise ValueError("prepared checkpoint cannot have a running timer")
        if phase >= 3 and game_started_at is None:
            raise ValueError("active checkpoint is missing game start metadata")
        timer_running = self._checked_bool(state_payload.get("timer_running"), "state.timer_running")
        if timer_running != (game_started_at is not None and 3 <= phase < 14):
            raise ValueError("checkpoint timer metadata is inconsistent")

        completed_events_raw = state_payload.get("completed_phase_events")
        if not isinstance(completed_events_raw, list) or any(not isinstance(item, str) for item in completed_events_raw):
            raise ValueError("checkpoint completed phase events must be a string list")
        completed_events = set(completed_events_raw)
        if len(completed_events) != len(completed_events_raw):
            raise ValueError("checkpoint completed phase events contain duplicates")
        if not completed_events.issubset(set(PHASES[phase].required_events)):
            raise ValueError("checkpoint completed phase events do not belong to the current phase")
        if PHASES[phase].required_events and set(PHASES[phase].required_events).issubset(completed_events):
            raise ValueError("checkpoint contains a completed gate that should already have transitioned")

        completion = state_payload.get("completion")
        if not isinstance(completion, dict):
            raise ValueError("checkpoint completion metadata must be an object")
        db_saved = self._checked_bool(completion.get("db_saved"), "state.completion.db_saved")
        json_saved = self._checked_bool(completion.get("json_saved"), "state.completion.json_saved")
        if phase < 14 and (db_saved or json_saved):
            raise ValueError("active checkpoint cannot be marked completed")
        if json_saved and not db_saved:
            raise ValueError("checkpoint JSON cannot precede database persistence")
        durability_degraded = self._checked_bool(
            state_payload.get("durability_degraded"), "state.durability_degraded"
        )

        run_id = self._checked_string(run_payload.get("run_id"), "run.run_id", max_length=256)
        safe_run_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        if not run_id.startswith("run_") or any(ch not in safe_run_chars for ch in run_id):
            raise ValueError("checkpoint run.run_id has an unsafe format")
        run_date = self._checked_string(run_payload.get("date"), "run.date", max_length=32)
        try:
            datetime.strptime(run_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("checkpoint run.date must be YYYY-MM-DD") from exc
        started_at = self._validate_iso_datetime(run_payload.get("started_at"), "run.started_at")
        run_elapsed_s = self._checked_seconds(run_payload.get("run_elapsed_s"), "run.run_elapsed_s")
        players_count = self._checked_int(run_payload.get("players_count"), "run.players_count", 0, 1000)
        booking = run_payload.get("booking")
        if (
            not isinstance(booking, dict)
            or self._json_bytes(booking) > MAX_BOOKING_BYTES
        ):
            raise ValueError("checkpoint run.booking must be a small object")
        self._validate_json_data(booking, "run.booking")
        if self._bounded_booking(booking) != booking:
            raise ValueError("checkpoint run.booking violates recursive byte bounds")
        leaderboard_code = run_payload.get("leaderboard_code")
        if leaderboard_code is not None:
            leaderboard_code = self._checked_string(
                leaderboard_code, "run.leaderboard_code", max_length=128
            )
        ended_at = self._validate_iso_datetime(run_payload.get("ended_at"), "run.ended_at", allow_none=True)
        duration_s = self._checked_seconds(run_payload.get("duration_s"), "run.duration_s", allow_none=True)
        events = run_payload.get("events")
        if not isinstance(events, list) or len(events) > MAX_RUN_EVENTS or any(not isinstance(item, dict) for item in events):
            raise ValueError("checkpoint run.events must be a bounded object list")
        self._validate_json_data(events, "run.events")
        if any(self._event_json_bytes(item) > MAX_EVENT_BYTES for item in events):
            raise ValueError("checkpoint contains an oversized event")
        if self._event_json_bytes(events) > MAX_EVENT_HISTORY_BYTES:
            raise ValueError("checkpoint event history exceeds its aggregate budget")
        if self._bounded_event_history(events) != events:
            raise ValueError("checkpoint event history violates recursive bounds")

        raw_timings = run_payload.get("riddle_timings")
        if not isinstance(raw_timings, dict) or set(raw_timings) != set(config.RIDDLES):
            raise ValueError("checkpoint riddle set does not match configuration")
        now_mono = self._monotonic()
        run = CurrentRun(
            run_id=run_id,
            date=run_date,
            started_at=started_at,
            started_monotonic=now_mono - run_elapsed_s,
            players_count=players_count,
            booking=copy.deepcopy(booking),
            leaderboard_code=leaderboard_code,
            events=copy.deepcopy(events),
            ended_at=ended_at,
            duration_s=duration_s,
        )

        valid_statuses = {"pending", "active", "reset", "solved", "skipped", "not_solved"}
        for riddle in config.RIDDLES:
            item = raw_timings[riddle]
            if not isinstance(item, dict) or item.get("riddle_key") != riddle:
                raise ValueError(f"checkpoint timing is invalid for {riddle}")
            solve_time_s = self._checked_seconds(item.get("solve_time_s"), f"{riddle}.solve_time_s")
            hint_count = self._checked_int(item.get("hint_count"), f"{riddle}.hint_count", 0, 100000)
            hints = item.get("hints")
            if (
                not isinstance(hints, str)
                or hints != self._truncate_utf8(hints, MAX_HINTS_BYTES, keep_tail=True)
            ):
                raise ValueError(f"checkpoint hints are invalid for {riddle}")
            skipped = self._checked_bool(item.get("skipped"), f"{riddle}.skipped")
            not_solved = self._checked_bool(item.get("not_solved"), f"{riddle}.not_solved")
            reset_pending = self._checked_bool(item.get("reset_pending"), f"{riddle}.reset_pending")
            status = item.get("status")
            if status not in valid_statuses:
                raise ValueError(f"checkpoint status is invalid for {riddle}")
            if skipped and not_solved:
                raise ValueError(f"checkpoint has conflicting outcomes for {riddle}")
            first_elapsed = self._checked_seconds(
                item.get("first_elapsed_s"), f"{riddle}.first_elapsed_s", allow_none=True
            )
            segment_elapsed = self._checked_seconds(
                item.get("segment_elapsed_s"), f"{riddle}.segment_elapsed_s", allow_none=True
            )
            if segment_elapsed is not None and first_elapsed is None:
                raise ValueError(f"checkpoint segment lacks first activation for {riddle}")
            if segment_elapsed is not None and first_elapsed is not None and segment_elapsed > first_elapsed + 0.001:
                raise ValueError(f"checkpoint segment predates first activation for {riddle}")
            if reset_pending and (skipped or not_solved or solve_time_s > 0):
                raise ValueError(f"checkpoint reset state conflicts with final data for {riddle}")
            timing = RiddleTiming(
                riddle_key=riddle,
                solve_time_s=solve_time_s,
                hint_count=hint_count,
                hints=hints,
                skipped=skipped,
                not_solved=not_solved,
                first_started_monotonic=None if first_elapsed is None else now_mono - first_elapsed,
                segment_started_monotonic=None if segment_elapsed is None else now_mono - segment_elapsed,
                reset_pending=reset_pending,
            )
            if timing.status() != status:
                raise ValueError(f"checkpoint status metadata is inconsistent for {riddle}")
            if status in {"active", "reset"} and segment_elapsed is None:
                raise ValueError(f"checkpoint active timing lacks elapsed state for {riddle}")
            if status not in {"active", "reset"} and segment_elapsed is not None:
                raise ValueError(f"checkpoint final timing has a running segment for {riddle}")
            run.riddle_timings[riddle] = timing

        for event_name in completed_events:
            riddle = next((key for key, value in RIDDLE_SOLVE_EVENTS.items() if value == event_name), "")
            if not riddle or not run.riddle_timings[riddle].is_final():
                raise ValueError("checkpoint phase-event gate conflicts with riddle outcome")

        spec = PHASES[phase]
        solved_riddles = set(spec.solved_riddles)
        active_riddles = set(spec.active_riddles)
        final_statuses = {"solved", "skipped", "not_solved"}
        for riddle, timing in run.riddle_timings.items():
            status = timing.status()
            if riddle in solved_riddles:
                if status not in final_statuses | {"reset"}:
                    raise ValueError(f"checkpoint has an unfinished historical riddle in phase {phase}: {riddle}")
            elif riddle in active_riddles:
                pending_candles_gate = phase == 10 and riddle == "candles" and status == "pending"
                if status not in final_statuses | {"active", "reset"} and not pending_candles_gate:
                    raise ValueError(f"checkpoint has an inactive current riddle in phase {phase}: {riddle}")
            elif status != "pending":
                raise ValueError(f"checkpoint has riddle state outside phase {phase}: {riddle}")
            if status == "reset" and (
                riddle not in RESETTABLE_RIDDLES
                or riddle not in active_riddles | solved_riddles
            ):
                raise ValueError(f"checkpoint has an impossible historical reset: {riddle}")

        sissi_timing = run.riddle_timings["sissi"]
        if phase < 13 and sissi_timing.status() != "pending":
            raise ValueError("checkpoint has Sissi state before the Sissi phase")
        if phase == 13 and sissi_timing.status() != "active":
            raise ValueError("phase 13 checkpoint must have active Sissi timing")
        if phase == 14:
            if last_phase != 13:
                raise ValueError("finished checkpoint must come directly from phase 13")
            if sissi_timing.status() != "solved" or not sissi_timing.is_final():
                raise ValueError("finished checkpoint requires a genuine solved Sissi timing")
            if any(not timing.is_final() or timing.reset_pending for timing in run.riddle_timings.values()):
                raise ValueError("finished checkpoint contains an unfinished or reset riddle")

        restored = RuntimeState(
            phase=phase,
            lighting_phase=phase,
            last_phase=last_phase,
            game_started_at=game_started_at,
            last_riddle_solved_at=last_riddle_solved_at,
            current_run=run,
            completed_phase_events=completed_events,
            phase_generation=phase_generation,
            completion_db_saved=db_saved,
            completion_json_saved=json_saved,
            recovery_restored=True,
            recovery_checkpoint_saved_at=saved_at,
            recovery_durability_degraded=durability_degraded,
        )
        return restored

    def _remove_completed_checkpoint(self) -> bool:
        with self._checkpoint_io_lock:
            try:
                self.checkpoint_path.unlink()
            except FileNotFoundError:
                try:
                    _fsync_parent(self.checkpoint_path)
                except Exception:
                    self._completion_checkpoint_cleanup_pending = True
                    LOG.exception("Completed checkpoint directory sync retry failed path=%s", self.checkpoint_path)
                    return False
                self._completion_checkpoint_cleanup_pending = False
                self._last_checkpoint_monotonic = None
                self._last_checkpoint_attempt_monotonic = None
                return True
            except Exception:
                self._completion_checkpoint_cleanup_pending = True
                LOG.exception("Completed active-run checkpoint could not be removed path=%s", self.checkpoint_path)
                return False
            try:
                _fsync_parent(self.checkpoint_path)
            except Exception:
                self._completion_checkpoint_cleanup_pending = True
                LOG.exception("Completed checkpoint deletion could not be synced path=%s", self.checkpoint_path)
                return False
            self._completion_checkpoint_cleanup_pending = False
            self._last_checkpoint_monotonic = None
            self._last_checkpoint_attempt_monotonic = None
            return True

    def _stage_active_checkpoint_abandonment(self, reason: str) -> tuple[bool, Path | None]:
        with self._checkpoint_io_lock:
            if self._checkpoint_writes_blocked:
                LOG.error(
                    "Active-run checkpoint abandonment is blocked because an invalid source file must be preserved path=%s reason=%s",
                    self.checkpoint_path,
                    reason,
                )
                return False, None
            if not self.checkpoint_path.exists():
                return True, None
            abandoned = self.checkpoint_path.with_name(
                f"{self.checkpoint_path.name}.abandoned.{self._utc_now().strftime('%Y%m%dT%H%M%SZ')}.{uuid.uuid4().hex[:8]}"
            )
            try:
                os.replace(self.checkpoint_path, abandoned)
                _fsync_parent(abandoned)
            except Exception:
                try:
                    if abandoned.exists() and not self.checkpoint_path.exists():
                        os.replace(abandoned, self.checkpoint_path)
                        _fsync_parent(self.checkpoint_path)
                except Exception:
                    self._checkpoint_writes_blocked = True
                    LOG.critical("Failed restoring checkpoint after abandonment sync failure", exc_info=True)
                LOG.exception(
                    "Active-run checkpoint could not be abandoned safely path=%s reason=%s",
                    self.checkpoint_path,
                    reason,
                )
                return False, None
            return True, abandoned

    @staticmethod
    def _discard_staged_checkpoint(abandoned: Path | None) -> None:
        if abandoned is None:
            return
        try:
            abandoned.unlink()
            _fsync_parent(abandoned)
        except Exception:
            LOG.warning("Abandoned checkpoint archive could not be removed path=%s", abandoned, exc_info=True)

    def _abandon_active_checkpoint(self, reason: str) -> bool:
        with self._checkpoint_io_lock:
            succeeded, abandoned = self._stage_active_checkpoint_abandonment(reason)
            if not succeeded:
                return False
            self._discard_staged_checkpoint(abandoned)
            self._last_checkpoint_monotonic = None
            self._last_checkpoint_attempt_monotonic = None
            self._checkpoint_writes_blocked = False
            self._persistence_revision += 1
            return True

    def _reconcile_stable_state(self, reason: str) -> None:
        with self._checkpoint_io_lock:
            if self._shutting_down.is_set():
                return
            with self._lock:
                phase = int(self.state.phase)
                normalized = self.state.lighting_phase != phase or bool(self.state.pending)
                self.state.lighting_phase = phase
                self.state.pending.clear()
                if normalized:
                    self.state.phase_generation += 1
            if normalized and not self._checkpoint_now(f"stable_scene_{reason}"):
                LOG.error("Stable-scene normalization could not be checkpointed phase=%s", phase)
                return
            if self._hardware_effects_blocked:
                LOG.critical("Stable-scene reconciliation blocked by a checkpoint durability fault phase=%s", phase)
                return
            self._apply_phase_stable_scene(phase, lighting_phase=phase)
            self.publish_game_state()

    def _publish_json(self, topic: str, payload: dict[str, Any], retained: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        self._client.publish(topic, body, qos=0, retain=retained)
        LOG.info("PUB %s %s", topic, body)

    def publish_game_state(self) -> None:
        """Publish firmware-safe state plus dashboard-only live details.

        Physical puzzle nodes subscribe to ``game/state`` and some of them use small
        JSON buffers. Keep that retained message tiny and backwards-compatible so
        nodes can always read the phase/timer state. The dashboard receives the
        larger run/riddle payload on ``game/dashboard_state`` instead.
        """
        with self._lock:
            node_payload = self.state.to_game_state_payload()
            dashboard_payload = dict(node_payload)
            self._enrich_dashboard_state_payload_locked(dashboard_payload)

        self._publish_json(config.TOPIC_GAME_STATE, node_payload, retained=True)
        self._publish_json(config.TOPIC_DASHBOARD_STATE, dashboard_payload, retained=True)

    def _enrich_dashboard_state_payload_locked(self, payload: dict[str, Any]) -> None:
        payload["recovery"] = {
            "restored": bool(self.state.recovery_restored),
            "checkpoint_saved_at": self.state.recovery_checkpoint_saved_at,
            "durability_degraded": bool(self.state.recovery_durability_degraded),
            "startup_fault": bool(self._startup_recovery_fault),
        }
        run = self.state.current_run
        if run is None:
            payload.setdefault("players_count", 0)
            payload["run"] = None
            return

        now_mono = self._monotonic()
        timer_running = bool(payload.get("timer_running"))
        riddle_payloads: dict[str, dict[str, Any]] = {}
        for key, timing in run.riddle_timings.items():
            status = timing.status()
            final_time = float(timing.solve_time_s or 0)
            if status in {"active", "reset"} and timing.segment_started_monotonic is not None:
                live_time = round(max(0.0, now_mono - timing.segment_started_monotonic), 3)
            else:
                live_time = round(max(0.0, final_time), 3)
            riddle_payloads[key] = {
                "riddle_key": timing.riddle_key,
                "solve_time_s": round(max(0.0, final_time), 3),
                "live_time_s": live_time,
                "display_time_s": live_time if status in {"active", "reset"} else round(max(0.0, final_time), 3),
                "hint_count": int(timing.hint_count or 0),
                "hints": timing.hints or "",
                "skipped": bool(timing.skipped),
                "not_solved": bool(timing.not_solved),
                "status": status,
                "solved": status == "solved",
                "final": status in {"solved", "skipped", "not_solved"},
                "active": status == "active",
                "reset_pending": bool(timing.reset_pending),
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
            "booking": copy.deepcopy(run.booking or {}),
            "booking_code": str((run.booking or {}).get("bookingCode") or (run.booking or {}).get("booking_code") or ""),
            "booking_email": str((run.booking or {}).get("customerEmail") or (run.booking or {}).get("customer_email") or ""),
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
        payload = {"ts": self._now_iso(), "msg": msg}
        if d:
            payload["d"] = d
        self._publish_json(config.TOPIC_GAME_MASTER_DEBUG, payload, retained=False)

    def _new_run_shell(self, players_count: int = 0, booking: dict[str, Any] | None = None) -> CurrentRun:
        now = self._utc_now()
        run = CurrentRun(
            run_id=f"run_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}",
            date=now.date().isoformat(),
            started_at=now.isoformat(timespec="seconds"),
            started_monotonic=self._monotonic(),
            players_count=max(0, int(players_count or 0)),
            booking=dict(booking or {}),
        )
        for node in config.RIDDLES:
            run.riddle_timings[node] = RiddleTiming(riddle_key=node)
        return run

    def _prepare_new_run(self) -> None:
        with self._lock:
            self.state.current_run = self._new_run_shell()
            self.state.completed_phase_events.clear()
            self.state.game_started_at = None
            self.state.last_riddle_solved_at = None
            self.state.completion_db_saved = False
            self.state.completion_json_saved = False
            self.state.recovery_restored = False
            self.state.recovery_checkpoint_saved_at = None

    def _start_run_timer(self, *, publish: bool = True) -> None:
        with self._lock:
            if self.state.current_run is None:
                self.state.current_run = self._new_run_shell()
            run = self.state.current_run
            now = self._utc_now()
            started_at = now.isoformat(timespec="seconds")
            run.date = now.date().isoformat()
            run.started_at = started_at
            run.started_monotonic = self._monotonic()
            run.ended_at = None
            run.duration_s = None
            run.events.clear()
            run.riddle_timings.clear()
            self.state.completed_phase_events.clear()
            self.state.game_started_at = started_at
            self.state.last_riddle_solved_at = None
            self.state.completion_db_saved = False
            self.state.completion_json_saved = False
            for node in config.RIDDLES:
                run.riddle_timings[node] = RiddleTiming(riddle_key=node)
            for node in PHASES.get(self.state.phase, PHASES[3]).active_riddles:
                if node in run.riddle_timings:
                    run.riddle_timings[node].first_started_monotonic = run.started_monotonic
                    run.riddle_timings[node].segment_started_monotonic = run.started_monotonic
        if publish:
            self.publish_game_state()

    def _finalize_current_run(self) -> None:
        self._retry_completed_persistence("phase_14")

    def _reuse_persisted_leaderboard_code(self, run: CurrentRun) -> None:
        if run.leaderboard_code:
            return
        try:
            games = self.db.list_games()
        except Exception:
            LOG.warning("Could not inspect persisted run before completion retry run_id=%s", run.run_id, exc_info=True)
            return
        for game in games:
            if str(game.get("id") or "") != run.run_id:
                continue
            code = str(game.get("leaderboard_code") or "").strip()
            if code:
                run.leaderboard_code = code
                LOG.warning("Reusing persisted leaderboard code for completion retry run_id=%s", run.run_id)
            return

    def _retry_completed_persistence(self, reason: str) -> bool:
        with self._checkpoint_io_lock:
            self._last_completion_retry_monotonic = self._monotonic()
            with self._lock:
                run = self.state.current_run
                if self.state.phase != 14 or run is None:
                    return False
                db_saved = bool(self.state.completion_db_saved)
                json_saved = bool(self.state.completion_json_saved)
                try:
                    self._reuse_persisted_leaderboard_code(run)
                    self.db.recalc_run(run)
                except Exception:
                    LOG.exception("Completed run recalculation failed run_id=%s reason=%s", run.run_id, reason)
                    return False

                if not db_saved:
                    if not self._checkpoint_now("completion_before_db"):
                        LOG.error("Completion retry stopped because pre-database checkpoint failed run_id=%s", run.run_id)
                        return False
                    try:
                        self.db.save_completed_run(run)
                    except Exception:
                        LOG.exception("Completed run database persistence failed run_id=%s reason=%s", run.run_id, reason)
                        return False
                    self.state.completion_db_saved = True
                    self.state.completion_json_saved = False
                    if not self._checkpoint_now("completion_db_saved"):
                        LOG.error("Database committed but completion checkpoint update failed run_id=%s", run.run_id)
                        return False

                output_path = self.runs_dir / f"{run.run_id}.json"
                if not json_saved or not output_path.is_file():
                    try:
                        json_directory_synced = self._write_run_json(run)
                    except Exception:
                        LOG.exception("Completed run JSON persistence failed run_id=%s reason=%s", run.run_id, reason)
                        self._checkpoint_now("completion_json_failed")
                        return False
                    if not json_directory_synced:
                        self.state.completion_json_saved = False
                        LOG.critical(
                            "Completed run JSON is visible but not durably confirmed; retaining active checkpoint run_id=%s",
                            run.run_id,
                        )
                        self._checkpoint_now("completion_json_not_durable")
                        return False

                self.state.completion_db_saved = True
                self.state.completion_json_saved = True
                removed = self._remove_completed_checkpoint()
            self.publish_game_state()
            return removed

    def _write_run_json(self, run: CurrentRun) -> bool:
        booking = self._bounded_booking(run.booking)
        payload = {
            "run_id": run.run_id,
            "date": run.date,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "duration_s": run.duration_s,
            "players_count": int(run.players_count or 0),
            "booking": copy.deepcopy(booking),
            "leaderboard_code": run.leaderboard_code,
            "hint_count": run.hint_count(),
            "riddle_timings": {
                node: {
                    "riddle_key": timing.riddle_key,
                    "solve_time_s": timing.solve_time_s,
                    "hint_count": timing.hint_count,
                    "hints": self._truncate_utf8(timing.hints or "", MAX_HINTS_BYTES, keep_tail=True),
                    "skipped": bool(timing.skipped),
                    "not_solved": bool(timing.not_solved),
                    "status": timing.status(),
                }
                for node, timing in run.riddle_timings.items()
            },
            "events": copy.deepcopy(self._bounded_event_history(run.events)),
        }
        directory_synced = _atomic_write_json(self.runs_dir / f"{run.run_id}.json", payload)
        if not directory_synced:
            with self._lock:
                self.state.recovery_durability_degraded = True
        return directory_synced

    @classmethod
    def _bounded_event_value(cls, value: Any, depth: int = 0) -> Any:
        return cls._bounded_json_value(
            value,
            max_string_bytes=MAX_EVENT_STRING_LENGTH,
            depth=depth,
        )

    @staticmethod
    def _event_json_bytes(value: Any) -> int:
        return GameMaster._json_bytes(value)

    @classmethod
    def _sanitize_event_record(cls, value: Any) -> dict[str, Any]:
        bounded = cls._bounded_event_value(value)
        if not isinstance(bounded, dict):
            bounded = {"event": "invalid_event", "payload_truncated": True}
        else:
            bounded["event"] = cls._truncate_utf8(bounded.get("event") or "event", MAX_EVENT_KEY_LENGTH)
        if cls._event_json_bytes(bounded) > MAX_EVENT_BYTES:
            bounded = {
                "ts": cls._truncate_utf8(bounded.get("ts") or "", 64),
                "event": cls._truncate_utf8(bounded.get("event") or "event", MAX_EVENT_KEY_LENGTH),
                "payload_truncated": True,
            }
        return bounded

    @classmethod
    def _bounded_event_history(
        cls,
        events: Any,
        *,
        max_bytes: int = MAX_EVENT_HISTORY_BYTES,
    ) -> list[dict[str, Any]]:
        if not isinstance(events, list):
            return []
        kept_newest_first: list[dict[str, Any]] = []
        aggregate_bytes = 2  # JSON list brackets.
        byte_limit = max(2, min(MAX_EVENT_HISTORY_BYTES, int(max_bytes)))
        for raw_event in reversed(events[-MAX_RUN_EVENTS:]):
            event = cls._sanitize_event_record(raw_event)
            event_bytes = cls._event_json_bytes(event)
            additional_bytes = event_bytes + (1 if kept_newest_first else 0)
            if aggregate_bytes + additional_bytes > byte_limit:
                break
            kept_newest_first.append(event)
            aggregate_bytes += additional_bytes
        kept_newest_first.reverse()
        return kept_newest_first

    def _record_event(self, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            bounded_payload = self._bounded_event_value(payload)
            record = self._sanitize_event_record({
                **bounded_payload,
                "ts": self._now_iso(),
                "event": self._truncate_utf8(event, MAX_EVENT_KEY_LENGTH),
            })
            run.events = self._bounded_event_history([*run.events, record])

    def _mark_activations(self, nodes: tuple[str, ...]) -> None:
        if not nodes:
            return
        with self._lock:
            run = self.state.current_run
            if run is None:
                return
            now = self._monotonic()
            current_phase = int(self.state.phase)
            for node in nodes:
                if node == "candles" and current_phase < 11:
                    continue
                timing = run.riddle_timings.get(node)
                if timing is None:
                    continue
                if timing.is_final():
                    continue
                if timing.first_started_monotonic is None:
                    timing.first_started_monotonic = now
                if timing.segment_started_monotonic is None:
                    timing.segment_started_monotonic = timing.first_started_monotonic

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

            now_mono = self._monotonic()
            segment_start = timing.segment_started_monotonic or timing.first_started_monotonic or run.started_monotonic
            if timing.first_started_monotonic is None:
                timing.first_started_monotonic = segment_start

            if not timing.is_final() or float(timing.solve_time_s or 0) <= 0:
                timing.solve_time_s = round(max(0.0, now_mono - segment_start), 3)
                if outcome == "solved" and timing.solve_time_s <= 0:
                    timing.solve_time_s = 0.001
            timing.segment_started_monotonic = None
            timing.reset_pending = False
            timing.skipped = outcome == "skipped"
            timing.not_solved = outcome == "not_solved"
            if timing.skipped and timing.not_solved:
                timing.not_solved = False
            self.state.last_riddle_solved_at = self._now_iso()

        self._record_event(outcome, {"node": node, "source": source})

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

    @_serialized_mutation
    def handle_game_event(self, payload: dict[str, Any]) -> None:
        node = self._canonical_riddle_name(str(payload.get("node", "")).strip())
        event = str(payload.get("event", "")).strip().lower()
        if not node or not event:
            raise ValueError("game/event requires node and event")
        self._record_event("game_event", payload)
        if event == "solved":
            self.handle_solve(node, source="node")

    @_serialized_mutation
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
        if cmd in {"set_booking", "booking"}:
            if isinstance(payload.get("booking"), dict):
                raw_booking = dict(payload["booking"])
            else:
                raw_booking = {
                    "bookingCode": payload.get("bookingCode", payload.get("booking_code", "")),
                    "customerEmail": payload.get("customerEmail", payload.get("customer_email", payload.get("bookingEmail", payload.get("booking_email", "")))),
                    "customerName": payload.get("customerName", payload.get("customer_name", "")),
                    "date": payload.get("date", ""),
                    "slot": payload.get("slot", ""),
                    "kind": payload.get("kind", "booking"),
                }
                if "players" in payload or "players_count" in payload:
                    raw_booking["players"] = payload.get("players", payload.get("players_count"))
            self.set_booking(raw_booking, expected_run_id=str(payload.get("expected_run_id") or "").strip())
            return
        if cmd == "set_players_count":
            players_count = payload.get("players_count", 0)
            try:
                count = int(players_count)
            except (TypeError, ValueError):
                raise ValueError("set_players_count requires integer players_count")
            self.set_players_count(count, expected_run_id=str(payload.get("expected_run_id") or "").strip())
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
        if cmd in {"reset_riddle", "reset_riddle_timing"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            self.reset_riddle(riddle)
            return
        if cmd in {"clear_riddle_outcome", "clear_outcome"}:
            riddle = str(payload.get("riddle", payload.get("node", ""))).strip()
            self.set_riddle_outcome(riddle, "clear")
            return
        if cmd == "list_games":
            self.publish_debug("GAMES", {"games": self.db.list_games()})
            return
        raise ValueError(f"Unknown command: {cmd}")

    @_serialized_mutation
    def set_players_count(self, players_count: int, *, expected_run_id: str = "") -> bool:
        cleaned_count = max(0, int(players_count or 0))
        if cleaned_count > 1000:
            raise ValueError("players_count cannot exceed 1000")
        rejected_run_id = ""
        with self._lock:
            current_run_id = self.state.current_run.run_id if self.state.current_run is not None else ""
            if expected_run_id and (current_run_id != expected_run_id or not 3 <= self.state.phase <= 13):
                rejected_run_id = current_run_id
            elif self.state.current_run is None:
                self.state.current_run = self._new_run_shell(cleaned_count)
            else:
                self.state.current_run.players_count = cleaned_count
        if rejected_run_id or (expected_run_id and not current_run_id):
            self.publish_debug("PLAYERS_COUNT_IGNORED_RUN_MISMATCH", {
                "expected_run_id": expected_run_id,
                "current_run_id": rejected_run_id,
                "phase": self.state.phase,
            })
            return False
        self._record_event("players_count_updated", {"players_count": cleaned_count})
        self._checkpoint_after_mutation("players_count_updated")
        self.publish_game_state()
        return True

    @_serialized_mutation
    def set_booking(self, booking: dict[str, Any], *, expected_run_id: str = "") -> bool:
        raw_booking = copy.deepcopy(dict(booking or {}))
        raw_players = raw_booking.get("players", raw_booking.get("players_count"))
        cleaned = self._bounded_booking(raw_booking)
        self._validate_json_data(cleaned, "booking")
        rejected_run_id = ""
        with self._lock:
            current_run_id = self.state.current_run.run_id if self.state.current_run is not None else ""
            if expected_run_id and (current_run_id != expected_run_id or not 3 <= self.state.phase <= 13):
                rejected_run_id = current_run_id
            else:
                existing_players = int(self.state.current_run.players_count or 0) if self.state.current_run is not None else 0
                if raw_players is None or raw_players == "":
                    players_count = existing_players
                else:
                    try:
                        players_count = max(0, int(raw_players))
                    except (TypeError, ValueError):
                        players_count = existing_players
                if players_count > 1000:
                    raise ValueError("players_count cannot exceed 1000")
                cleaned["players"] = players_count
                cleaned["players_count"] = players_count
                cleaned = self._bounded_booking(cleaned)
                if self.state.current_run is None:
                    self.state.current_run = self._new_run_shell(players_count, cleaned)
                else:
                    self.state.current_run.booking = cleaned
                    self.state.current_run.players_count = players_count
        if rejected_run_id or (expected_run_id and not current_run_id):
            self.publish_debug("BOOKING_IGNORED_RUN_MISMATCH", {
                "expected_run_id": expected_run_id,
                "current_run_id": rejected_run_id,
                "phase": self.state.phase,
            })
            return False
        self._record_event("booking_updated", {
            "booking_code": str(cleaned.get("bookingCode") or cleaned.get("booking_code") or ""),
            "booking_email": str(cleaned.get("customerEmail") or cleaned.get("customer_email") or ""),
            "players_count": players_count,
        })
        self._checkpoint_after_mutation("booking_updated")
        self.publish_game_state()
        return True

    @_serialized_mutation
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
            next_count = int(timing.hint_count or 0) + 1
            if next_count > 100000:
                raise ValueError("hint count exceeds the recoverable limit")
            next_hints = timing.hints
            if hint_text:
                next_hints = ((timing.hints + "\n---\n" + hint_text) if timing.hints else hint_text).strip()
                next_hints = self._truncate_utf8(next_hints, MAX_HINTS_BYTES, keep_tail=True)
            timing.hint_count = next_count
            timing.hints = next_hints
        self._record_event("hint_added", {"riddle": riddle, "hint_text": hint_text})
        self._checkpoint_after_mutation("hint_added")
        self.publish_game_state()

    @_serialized_mutation
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
            cleaned_count = max(0, int(count or 0))
            if cleaned_count > 100000:
                raise ValueError("hint count exceeds the recoverable limit")
            timing.hint_count = cleaned_count
        self._record_event("hint_count_set", {"riddle": riddle, "count": cleaned_count})
        self._checkpoint_after_mutation("hint_count_set")
        self.publish_game_state()

    @_serialized_mutation
    def set_riddle_time(self, riddle: str, solve_time_s: float) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if not riddle:
            raise ValueError("set_riddle_time requires riddle")
        raw_seconds = float(solve_time_s or 0)
        if not math.isfinite(raw_seconds) or raw_seconds > MAX_RECOVERABLE_ELAPSED_S:
            raise ValueError("riddle time exceeds the recoverable range")
        seconds = round(max(0.0, raw_seconds), 3)
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            current_status = timing.status()
            if current_status == "pending" and seconds > 0:
                raise ValueError(f"Cannot set a positive time for pending riddle: {riddle}")
            if current_status in {"active", "reset"} and not timing.is_final():
                adjusted_start = self._monotonic() - seconds
                timing.segment_started_monotonic = adjusted_start
                timing.first_started_monotonic = adjusted_start
                timing.solve_time_s = 0.0
            else:
                timing.solve_time_s = seconds
                timing.reset_pending = False
                if seconds > 0:
                    timing.first_started_monotonic = self._monotonic() - seconds
                if seconds <= 0 and not (timing.skipped or timing.not_solved):
                    spec = PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE])
                    if riddle not in spec.active_riddles:
                        timing.segment_started_monotonic = None
            run.duration_s = self.db._compute_effective_duration_s(run)
        self._record_event("riddle_time_set", {"riddle": riddle, "solve_time_s": seconds})
        self._checkpoint_after_mutation("riddle_time_set")
        self.publish_game_state()

    @_serialized_mutation
    def set_riddle_outcome(self, riddle: str, outcome: str, *, advance: bool = False) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if riddle == "sissi":
            raise ValueError("Sissi can only be completed through the normal solve command")
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
            with self._lock:
                run = self.state.current_run
                timing = run.riddle_timings.get(riddle) if run is not None else None
                is_current_active = riddle in PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE]).active_riddles
                can_advance = timing is not None and timing.status() in {"active", "reset"} and is_current_active
            if can_advance:
                self._complete_active_riddle(riddle, source=f"admin_{outcome}", outcome=outcome)
                return
            advance = False

        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            if outcome == "clear":
                # Clearing an outcome does not rewind phase progression or timing.
                timing.skipped = False
                timing.not_solved = False
                timing.reset_pending = False
                if float(timing.solve_time_s or 0) <= 0:
                    spec = PHASES.get(self.state.phase, PHASES[config.DEFAULT_PHASE])
                    if riddle in spec.active_riddles and timing.segment_started_monotonic is None:
                        now_mono = self._monotonic()
                        timing.first_started_monotonic = timing.first_started_monotonic or now_mono
                        timing.segment_started_monotonic = timing.first_started_monotonic
            elif outcome == "solved":
                timing.skipped = False
                timing.not_solved = False
                if float(timing.solve_time_s or 0) <= 0:
                    segment_start = timing.segment_started_monotonic or timing.first_started_monotonic or run.started_monotonic
                    timing.first_started_monotonic = timing.first_started_monotonic or segment_start
                    timing.solve_time_s = round(max(0.0, self._monotonic() - segment_start), 3)
                    if timing.solve_time_s <= 0:
                        timing.solve_time_s = 0.001
                timing.segment_started_monotonic = None
                timing.reset_pending = False
            else:
                if float(timing.solve_time_s or 0) <= 0:
                    segment_start = timing.segment_started_monotonic or timing.first_started_monotonic or run.started_monotonic
                    timing.first_started_monotonic = timing.first_started_monotonic or segment_start
                    timing.solve_time_s = round(max(0.0, self._monotonic() - segment_start), 3)
                timing.skipped = outcome == "skipped"
                timing.not_solved = outcome == "not_solved"
                timing.reset_pending = False
                if timing.skipped and timing.not_solved:
                    timing.not_solved = False
                timing.segment_started_monotonic = None
            run.duration_s = self.db._compute_effective_duration_s(run)
        self._record_event("riddle_outcome_set", {"riddle": riddle, "outcome": outcome, "advance": bool(advance)})
        self._checkpoint_after_mutation("riddle_outcome_set")
        self.publish_game_state()

    @_serialized_mutation
    def reset_riddle(self, riddle: str) -> None:
        riddle = self._canonical_riddle_name(riddle)
        if riddle not in RESETTABLE_RIDDLES:
            raise ValueError(f"Riddle cannot be reset safely: {riddle}")
        with self._lock:
            run = self.state.current_run
            if run is None:
                raise ValueError("No current run")
            timing = run.riddle_timings.get(riddle)
            if timing is None:
                raise ValueError(f"Unknown riddle: {riddle}")
            if timing.status() not in {"solved", "skipped", "not_solved", "reset"}:
                raise ValueError(f"Only a completed riddle can be reset: {riddle}")
            now_mono = self._monotonic()
            timing.solve_time_s = 0.0
            timing.skipped = False
            timing.not_solved = False
            timing.reset_pending = True
            timing.first_started_monotonic = now_mono
            timing.segment_started_monotonic = now_mono
            event_name = RIDDLE_SOLVE_EVENTS.get(riddle)
            if event_name:
                self.state.completed_phase_events.discard(event_name)
            run.duration_s = self.db._compute_effective_duration_s(run)
        self._record_event("riddle_reset", {"riddle": riddle, "phase_unchanged": True})
        self._checkpoint_after_mutation("riddle_reset")
        self.publish_game_state()

    @_serialized_mutation
    def handle_solve(self, riddle: str, source: str) -> None:
        self._complete_active_riddle(riddle, source=source, outcome="solved")

    def _complete_active_riddle(self, riddle: str, source: str, outcome: str) -> None:
        if riddle not in config.RIDDLES:
            raise ValueError(f"Unknown riddle node: {riddle}")
        if riddle == "sissi" and outcome != "solved":
            raise ValueError("Sissi can only be completed with the solved outcome")
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
                return
        self._checkpoint_after_mutation("riddle_completed")
        self.publish_game_state()

    def schedule_in(self, delay_s: float, kind: str, payload: dict[str, Any]) -> None:
        delay = float(delay_s)
        if not math.isfinite(delay) or not 0 <= delay <= MAX_RECOVERABLE_ELAPSED_S:
            raise ValueError("scheduled-action delay is outside the recoverable range")
        with self._lock:
            self.state.pending.append(ScheduledAction(
                due_monotonic=self._monotonic() + delay,
                kind=kind,
                payload=copy.deepcopy(payload),
                phase=int(self.state.phase),
                phase_generation=int(self.state.phase_generation),
            ))
        self._record_event("scheduled", {"kind": kind, "delay_s": delay, "payload": payload})

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_due_actions()
                if not self._shutting_down.is_set():
                    self._checkpoint_if_due()
            except Exception:
                LOG.exception("Scheduler iteration failed")
            self._stop.wait(config.SCHEDULER_TICK_MS / 1000.0)

    def _run_due_actions(self) -> None:
        with self._checkpoint_io_lock:
            if self._shutting_down.is_set():
                return
            if self._hardware_effects_blocked:
                LOG.critical("Scheduled actions blocked by a checkpoint durability fault")
                return
            now = self._monotonic()
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
        with self._checkpoint_io_lock:
            if self._shutting_down.is_set():
                return
            with self._lock:
                if (
                    action.phase != self.state.phase
                    or action.phase_generation != self.state.phase_generation
                ):
                    LOG.warning(
                        "Dropping stale scheduled action kind=%s action_phase=%s action_generation=%s current_phase=%s current_generation=%s",
                        action.kind,
                        action.phase,
                        action.phase_generation,
                        self.state.phase,
                        self.state.phase_generation,
                    )
                    return
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
        if self._hardware_effects_blocked:
            LOG.critical("Phase hardware blocked by a checkpoint durability fault phase=%s", phase)
            return
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

    @_serialized_mutation
    def _set_lighting_phase(self, lighting_phase: int) -> None:
        with self._lock:
            self.state.lighting_phase = int(lighting_phase)
            phase = self.state.phase
        self._record_event("lighting_phase_changed", {"phase": phase, "lighting_phase": int(lighting_phase)})
        self._checkpoint_after_mutation("lighting_phase_changed")
        self._apply_phase_stable_scene(phase, lighting_phase=int(lighting_phase), apply_maglocks=False)

    def _run_transition_action(self, action) -> None:
        if self._hardware_effects_blocked:
            LOG.critical("Transition action blocked by a checkpoint durability fault kind=%s", action.kind)
            return
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

    @_serialized_mutation
    def _enter_phase(self, new_phase: int, reason: str) -> None:
        if self._hardware_effects_blocked:
            raise RecoveryPersistenceError("phase changes are blocked by a latched checkpoint durability fault")
        if type(new_phase) is not int or new_phase not in PHASES:
            raise ValueError(f"Unknown phase: {new_phase}")
        spec = PHASES[new_phase]
        with self._lock:
            old_phase = self.state.phase
            run = self.state.current_run
            completion_pending = (
                old_phase == 14
                and run is not None
                and not (self.state.completion_db_saved and self.state.completion_json_saved)
            )
            if completion_pending and new_phase != 14:
                raise RecoveryPersistenceError("cannot leave phase 14 while completed-run persistence is pending")
            if new_phase == 14:
                sissi = run.riddle_timings.get("sissi") if run is not None else None
                if (
                    old_phase != 13
                    or reason != RIDDLE_SOLVE_EVENTS["sissi"]
                    or sissi is None
                    or sissi.status() != "solved"
                ):
                    raise ValueError("phase 14 is reachable only through a genuine Sissi solve")

        # Prepare replaces the previous run with a new durable checkpoint in one
        # atomic commit. Modes without checkpoints must abandon the old file first.
        if new_phase in {0, 1} and (run is not None or self.checkpoint_path.exists()):
            if not self._abandon_active_checkpoint(reason):
                raise RecoveryPersistenceError("active-run checkpoint could not be abandoned safely")

        with self._lock:
            self.state.last_phase = old_phase
            self.state.phase = new_phase
            self.state.lighting_phase = int(spec.lighting_phase_on_enter if spec.lighting_phase_on_enter is not None else new_phase)
            self.state.phase_generation += 1
            self.state.pending.clear()
            self.state.completed_phase_events.clear()
            self.state.completion_db_saved = False
            self.state.completion_json_saved = False
            if new_phase in {0, 1}:
                self.state.current_run = None
                self.state.game_started_at = None
                self.state.last_riddle_solved_at = None
                self.state.recovery_restored = False
                self.state.recovery_checkpoint_saved_at = None
            lighting_phase = self.state.lighting_phase

        prehandled_actions: set[int] = set()
        for index, action in enumerate(spec.on_enter):
            if action.kind == "new_game_init":
                self._prepare_new_run()
                prehandled_actions.add(index)
            elif action.kind == "timer_start":
                self._start_run_timer(publish=False)
                prehandled_actions.add(index)
            elif action.kind == "log_solve_time":
                self._mark_solved(action.payload["riddle"], source="phase")
                prehandled_actions.add(index)

        self._mark_activations(spec.active_riddles)
        self._record_event("phase_changed", {"from": old_phase, "to": new_phase, "reason": reason, "lighting_phase": lighting_phase})
        if 2 <= new_phase <= 14 and not self._checkpoint_now("phase_transition"):
            raise CheckpointPersistenceError(f"phase {new_phase} checkpoint failed")
        self._apply_phase_stable_scene(new_phase, lighting_phase=lighting_phase)

        for index, action in enumerate(spec.on_enter):
            if index in prehandled_actions:
                continue
            self._run_transition_action(action)

        self.publish_game_state()
