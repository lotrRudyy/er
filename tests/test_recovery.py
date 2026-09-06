from __future__ import annotations

import ast
import copy
import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GAME_MASTER_DIR = ROOT / "scripts" / "game_master"
sys.path.insert(0, str(GAME_MASTER_DIR))

try:
    import paho.mqtt.client  # noqa: F401
except ModuleNotFoundError:
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = object
    client_module.MQTTMessage = object
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_module.client = client_module
    paho_module = types.ModuleType("paho")
    paho_module.mqtt = mqtt_module
    sys.modules.update({
        "paho": paho_module,
        "paho.mqtt": mqtt_module,
        "paho.mqtt.client": client_module,
    })

import config
import game_master as game_master_module
from unittest import mock

from game_master import (
    CHECKPOINT_SIZE_MARGIN_BYTES,
    CHECKPOINT_VERSION,
    MAX_CHECKPOINT_BYTES,
    MAX_BOOKING_BYTES,
    MAX_EVENT_HISTORY_BYTES,
    MAX_HINTS_BYTES,
    MAX_RUN_EVENTS,
    CheckpointPersistenceError,
    GameMaster,
    RecoveryPersistenceError,
)
from models import RiddleTiming


class FakeClock:
    def __init__(self, monotonic: float, wall: datetime | None = None) -> None:
        self.value = float(monotonic)
        self.wall = wall or datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.value += seconds
        self.wall += timedelta(seconds=seconds)


class FakePublishResult:
    rc = 0


class FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any, bool]] = []
        self.subscriptions: list[tuple[str, int | None]] = []
        self.connected = False
        self.loop_running = False
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None

    def enable_logger(self, logger: logging.Logger) -> None:
        self.logger = logger

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def loop_start(self) -> None:
        self.loop_running = True

    def loop_stop(self) -> None:
        self.loop_running = False

    def subscribe(self, topic: str, qos: int | None = None) -> None:
        self.subscriptions.append((topic, qos))

    def publish(self, topic: str, body: str, qos: int = 0, retain: bool = False) -> FakePublishResult:
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            payload = body
        self.published.append((topic, payload, retain))
        return FakePublishResult()


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.save_calls = 0

    @staticmethod
    def _compute_effective_duration_s(run: Any) -> float:
        times = {
            key: float(run.riddle_timings[key].solve_time_s or 0)
            for key in config.RIDDLES
        }
        return round(max(0.0, (
            times["images"]
            + times["piano"]
            + times["prison"]
            + times["wheel"]
            + times["chains"]
            + max(times["tangram"], times["magnet"])
            + times["chess"]
            + times["knocking"]
            + times["candles"]
            + times["stars"]
            + times["sissi"]
        )), 3)

    def recalc_run(self, run: Any) -> None:
        run.duration_s = self._compute_effective_duration_s(run)
        started = datetime.fromisoformat(run.started_at)
        run.ended_at = (started + timedelta(seconds=run.duration_s)).isoformat(timespec="seconds")
        run.date = started.date().isoformat()

    def save_completed_run(self, run: Any) -> None:
        self.save_calls += 1
        existing = self.rows.get(run.run_id, {})
        run.leaderboard_code = run.leaderboard_code or existing.get("leaderboard_code") or "654321"
        self.recalc_run(run)
        self.rows[run.run_id] = {
            "id": run.run_id,
            "leaderboard_code": run.leaderboard_code,
            "duration_s": run.duration_s,
        }

    def list_games(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]


class RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.checkpoint = self.root / "active.json"
        self.runs_dir = self.root / "runs"
        self.db = FakeDatabase()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_game_master(
        self,
        clock: FakeClock,
        *,
        client: FakeMqttClient | None = None,
        db: FakeDatabase | None = None,
    ) -> GameMaster:
        return GameMaster(
            db=db or self.db,
            runs_dir=self.runs_dir,
            checkpoint_path=self.checkpoint,
            mqtt_client=client or FakeMqttClient(),
            monotonic_fn=clock.monotonic,
            utc_now_fn=clock.utc_now,
        )

    @staticmethod
    def live_riddle_seconds(gm: GameMaster, riddle: str) -> float:
        payload = gm.state.to_game_state_payload()
        gm._enrich_dashboard_state_payload_locked(payload)
        return float(payload["run"]["riddle_timings"][riddle]["live_time_s"])

    def progress_to_phase_13(self, gm: GameMaster, clock: FakeClock) -> None:
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        for riddle in (
            "images", "piano", "prison", "wheel", "chains", "tangram",
            "magnet", "chess", "knocking", "candles", "stars",
        ):
            clock.advance(1.0)
            gm.handle_solve(riddle, source="manual")
        self.assertEqual(gm.state.phase, 13)

    def test_phase_3_resume_pauses_outage_and_replays_only_stable_scene(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        run_id = gm.state.current_run.run_id
        gm._enter_phase(3, "admin_start")
        gm.set_booking({
            "kind": "booking",
            "id": "booking-7",
            "bookingCode": "BOOK-7",
            "customerEmail": "guest@example.invalid",
            "players": 4,
        }, expected_run_id=run_id)
        gm.add_hint("images", "Look closely")
        clock.advance(12.25)
        gm._checkpoint_if_due()

        checkpoint_text = self.checkpoint.read_text(encoding="utf-8")
        self.assertNotIn("monotonic", checkpoint_text.lower())
        before = json.loads(checkpoint_text)
        self.assertAlmostEqual(before["run"]["riddle_timings"]["images"]["segment_elapsed_s"], 12.25)

        restored_clock = FakeClock(9000.0, clock.wall + timedelta(hours=9))
        client = FakeMqttClient()
        restored = self.make_game_master(restored_clock, client=client)
        self.assertEqual(restored.state.phase, 3)
        self.assertEqual(restored.state.current_run.run_id, run_id)
        self.assertEqual(restored.state.current_run.players_count, 4)
        self.assertEqual(restored.state.current_run.booking["bookingCode"], "BOOK-7")
        self.assertEqual(restored.state.current_run.riddle_timings["images"].hint_count, 1)
        self.assertAlmostEqual(self.live_riddle_seconds(restored, "images"), 12.25)

        restored_clock.advance(2.5)
        self.assertAlmostEqual(self.live_riddle_seconds(restored, "images"), 14.75)
        started_at = restored.state.current_run.started_at
        event_count = len(restored.state.current_run.events)
        restored.start()
        self.assertEqual(restored.state.current_run.run_id, run_id)
        self.assertEqual(restored.state.current_run.started_at, started_at)
        self.assertEqual(len(restored.state.current_run.events), event_count)

        commands = [(topic, payload) for topic, payload, _retain in client.published]
        pulse_locks = {"images", "knocking", "slider"}
        self.assertFalse(any(
            topic == config.TOPIC_MAGLOCK_CMD
            and isinstance(payload, dict)
            and payload.get("cmd") == "open"
            and payload.get("lock") in pulse_locks
            for topic, payload in commands
        ))
        self.assertFalse(any(isinstance(payload, dict) and payload.get("cmd") == "fade_to" for _topic, payload in commands))
        self.assertTrue(any(
            topic == config.TOPIC_LIGHTING_CMD and payload == {"cmd": "set_phase", "phase": 3}
            for topic, payload in commands
        ))
        dashboard_states = [payload for topic, payload in commands if topic == config.TOPIC_DASHBOARD_STATE]
        self.assertTrue(dashboard_states[-1]["recovery"]["restored"])

        client.published.clear()
        restored._on_connect(client, None, None, 0, None)
        self.assertFalse(any(
            topic == config.TOPIC_MAGLOCK_CMD
            and isinstance(payload, dict)
            and payload.get("lock") in pulse_locks
            for topic, payload, _retain in client.published
        ))
        restored.stop()

    def test_phase_8_retains_gate_outcomes_hints_reset_and_elapsed(self) -> None:
        clock = FakeClock(200.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        run = gm.state.current_run
        run.players_count = 5
        run.booking = {"kind": "booking", "bookingCode": "PARALLEL", "players": 5}
        run.events.append({"ts": gm._now_iso(), "event": "operator_note", "value": "kept"})
        with gm._lock:
            gm.state.last_phase = 7
            gm.state.phase = 8
            gm.state.lighting_phase = 8
            gm.state.completed_phase_events = {"tangram_solved"}
            for index, riddle in enumerate(("images", "piano", "prison", "wheel", "chains"), start=1):
                timing = run.riddle_timings[riddle]
                timing.solve_time_s = float(index)
                timing.first_started_monotonic = clock.monotonic() - index
                timing.segment_started_monotonic = None
            prison = run.riddle_timings["prison"]
            prison.skipped = True
            prison.solve_time_s = 3.0
            prison.hint_count = 2
            prison.hints = "first\n---\nsecond"
            run.riddle_timings["wheel"].not_solved = True
            tangram = run.riddle_timings["tangram"]
            tangram.solve_time_s = 7.0
            tangram.first_started_monotonic = clock.monotonic() - 7.0
            magnet = run.riddle_timings["magnet"]
            magnet.reset_pending = True
            magnet.first_started_monotonic = clock.monotonic() - 6.0
            magnet.segment_started_monotonic = clock.monotonic() - 6.0
            magnet.hint_count = 3
        self.assertTrue(gm._checkpoint_now("phase_8_test"))

        restored_clock = FakeClock(5000.0, clock.wall + timedelta(days=2))
        client = FakeMqttClient()
        restored = self.make_game_master(restored_clock, client=client)
        restored_run = restored.state.current_run
        self.assertEqual(restored.state.phase, 8)
        self.assertEqual(restored.state.last_phase, 7)
        self.assertEqual(restored.state.completed_phase_events, {"tangram_solved"})
        self.assertEqual(restored_run.booking["bookingCode"], "PARALLEL")
        self.assertEqual(restored_run.events[-1]["event"], "operator_note")
        self.assertTrue(restored_run.riddle_timings["prison"].skipped)
        self.assertTrue(restored_run.riddle_timings["wheel"].not_solved)
        self.assertEqual(restored_run.riddle_timings["prison"].hint_count, 2)
        self.assertEqual(restored_run.riddle_timings["prison"].hints, "first\n---\nsecond")
        self.assertEqual(restored_run.riddle_timings["magnet"].status(), "reset")
        self.assertEqual(restored_run.riddle_timings["magnet"].hint_count, 3)
        self.assertAlmostEqual(self.live_riddle_seconds(restored, "magnet"), 6.0)

        restored._reconcile_stable_state("test")
        self.assertTrue(any(
            topic == config.TOPIC_LIGHTING_CMD and payload == {"cmd": "set_phase", "phase": 8}
            for topic, payload, _retain in client.published
        ))
        self.assertFalse(any(
            isinstance(payload, dict) and payload.get("cmd") in {"fade_to", "pulse_open"}
            for _topic, payload, _retain in client.published
        ))

        restored_clock.advance(2.0)
        restored.handle_solve("magnet", source="manual")
        self.assertEqual(restored.state.phase, 9)
        self.assertAlmostEqual(restored_run.riddle_timings["magnet"].solve_time_s, 8.0)
        self.assertEqual(restored.state.completed_phase_events, set())

    def test_transitional_lighting_checkpoint_normalizes_without_delayed_actions(self) -> None:
        clock = FakeClock(120.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        run = gm.state.current_run
        with gm._lock:
            image = run.riddle_timings["images"]
            image.solve_time_s = 2.0
            image.segment_started_monotonic = None
            piano = run.riddle_timings["piano"]
            piano.solve_time_s = 3.0
            piano.first_started_monotonic = clock.monotonic() - 3.0
            piano.segment_started_monotonic = None
            prison = run.riddle_timings["prison"]
            prison.first_started_monotonic = clock.monotonic()
            prison.segment_started_monotonic = clock.monotonic()
            gm.state.phase = 5
            gm.state.last_phase = 4
            gm.state.lighting_phase = 4
        gm._checkpoint_now("transitional_scene")

        client = FakeMqttClient()
        restored = self.make_game_master(FakeClock(900.0, clock.wall + timedelta(hours=1)), client=client)
        self.assertEqual(restored.state.phase, 5)
        self.assertEqual(restored.state.lighting_phase, 5)
        self.assertEqual(restored.state.pending, [])
        restored._reconcile_stable_state("test")
        self.assertTrue(any(
            topic == config.TOPIC_LIGHTING_CMD and payload == {"cmd": "set_phase", "phase": 5}
            for topic, payload, _retain in client.published
        ))
        self.assertFalse(any(
            isinstance(payload, dict) and payload.get("cmd") == "fade_to"
            for _topic, payload, _retain in client.published
        ))

    def test_malformed_checkpoint_is_quarantined_without_replacement(self) -> None:
        malformed = b'{"schema":"wrong","version":1}'
        self.checkpoint.write_bytes(malformed)
        clock = FakeClock(10.0)
        with self.assertLogs("game_master", level="ERROR") as captured:
            gm = self.make_game_master(clock)
        self.assertEqual(gm.state.phase, 0)
        self.assertFalse(self.checkpoint.exists())
        quarantined = list(self.root.glob("active.json.invalid.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), malformed)
        self.assertIn("quarantined", "\n".join(captured.output).lower())

    def test_previous_checkpoint_schema_version_is_explicitly_quarantined(self) -> None:
        self.assertEqual(CHECKPOINT_VERSION, 2)
        clock = FakeClock(10.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        payload = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        payload["version"] = 1
        self.checkpoint.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertLogs("game_master", level="ERROR") as captured:
            restored = self.make_game_master(FakeClock(20.0, clock.wall + timedelta(minutes=1)))
        self.assertEqual(restored.state.phase, 0)
        self.assertFalse(self.checkpoint.exists())
        self.assertEqual(len(list(self.root.glob("active.json.invalid.*"))), 1)
        self.assertIn("unsupported checkpoint version", "\n".join(captured.output).lower())

    def test_atomic_writer_separates_pre_and_post_replace_directory_sync_failures(self) -> None:
        path = self.root / "atomic.json"
        path.write_text('{"value":"old"}', encoding="utf-8")

        with mock.patch("game_master._fsync_parent", side_effect=OSError("preflight failed")):
            with self.assertRaisesRegex(OSError, "preflight failed"):
                game_master_module._atomic_write_json(path, {"value": "not-visible"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "old")

        with mock.patch("game_master._fsync_parent", side_effect=[None, OSError("final sync failed")]):
            with self.assertLogs("game_master", level="CRITICAL") as captured:
                directory_synced = game_master_module._atomic_write_json(path, {"value": "visible"})
        self.assertFalse(directory_synced)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "visible")
        self.assertIn("treating write as committed", "\n".join(captured.output))
        self.assertEqual(list(self.root.glob(".atomic.json.*.tmp")), [])

    def test_committed_checkpoint_keeps_memory_aligned_when_final_directory_sync_fails(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")

        with mock.patch("game_master._fsync_parent", side_effect=[None, OSError("final sync failed")]):
            with self.assertLogs("game_master", level="CRITICAL"):
                with self.assertRaises(CheckpointPersistenceError):
                    gm.set_players_count(7)

        visible = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(visible["run"]["players_count"], 7)
        self.assertEqual(gm.state.current_run.players_count, 7)
        self.assertTrue(gm.state.recovery_durability_degraded)

    def test_phase_transition_emits_no_hardware_after_final_directory_sync_failure(self) -> None:
        clock = FakeClock(100.0)
        client = FakeMqttClient()
        gm = self.make_game_master(clock, client=client)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        client.published.clear()

        with mock.patch("game_master._fsync_parent", side_effect=[None, OSError("final sync failed")]):
            with self.assertLogs("game_master", level="CRITICAL"):
                with self.assertRaises(CheckpointPersistenceError):
                    gm.handle_solve("images", source="manual")

        visible = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(visible["state"]["phase"], 4)
        self.assertEqual(gm.state.phase, 4)
        self.assertTrue(gm.state.recovery_durability_degraded)
        self.assertEqual(client.published, [])
        self.assertTrue(gm._checkpoint_now("durability_fault_latched"))
        with self.assertRaisesRegex(RecoveryPersistenceError, "latched checkpoint durability fault"):
            gm._enter_phase(5, "test_after_fault")
        self.assertEqual(gm.state.phase, 4)
        self.assertEqual(client.published, [])

    def test_startup_directory_barrier_failure_uses_safe_standby_without_restored_hardware(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")

        client = FakeMqttClient()
        with mock.patch("game_master._fsync_parent", side_effect=OSError("directory unavailable")):
            with self.assertLogs("game_master", level="CRITICAL"):
                faulted = self.make_game_master(FakeClock(500.0, clock.wall + timedelta(hours=1)), client=client)
        self.assertEqual(faulted.state.phase, config.DEFAULT_PHASE)
        self.assertTrue(faulted.state.recovery_durability_degraded)
        self.assertTrue(self.checkpoint.exists())

        with self.assertLogs("game_master", level="CRITICAL"):
            faulted.start()
        hardware_topics = {config.TOPIC_LIGHTING_CMD, config.TOPIC_MAGLOCK_CMD, "star_sky/cmd"}
        self.assertFalse(any(topic in hardware_topics for topic, _payload, _retain in client.published))
        dashboard_states = [payload for topic, payload, _retain in client.published if topic == config.TOPIC_DASHBOARD_STATE]
        self.assertTrue(dashboard_states[-1]["recovery"]["startup_fault"])
        faulted.stop()

    def test_failed_periodic_checkpoint_attempts_are_throttled_but_mutations_are_immediate(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")

        with mock.patch("game_master._atomic_write_json", side_effect=OSError("disk unavailable")) as writer:
            with self.assertLogs("game_master", level="ERROR"):
                clock.advance(gm._checkpoint_interval_s)
                gm._checkpoint_if_due()
                gm._checkpoint_if_due()
                self.assertEqual(writer.call_count, 1)

                clock.advance(gm._checkpoint_interval_s / 2)
                gm._checkpoint_if_due()
                self.assertEqual(writer.call_count, 1)

                clock.advance(gm._checkpoint_interval_s / 2)
                gm._checkpoint_if_due()
                self.assertEqual(writer.call_count, 2)

                with self.assertRaises(CheckpointPersistenceError):
                    gm.set_players_count(9)
                self.assertEqual(writer.call_count, 3)
        self.assertNotEqual(gm.state.current_run.players_count, 9)

    def test_graceful_stop_checkpoints_and_operator_modes_abandon(self) -> None:
        clock = FakeClock(50.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        first_run_id = gm.state.current_run.run_id
        gm._enter_phase(3, "admin_start")
        clock.advance(1.75)
        gm.stop()
        payload = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(payload["reason"], "graceful_stop")
        self.assertAlmostEqual(payload["run"]["riddle_timings"]["images"]["segment_elapsed_s"], 1.75)

        with self.assertRaisesRegex(RuntimeError, "shutting down"):
            gm._enter_phase(0, "admin_standby")

        active = self.make_game_master(FakeClock(500.0, clock.wall + timedelta(minutes=5)))
        active._enter_phase(0, "admin_standby")
        self.assertFalse(self.checkpoint.exists())
        self.assertIsNone(active.state.current_run)
        active._enter_phase(2, "admin_prepare")
        self.assertTrue(self.checkpoint.exists())
        self.assertNotEqual(active.state.current_run.run_id, first_run_id)
        prepared_run_id = active.state.current_run.run_id
        restored_prepare = self.make_game_master(FakeClock(700.0, clock.wall + timedelta(hours=1)))
        self.assertEqual(restored_prepare.state.phase, 2)
        self.assertEqual(restored_prepare.state.current_run.run_id, prepared_run_id)
        restored_prepare._reconcile_stable_state("test")
        self.assertEqual(restored_prepare.state.current_run.run_id, prepared_run_id)
        restored_prepare._enter_phase(1, "admin_maintenance")
        self.assertFalse(self.checkpoint.exists())
        self.assertIsNone(restored_prepare.state.current_run)

    def test_phase_14_reuses_db_code_retries_json_and_then_cleans_checkpoint(self) -> None:
        clock = FakeClock(300.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        run = gm.state.current_run
        with gm._lock:
            gm.state.last_phase = 12
            gm.state.phase = 13
            gm.state.lighting_phase = 13
            for riddle in config.RIDDLES[:-1]:
                run.riddle_timings[riddle] = RiddleTiming(
                    riddle_key=riddle,
                    solve_time_s=1.0,
                    first_started_monotonic=clock.monotonic() - 1.0,
                )
            sissi = run.riddle_timings["sissi"]
            sissi.first_started_monotonic = clock.monotonic()
            sissi.segment_started_monotonic = clock.monotonic()
        gm._checkpoint_now("phase_13_test")
        original_write = gm._write_run_json
        gm._write_run_json = lambda _run: (_ for _ in ()).throw(OSError("simulated JSON loss"))
        clock.advance(9.0)
        with self.assertLogs("game_master", level="ERROR"):
            gm.handle_solve("sissi", source="manual")
        gm._write_run_json = original_write

        self.assertEqual(gm.state.phase, 14)
        self.assertEqual(self.db.save_calls, 1)
        self.assertEqual(run.leaderboard_code, "654321")
        self.assertTrue(self.checkpoint.exists())
        self.assertFalse((self.runs_dir / f"{run.run_id}.json").exists())

        # Model the narrower crash window where SQLite committed before the
        # leaderboard code/status reached the checkpoint.
        stale = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertTrue(stale["state"]["completion"]["db_saved"])
        self.assertEqual(stale["run"]["leaderboard_code"], "654321")
        stale["run"]["leaderboard_code"] = None
        stale["state"]["completion"] = {"db_saved": False, "json_saved": False}
        self.checkpoint.write_text(json.dumps(stale), encoding="utf-8")

        restored_clock = FakeClock(8000.0, clock.wall + timedelta(hours=4))
        client = FakeMqttClient()
        restored = self.make_game_master(restored_clock, client=client)
        self.assertIsNone(restored.state.current_run.leaderboard_code)
        restored.start()
        self.assertEqual(restored.state.current_run.leaderboard_code, "654321")
        self.assertEqual(self.db.save_calls, 2)
        self.assertEqual(len(self.db.rows), 1)
        output = self.runs_dir / f"{run.run_id}.json"
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["leaderboard_code"], "654321")
        self.assertEqual(list(self.runs_dir.glob(".*.tmp")), [])
        self.assertFalse(self.checkpoint.exists())
        self.assertFalse(any("email" in topic.lower() for topic, _payload, _retain in client.published))
        restored.set_hint_count("images", 4)
        self.assertEqual(self.db.save_calls, 3)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["riddle_timings"]["images"]["hint_count"],
            4,
        )
        self.assertFalse(self.checkpoint.exists())
        restored.stop()
        self.assertFalse(self.checkpoint.exists())

    def test_non_durable_completed_json_keeps_completion_pending_and_checkpoint(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        self.progress_to_phase_13(gm, clock)
        gm._write_run_json = mock.Mock(return_value=False)

        with self.assertLogs("game_master", level="CRITICAL"):
            clock.advance(1.0)
            gm.handle_solve("sissi", source="manual")

        self.assertEqual(gm.state.phase, 14)
        self.assertTrue(gm.state.completion_db_saved)
        self.assertFalse(gm.state.completion_json_saved)
        self.assertTrue(self.checkpoint.exists())
        payload = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertTrue(payload["state"]["completion"]["db_saved"])
        self.assertFalse(payload["state"]["completion"]["json_saved"])
        gm._write_run_json.assert_called_once_with(gm.state.current_run)

    def test_failed_transition_rolls_back_and_emits_no_hardware_commands(self) -> None:
        clock = FakeClock(100.0)
        client = FakeMqttClient()
        gm = self.make_game_master(clock, client=client)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        checkpoint_before = self.checkpoint.read_bytes()
        run_id = gm.state.current_run.run_id
        generation = gm.state.phase_generation
        client.published.clear()

        with mock.patch("game_master._atomic_write_json", side_effect=OSError("simulated checkpoint failure")):
            with self.assertLogs("game_master", level="ERROR"):
                with self.assertRaises(CheckpointPersistenceError):
                    gm.handle_solve("images", source="manual")

        self.assertEqual(gm.state.phase, 3)
        self.assertEqual(gm.state.phase_generation, generation)
        self.assertEqual(gm.state.current_run.run_id, run_id)
        self.assertEqual(gm.state.current_run.riddle_timings["images"].status(), "active")
        self.assertEqual(gm.state.completed_phase_events, set())
        self.assertEqual(self.checkpoint.read_bytes(), checkpoint_before)
        self.assertFalse(any(
            topic in {config.TOPIC_LIGHTING_CMD, config.TOPIC_MAGLOCK_CMD}
            for topic, _payload, _retained in client.published
        ))

    def test_failed_prepare_replacement_preserves_previous_run_checkpoint(self) -> None:
        clock = FakeClock(100.0)
        client = FakeMqttClient()
        gm = self.make_game_master(clock, client=client)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        old_run_id = gm.state.current_run.run_id
        checkpoint_before = self.checkpoint.read_bytes()
        client.published.clear()

        with mock.patch("game_master._atomic_write_json", side_effect=OSError("simulated checkpoint failure")):
            with self.assertLogs("game_master", level="ERROR"):
                with self.assertRaises(CheckpointPersistenceError):
                    gm._enter_phase(2, "admin_prepare")

        self.assertEqual(gm.state.phase, 3)
        self.assertEqual(gm.state.current_run.run_id, old_run_id)
        self.assertEqual(self.checkpoint.read_bytes(), checkpoint_before)
        self.assertFalse(any(
            topic in {config.TOPIC_LIGHTING_CMD, config.TOPIC_MAGLOCK_CMD}
            for topic, _payload, _retained in client.published
        ))

    def test_failed_checkpoint_abandonment_preserves_active_run(self) -> None:
        clock = FakeClock(100.0)
        client = FakeMqttClient()
        gm = self.make_game_master(clock, client=client)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        run_id = gm.state.current_run.run_id
        client.published.clear()

        with mock.patch("game_master._fsync_parent", side_effect=OSError("simulated directory sync failure")):
            with self.assertLogs("game_master", level="ERROR"):
                with self.assertRaises(RecoveryPersistenceError):
                    gm._enter_phase(0, "admin_standby")

        self.assertEqual(gm.state.phase, 3)
        self.assertEqual(gm.state.current_run.run_id, run_id)
        self.assertTrue(self.checkpoint.exists())
        self.assertEqual(client.published, [])

    def test_semantically_impossible_checkpoint_is_quarantined(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        impossible = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        impossible_piano = impossible["run"]["riddle_timings"]["piano"]
        impossible_piano.update({
            "status": "active",
            "first_elapsed_s": 1.0,
            "segment_elapsed_s": 1.0,
        })
        self.checkpoint.write_text(json.dumps(impossible), encoding="utf-8")

        with self.assertLogs("game_master", level="ERROR"):
            restored = self.make_game_master(FakeClock(500.0, clock.wall + timedelta(hours=1)))
        self.assertEqual(restored.state.phase, 0)
        self.assertFalse(self.checkpoint.exists())
        self.assertEqual(len(list(self.root.glob("active.json.invalid.*"))), 1)

    def test_scheduled_action_is_phase_bound_and_dropped_after_transition(self) -> None:
        clock = FakeClock(100.0)
        client = FakeMqttClient()
        gm = self.make_game_master(clock, client=client)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        gm.schedule_in(0, "lighting_batch", {
            "commands": [{"kind": "lighting_turn_on", "payload": {"light": "r3_uv"}}],
        })
        action = gm.state.pending[0]
        original_generation = action.phase_generation

        clock.advance(1.0)
        gm.handle_solve("images", source="manual")
        self.assertEqual(gm.state.phase, 4)
        self.assertGreater(gm.state.phase_generation, original_generation)
        self.assertEqual(gm.state.pending, [])

        client.published.clear()
        gm._execute_action(action)
        self.assertFalse(any(topic == config.TOPIC_LIGHTING_CMD for topic, _payload, _retain in client.published))

    def test_historical_reset_restarts_elapsed_time_at_reset(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        for riddle in ("images", "piano", "prison"):
            clock.advance(2.0)
            gm.handle_solve(riddle, source="manual")
        self.assertEqual(gm.state.phase, 6)

        clock.advance(120.0)
        gm.reset_riddle("prison")
        self.assertAlmostEqual(self.live_riddle_seconds(gm, "prison"), 0.0)
        clock.advance(3.25)
        self.assertAlmostEqual(self.live_riddle_seconds(gm, "prison"), 3.25)

    def test_terminal_phase_requires_normal_sissi_solve_and_pending_completion_cannot_be_abandoned(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        self.progress_to_phase_13(gm, clock)

        with self.assertRaisesRegex(ValueError, "genuine Sissi solve"):
            gm._enter_phase(14, "admin_set_phase")
        for outcome in ("solved", "skipped", "not_solved", "clear"):
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(ValueError, "normal solve"):
                    gm.set_riddle_outcome("sissi", outcome)
        self.assertEqual(gm.state.phase, 13)

        original_write = gm._write_run_json
        gm._write_run_json = lambda _run: (_ for _ in ()).throw(OSError("simulated JSON failure"))
        clock.advance(1.0)
        with self.assertLogs("game_master", level="ERROR"):
            gm.handle_solve("sissi", source="manual")
        gm._write_run_json = original_write
        self.assertEqual(gm.state.phase, 14)
        self.assertTrue(self.checkpoint.exists())
        with self.assertRaisesRegex(RecoveryPersistenceError, "persistence is pending"):
            gm._enter_phase(0, "admin_standby")
        self.assertEqual(gm.state.phase, 14)
        self.assertTrue(self.checkpoint.exists())

    def test_events_are_bounded_and_oversized_payloads_are_sanitized(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        for index in range(MAX_RUN_EVENTS + 5):
            gm._record_event("bounded", {"index": index})
        gm._record_event("large", {"text": "x" * 100000})

        events = gm.state.current_run.events
        self.assertEqual(len(events), MAX_RUN_EVENTS)
        self.assertLessEqual(len(events[-1].get("text", "")), 4096)
        self.assertTrue(gm._checkpoint_now("bounded_events"))
        payload = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["run"]["events"]), MAX_RUN_EVENTS)

    def test_two_thousand_maximal_events_stay_within_checkpoint_and_loader_budgets(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        maximal_chunks = ["x" * 4096 for _ in range(15)]
        for index in range(2000):
            gm._record_event("stress", {"index": index, "chunks": maximal_chunks})

        self.assertTrue(gm._checkpoint_now("maximal_event_stress"))
        checkpoint_bytes = self.checkpoint.read_bytes()
        payload = json.loads(checkpoint_bytes)
        events = payload["run"]["events"]
        event_bytes = len(json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(event_bytes, MAX_EVENT_HISTORY_BYTES)
        self.assertLess(len(checkpoint_bytes), MAX_CHECKPOINT_BYTES)
        self.assertLess(len(events), 2000)
        self.assertEqual(events[-1]["index"], 1999)

        restored = self.make_game_master(FakeClock(500.0, clock.wall + timedelta(hours=1)))
        self.assertEqual(restored.state.phase, 3)
        self.assertEqual(restored.state.current_run.events, events)

    def test_multibyte_hints_booking_and_events_fit_together_during_phase_progression(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        multibyte_hint = "😀" * (MAX_HINTS_BYTES // 4 + 100)
        multibyte_field = "界" * (MAX_BOOKING_BYTES // 3)
        maximal_event_text = "😀" * 5000
        self.assertTrue(gm.set_booking({
            "id": multibyte_field,
            "kind": multibyte_field,
            "bookingCode": multibyte_field,
            "date": multibyte_field,
            "slot": multibyte_field,
            "customerEmail": multibyte_field,
            "customerName": multibyte_field,
            "language": multibyte_field,
            "bookingStatus": multibyte_field,
            "paymentStatus": multibyte_field,
            "label": multibyte_field,
            "players": 4,
            "extra": {"notes": multibyte_field},
        }))
        with gm._lock:
            run = gm.state.current_run
            for timing in run.riddle_timings.values():
                timing.hints = multibyte_hint
            run.events = [
                {"event": "maximal", "index": index, "text": maximal_event_text}
                for index in range(2000)
            ]

        gm.handle_solve("images", source="manual")
        self.assertEqual(gm.state.phase, 4)
        checkpoint_bytes = self.checkpoint.read_bytes()
        self.assertLessEqual(
            len(checkpoint_bytes),
            MAX_CHECKPOINT_BYTES - CHECKPOINT_SIZE_MARGIN_BYTES,
        )
        payload = json.loads(checkpoint_bytes)
        for timing in payload["run"]["riddle_timings"].values():
            self.assertLessEqual(len(timing["hints"].encode("utf-8")), MAX_HINTS_BYTES)
        self.assertLessEqual(
            len(json.dumps(payload["run"]["booking"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            MAX_BOOKING_BYTES,
        )
        self.assertLessEqual(
            len(json.dumps(payload["run"]["events"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            MAX_EVENT_HISTORY_BYTES,
        )

        restored = self.make_game_master(FakeClock(500.0, clock.wall + timedelta(hours=1)))
        self.assertEqual(restored.state.phase, 4)

    def test_shutdown_waits_for_inflight_mutation_before_final_checkpoint(self) -> None:
        clock = FakeClock(100.0)
        gm = self.make_game_master(clock)
        gm._enter_phase(2, "admin_prepare")
        gm._enter_phase(3, "admin_start")
        entered_checkpoint = threading.Event()
        release_checkpoint = threading.Event()
        original_checkpoint = gm._checkpoint_now

        def blocking_checkpoint(reason: str) -> bool:
            if reason == "players_count_updated":
                entered_checkpoint.set()
                if not release_checkpoint.wait(2.0):
                    raise AssertionError("test checkpoint release timed out")
            return original_checkpoint(reason)

        gm._checkpoint_now = blocking_checkpoint
        errors: list[BaseException] = []

        def set_players() -> None:
            try:
                message = types.SimpleNamespace(
                    topic=config.TOPIC_GAME_CMD,
                    payload=json.dumps({"cmd": "set_players_count", "players_count": 7}).encode("utf-8"),
                )
                gm._on_message(gm._client, None, message)
            except BaseException as exc:
                errors.append(exc)

        mutation = threading.Thread(target=set_players)
        mutation.start()
        self.assertTrue(entered_checkpoint.wait(1.0))
        stopped = threading.Event()

        def stop_game_master() -> None:
            try:
                gm.stop()
            except BaseException as exc:
                errors.append(exc)
            finally:
                stopped.set()

        stopper = threading.Thread(target=stop_game_master)
        stopper.start()
        self.assertFalse(stopped.wait(0.05))
        release_checkpoint.set()
        mutation.join(2.0)
        stopper.join(2.0)

        self.assertFalse(mutation.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertEqual(errors, [])
        payload = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(payload["reason"], "graceful_stop")
        self.assertEqual(payload["run"]["players_count"], 7)
        ignored = types.SimpleNamespace(
            topic=config.TOPIC_GAME_CMD,
            payload=json.dumps({"cmd": "set_players_count", "players_count": 9}).encode("utf-8"),
        )
        gm._on_message(gm._client, None, ignored)
        self.assertEqual(gm.state.current_run.players_count, 7)

    def test_completion_and_concurrent_edit_are_serialized_without_stale_json(self) -> None:
        class BlockingDatabase(FakeDatabase):
            def __init__(self) -> None:
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def save_completed_run(self, run: Any) -> None:
                if self.save_calls == 0:
                    self.entered.set()
                    if not self.release.wait(2.0):
                        raise AssertionError("test database release timed out")
                super().save_completed_run(run)

        clock = FakeClock(100.0)
        db = BlockingDatabase()
        gm = self.make_game_master(clock, db=db)
        self.progress_to_phase_13(gm, clock)
        errors: list[BaseException] = []

        def finish() -> None:
            try:
                clock.advance(1.0)
                gm.handle_solve("sissi", source="manual")
            except BaseException as exc:
                errors.append(exc)

        def edit() -> None:
            try:
                gm.set_hint_count("images", 4)
            except BaseException as exc:
                errors.append(exc)

        finish_thread = threading.Thread(target=finish)
        finish_thread.start()
        self.assertTrue(db.entered.wait(1.0))
        edit_thread = threading.Thread(target=edit)
        edit_thread.start()
        time.sleep(0.05)
        self.assertTrue(edit_thread.is_alive())
        db.release.set()
        finish_thread.join(2.0)
        edit_thread.join(2.0)

        self.assertEqual(errors, [])
        self.assertFalse(finish_thread.is_alive())
        self.assertFalse(edit_thread.is_alive())
        output = self.runs_dir / f"{gm.state.current_run.run_id}.json"
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["riddle_timings"]["images"]["hint_count"], 4)
        self.assertFalse(self.checkpoint.exists())

    def test_default_recovery_paths_are_anchored_to_game_master_module(self) -> None:
        module_dir = Path(config.__file__).resolve().parent
        self.assertEqual(config.DATA_DIR, module_dir / "data")
        self.assertEqual(config.DB_PATH, module_dir / "data" / "game_master.sqlite3")
        self.assertEqual(config.RUNS_DIR, module_dir / "data" / "game_runs")
        self.assertEqual(config.ACTIVE_RUN_CHECKPOINT_PATH, module_dir / "data" / "active_run_checkpoint.json")


class DashboardAssignmentSourceTest(unittest.TestCase):
    """Exercise production functions without importing app.py's MQTT startup side effects."""

    @staticmethod
    def extracted_namespace(function_names: set[str]) -> dict[str, Any]:
        source_path = ROOT / "web" / "er1_dashboard" / "app.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names
        }
        missing = function_names - set(functions)
        if missing:
            raise AssertionError(f"dashboard functions missing: {sorted(missing)}")
        namespace: dict[str, Any] = {
            "Any": Any,
            "Path": Path,
            "json": json,
            "logging": logging,
            "LOG": logging.getLogger("dashboard_assignment_test"),
            "math": math,
            "os": os,
            "tempfile": tempfile,
            "datetime": datetime,
            "timezone": timezone,
            "uuid": uuid,
            "START_INTENT_STATES": {"none", "authorized", "published", "terminal"},
            "START_CANDIDATE_STATES": {"none", "selected", "published"},
            "_START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED": False,
        }
        order = (["_idle_start_assignment"] if "_idle_start_assignment" in function_names else []) + [
            name for name in function_names if name != "_idle_start_assignment"
        ]
        for name in order:
            function = copy.deepcopy(functions[name])
            function.decorator_list = []
            module = ast.Module(body=[function], type_ignores=[])
            exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
        return namespace

    @staticmethod
    def extracted_method_namespace(method_names: set[str]) -> dict[str, Any]:
        source_path = ROOT / "web" / "er1_dashboard" / "app.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        dashboard_store = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DashboardStore"
        )
        methods = {
            node.name: node
            for node in dashboard_store.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in method_names
        }
        missing = method_names - set(methods)
        if missing:
            raise AssertionError(f"dashboard methods missing: {sorted(missing)}")
        namespace: dict[str, Any] = {
            "Any": Any,
            "json": json,
            "LOG": logging.getLogger("dashboard_method_test"),
        }
        for name in method_names:
            method = copy.deepcopy(methods[name])
            method.decorator_list = []
            module = ast.Module(body=[method], type_ignores=[])
            exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
        return namespace

    def test_dashboard_claim_atomic_round_trip_is_minimal(self) -> None:
        names = {
            "_idle_start_assignment",
            "_fsync_parent_directory",
            "_ensure_dashboard_directory_durable",
            "_atomic_write_dashboard_json",
            "_minimal_start_assignment",
            "_terminal_start_assignment",
            "_quarantine_start_assignment_file",
            "_invalidate_unsynced_start_assignment_file",
            "load_start_assignment",
            "save_start_assignment",
        }
        namespace = self.extracted_namespace(names)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "claim.json"
            namespace.update({
                "START_ASSIGNMENT_PATH": path,
                "START_ASSIGNMENT_SCHEMA": "er1.dashboard.start_assignment",
                "START_ASSIGNMENT_VERSION": 2,
                "MAX_START_BOOKING_BYTES": 32768,
                "normalize_booking_selection": lambda booking: dict(booking),
            })
            claim = {
                "active": True,
                "cancel_requested": False,
                "intent_state": "authorized",
                "candidate_state": "none",
                "status": "waiting_for_run",
                "claim_id": "claim-1",
                "run_id": "run-1",
                "start_clicked_at_ms": 123456789,
                "state_revision": 7,
                "message": "waiting",
                "booking": {"customerEmail": "must-not-persist@example.invalid"},
            }
            namespace["save_start_assignment"](claim)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["assignment"]["run_id"], "run-1")
            self.assertEqual(raw["assignment"]["intent_state"], "authorized")
            self.assertFalse(raw["assignment"]["start_published"])
            self.assertNotIn("booking", raw["assignment"])
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])
            loaded = namespace["load_start_assignment"]()
            self.assertTrue(loaded["active"])
            self.assertTrue(loaded["_restored_from_disk"])
            self.assertTrue(loaded["_directory_barrier_confirmed"])

            booking_snapshot = {"id": "b-1", "kind": "booking", "customerEmail": "guest@example.invalid"}
            namespace["save_start_assignment"]({
                **claim,
                "active": False,
                "intent_state": "terminal",
                "candidate_state": "published",
                "status": "assigned",
                "booking_id": "b-1",
                "booking_snapshot": booking_snapshot,
                "start_published": True,
            })
            terminal = namespace["load_start_assignment"]()
            self.assertFalse(terminal["active"])
            self.assertEqual(terminal["status"], "assigned")
            self.assertTrue(terminal["start_published"])
            self.assertEqual(terminal["booking_snapshot"], booking_snapshot)
            self.assertNotIn("_restored_from_disk", terminal)

            namespace["_atomic_write_dashboard_json"] = mock.Mock(side_effect=OSError("disk unavailable"))
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                namespace["save_start_assignment"](claim)

    def test_dashboard_startup_barrier_failure_quarantines_intent(self) -> None:
        names = {
            "_idle_start_assignment",
            "_fsync_parent_directory",
            "_ensure_dashboard_directory_durable",
            "_atomic_write_dashboard_json",
            "_minimal_start_assignment",
            "_terminal_start_assignment",
            "_quarantine_start_assignment_file",
            "_invalidate_unsynced_start_assignment_file",
            "load_start_assignment",
            "save_start_assignment",
        }
        namespace = self.extracted_namespace(names)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "claim.json"
            namespace.update({
                "START_ASSIGNMENT_PATH": path,
                "START_ASSIGNMENT_SCHEMA": "er1.dashboard.start_assignment",
                "START_ASSIGNMENT_VERSION": 2,
                "MAX_START_BOOKING_BYTES": 32768,
                "normalize_booking_selection": lambda booking: dict(booking),
            })
            namespace["save_start_assignment"]({
                "active": True,
                "cancel_requested": False,
                "intent_state": "authorized",
                "candidate_state": "none",
                "status": "start_intent",
                "claim_id": "claim-1",
                "run_id": "run-1",
                "start_clicked_at_ms": 123456789,
            })

            namespace["_fsync_parent_directory"] = mock.Mock(side_effect=[OSError("barrier failed"), None])
            with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
                loaded = namespace["load_start_assignment"]()

            self.assertFalse(loaded["active"])
            self.assertEqual(loaded["intent_state"], "terminal")
            self.assertTrue(loaded["_load_durability_failed"])
            self.assertTrue(namespace["_START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED"])
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(temp_dir).glob("claim.json.invalid.*"))), 1)

    def test_dashboard_atomic_writer_keeps_visible_commit_on_final_sync_failure(self) -> None:
        names = {
            "_fsync_parent_directory",
            "_ensure_dashboard_directory_durable",
            "_atomic_write_dashboard_json",
        }
        namespace = self.extracted_namespace(names)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dashboard.json"
            path.write_text('{"value":"old"}', encoding="utf-8")

            namespace["_fsync_parent_directory"] = mock.Mock(side_effect=OSError("preflight failed"))
            with self.assertRaisesRegex(OSError, "preflight failed"):
                namespace["_atomic_write_dashboard_json"](path, {"value": "not-visible"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "old")

            namespace["_fsync_parent_directory"] = mock.Mock(side_effect=[None, OSError("final sync failed")])
            with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
                directory_synced = namespace["_atomic_write_dashboard_json"](path, {"value": "visible"})
            self.assertFalse(directory_synced)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "visible")
            self.assertEqual(list(Path(temp_dir).glob(".dashboard.json.*.tmp")), [])

    def test_dashboard_committed_claim_updates_memory_and_degraded_status(self) -> None:
        methods = self.extracted_method_namespace({"_set_start_assignment_locked"})
        methods["save_start_assignment"] = mock.Mock(return_value=False)
        store = types.SimpleNamespace(
            start_assignment={"active": False, "status": "idle"},
            persistence_degraded=False,
            last_start_assignment_directory_synced=True,
        )
        claim = {"active": True, "status": "publishing_start", "claim_id": "c1", "run_id": "r1"}
        self.assertFalse(methods["_set_start_assignment_locked"](store, claim))
        self.assertEqual(store.start_assignment, claim)
        self.assertTrue(store.persistence_degraded)
        self.assertFalse(store.last_start_assignment_directory_synced)

    def test_dashboard_resume_is_scoped_and_launched_once(self) -> None:
        namespace = self.extracted_namespace({
            "_resume_persisted_start_assignment",
            "_maybe_resume_persisted_start_assignment",
            "_publish_start_for_claim",
            "_ensure_active_start_assignment_worker",
        })

        class Store:
            def __init__(
                self,
                current_run_id: str,
                *,
                phase: int = 2,
                active: bool = True,
                applied_booking: dict[str, Any] | None = None,
            ) -> None:
                self.lock = threading.RLock()
                self.start_assignment = {
                    "active": active,
                    "cancel_requested": False,
                    "intent_state": "authorized",
                    "candidate_state": "none",
                    "start_published": False,
                    "status": "start_intent",
                    "claim_id": "claim-1",
                    "run_id": "run-1",
                    "start_clicked_at_ms": 123456789,
                    "_restored_from_disk": True,
                    "_directory_barrier_confirmed": True,
                }
                if applied_booking:
                    self.start_assignment.update({
                        "candidate_state": "published",
                        "booking_id": str(applied_booking["id"]),
                        "booking_snapshot": copy.deepcopy(applied_booking),
                    })
                self.game_state = {
                    "phase": phase,
                    "run": {
                        "run_id": current_run_id,
                        "booking": copy.deepcopy(applied_booking or {}),
                    },
                }

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                self.start_assignment = dict(value)
                return True

            def update_start_assignment(self, claim_id: str, **updates: Any) -> bool:
                if claim_id != self.start_assignment.get("claim_id"):
                    return False
                self.start_assignment.update(updates)
                return True

            def set_selected_booking(self, booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
                self.selected_booking = dict(booking)
                return self.selected_booking

        launches: list[tuple[str, bool]] = []
        publishes: list[tuple[str, dict[str, Any]]] = []
        launched_claims: set[str] = set()

        def launch(claim_id: str, resumed: bool = False) -> bool:
            if claim_id not in launched_claims:
                launched_claims.add(claim_id)
                launches.append((claim_id, resumed))
            return True

        namespace.update({
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda topic, payload: publishes.append((topic, payload)) or True,
            "_launch_start_assignment_worker": launch,
            "_booking_selection_key": lambda booking: tuple(sorted(booking.items())),
        })

        matching = Store("run-1", phase=2)
        namespace["store"] = matching
        namespace["_maybe_resume_persisted_start_assignment"]()
        namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(launches, [("claim-1", True)])
        self.assertEqual(publishes, [("game/cmd", {"cmd": "start"})])
        self.assertEqual(matching.start_assignment["intent_state"], "published")
        self.assertTrue(matching.start_assignment["start_published"])

        launches.clear()
        publishes.clear()
        launched_claims.clear()
        already_running = Store("run-1", phase=3)
        namespace["store"] = already_running
        namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(publishes, [])
        self.assertEqual(launches, [("claim-1", True)])
        self.assertEqual(already_running.start_assignment["intent_state"], "published")

        launches.clear()
        launched_claims.clear()
        mismatch = Store("run-other", phase=2)
        namespace["store"] = mismatch
        namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(launches, [])
        self.assertTrue(mismatch.start_assignment["active"])

        completed = Store("run-1", active=False)
        namespace["store"] = completed
        namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(launches, [])

        booking = {"id": "booking-7", "kind": "booking"}
        applied = Store("run-1", phase=3, applied_booking=booking)
        namespace["store"] = applied
        namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(launches, [])
        self.assertFalse(applied.start_assignment["active"])
        self.assertEqual(applied.start_assignment["status"], "assigned")
        self.assertEqual(applied.selected_booking["id"], "booking-7")

        launches.clear()
        publishes.clear()
        launched_claims.clear()
        unsafe = Store("run-1", phase=2)
        unsafe.start_assignment.pop("_directory_barrier_confirmed")
        namespace["store"] = unsafe
        with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
            namespace["_maybe_resume_persisted_start_assignment"]()
        self.assertEqual(launches, [])
        self.assertEqual(publishes, [])

    def test_dashboard_callback_and_resume_boundaries_suppress_production_path_exceptions(self) -> None:
        namespace = self.extracted_namespace({"_handle_mqtt_message", "on_message"})

        class FailingStore:
            @staticmethod
            def update_game_state(_payload: dict[str, Any]) -> None:
                raise OSError("state persistence failed")

        namespace.update({
            "store": FailingStore(),
            "parse_json_payload": lambda _payload: {"phase": 3},
            "TOPIC_DASHBOARD_STATE": "dashboard/state",
            "_maybe_resume_persisted_start_assignment": mock.Mock(),
        })
        message = types.SimpleNamespace(topic="dashboard/state", payload=b"{}")
        with self.assertLogs("dashboard_assignment_test", level="ERROR") as captured:
            namespace["on_message"](None, None, message)
        self.assertIn("network loop can continue", "\n".join(captured.output))

        resume = self.extracted_namespace({"_maybe_resume_persisted_start_assignment"})
        resume["_resume_persisted_start_assignment"] = mock.Mock(side_effect=OSError("claim read failed"))
        resume["_ensure_active_start_assignment_worker"] = mock.Mock()
        with self.assertLogs("dashboard_assignment_test", level="ERROR"):
            resume["_maybe_resume_persisted_start_assignment"]()

        source = (ROOT / "web" / "er1_dashboard" / "app.py").read_text(encoding="utf-8")
        self.assertIn("mqtt_client.suppress_exceptions = True", source)

    def test_dashboard_claim_failure_does_not_change_memory_or_publish_start(self) -> None:
        methods = self.extracted_method_namespace({"_set_start_assignment_locked"})
        previous = {"active": False, "status": "idle", "claim_id": "", "run_id": ""}
        fake_store = types.SimpleNamespace(start_assignment=dict(previous))
        methods["save_start_assignment"] = mock.Mock(side_effect=OSError("claim write failed"))
        claim = {"active": True, "status": "publishing_start", "claim_id": "c1", "run_id": "r1"}
        with self.assertRaisesRegex(OSError, "claim write failed"):
            methods["_set_start_assignment_locked"](fake_store, claim)
        self.assertEqual(fake_store.start_assignment, previous)

        namespace = self.extracted_namespace({"api_phase"})

        class Store:
            @staticmethod
            def claim_start_assignment(_reference_time: datetime) -> tuple[dict[str, Any], bool]:
                raise OSError("claim write failed")

            @staticmethod
            def get_start_assignment() -> dict[str, Any]:
                return dict(previous)

        publishes: list[tuple[str, Any]] = []
        namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"action": "start", "start_clicked_at_ms": 1}),
            "jsonify": lambda payload: payload,
            "store": Store(),
            "_parse_start_clicked_at_ms": lambda _value: datetime.now(timezone.utc),
            "mqtt_publish": lambda topic, payload: publishes.append((topic, payload)) or True,
            "TOPIC_GAME_CMD": "game/cmd",
        })
        with self.assertLogs("dashboard_assignment_test", level="ERROR"):
            response, status = namespace["api_phase"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertEqual(publishes, [])

    def test_unsynced_initial_start_intent_never_publishes_start(self) -> None:
        methods = self.extracted_method_namespace({"claim_start_assignment"})
        methods["uuid"] = types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="claim-unsafe"))
        invalidations: list[str] = []
        quarantines: list[str] = []
        methods.update({
            "_invalidate_unsynced_start_assignment_file": lambda: invalidations.append("invalidated"),
            "_quarantine_start_assignment_file": lambda reason: quarantines.append(reason) or True,
            "_terminal_start_assignment": lambda status, message, **ids: {
                "active": False,
                "cancel_requested": False,
                "intent_state": "terminal",
                "candidate_state": "none",
                "start_published": False,
                "status": status,
                "message": message,
                **ids,
            },
        })

        class ClaimStore:
            claim_start_assignment = methods["claim_start_assignment"]

            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": 2, "run": {"run_id": "run-1"}}
                self.game_state_revision = 1
                self.start_assignment = {"active": False}

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                self.start_assignment = dict(value)
                return False

        persisted_claim, created = ClaimStore().claim_start_assignment(datetime.now(timezone.utc))
        self.assertTrue(created)
        self.assertFalse(persisted_claim["start_published"])
        self.assertFalse(persisted_claim["_directory_synced"])
        self.assertFalse(persisted_claim["active"])
        self.assertEqual(persisted_claim["intent_state"], "terminal")
        self.assertEqual(invalidations, ["invalidated"])
        self.assertEqual(len(quarantines), 1)

        namespace = self.extracted_namespace({"api_phase"})

        class Store:
            def __init__(self) -> None:
                self.last_start_assignment_directory_synced = False
                self.start_assignment = {
                    "active": False,
                    "cancel_requested": False,
                    "intent_state": "terminal",
                    "start_published": False,
                    "status": "durability_failed",
                    "claim_id": "claim-unsafe",
                    "run_id": "run-1",
                }

            def claim_start_assignment(self, _reference_time: datetime) -> tuple[dict[str, Any], bool]:
                return {**self.start_assignment, "_directory_synced": False}, True

            def get_start_assignment(self) -> dict[str, Any]:
                return dict(self.start_assignment)

        publishes: list[tuple[str, Any]] = []
        namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"action": "start", "start_clicked_at_ms": 1}),
            "jsonify": lambda payload: payload,
            "store": Store(),
            "_parse_start_clicked_at_ms": lambda _value: datetime.now(timezone.utc),
            "mqtt_publish": lambda topic, payload: publishes.append((topic, payload)) or True,
            "TOPIC_GAME_CMD": "game/cmd",
        })
        with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
            response, status = namespace["api_phase"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertFalse(response["mqtt_queued"])
        self.assertEqual(publishes, [])
        self.assertFalse(response["start_assignment"]["start_published"])

    def test_start_worker_launch_survives_intermediate_status_persistence_failure(self) -> None:
        namespace = self.extracted_namespace({
            "api_phase",
            "_publish_start_for_claim",
            "_ensure_active_start_assignment_worker",
        })

        class Store:
            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": 2, "run": {"run_id": "run-1"}}
                self.start_assignment = {
                    "active": True,
                    "cancel_requested": False,
                    "claim_id": "claim-1",
                    "run_id": "run-1",
                    "intent_state": "authorized",
                    "candidate_state": "none",
                    "start_published": False,
                    "status": "start_intent",
                    "_intent_durable_runtime": True,
                }

            def claim_start_assignment(self, _reference_time: datetime) -> tuple[dict[str, Any], bool]:
                return {**self.start_assignment, "_directory_synced": True}, True

            @staticmethod
            def _set_start_assignment_locked(_value: dict[str, Any]) -> bool:
                raise OSError("intermediate write failed")

            def get_start_assignment(self) -> dict[str, Any]:
                return dict(self.start_assignment)

        publishes: list[tuple[str, Any]] = []
        launches: list[str] = []
        namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"action": "start", "start_clicked_at_ms": 1}),
            "jsonify": lambda payload: payload,
            "store": Store(),
            "_parse_start_clicked_at_ms": lambda _value: datetime.now(timezone.utc),
            "mqtt_publish": lambda topic, payload: publishes.append((topic, payload)) or True,
            "_launch_start_assignment_worker": lambda claim_id, resumed=False: launches.append(claim_id) or True,
            "TOPIC_GAME_CMD": "game/cmd",
        })
        with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
            response = namespace["api_phase"]()
        self.assertTrue(response["ok"])
        self.assertTrue(response["mqtt_queued"])
        self.assertTrue(response["assignment_worker_started"])
        self.assertEqual(publishes, [("game/cmd", {"cmd": "start"})])
        self.assertEqual(launches, ["claim-1"])
        self.assertFalse(response["start_assignment"]["start_published"])
        self.assertTrue(response["start_assignment"]["_start_published_runtime"])

    def test_start_assignment_worker_launcher_retains_and_deduplicates_worker(self) -> None:
        namespace = self.extracted_namespace({
            "_start_assignment_worker_entry",
            "_launch_start_assignment_worker",
        })
        created: list[Any] = []

        class FakeThread:
            def __init__(self, *, target: Any, args: tuple[Any, ...], name: str, daemon: bool) -> None:
                self.target = target
                self.args = args
                self.name = name
                self.daemon = daemon
                self.started = False
                created.append(self)

            def start(self) -> None:
                self.started = True

        workers: dict[str, Any] = {}
        namespace.update({
            "threading": types.SimpleNamespace(Thread=FakeThread),
            "_start_assignment_workers": workers,
            "_start_assignment_workers_lock": threading.Lock(),
            "_run_start_booking_assignment": mock.Mock(),
        })
        self.assertTrue(namespace["_launch_start_assignment_worker"]("claim-1"))
        self.assertTrue(namespace["_launch_start_assignment_worker"]("claim-1"))
        self.assertEqual(len(created), 1)
        self.assertIs(workers["claim-1"], created[0])
        self.assertTrue(created[0].started)

    def test_level_triggered_worker_ensure_retries_thread_start_and_then_deduplicates(self) -> None:
        namespace = self.extracted_namespace({
            "_start_assignment_worker_entry",
            "_launch_start_assignment_worker",
            "_ensure_active_start_assignment_worker",
        })
        created: list[Any] = []

        class FakeThread:
            def __init__(self, *, target: Any, args: tuple[Any, ...], name: str, daemon: bool) -> None:
                self.target = target
                self.args = args
                self.started = False
                created.append(self)

            def start(self) -> None:
                if len(created) == 1:
                    raise RuntimeError("temporary thread exhaustion")
                self.started = True

        store = types.SimpleNamespace(
            lock=threading.RLock(),
            start_assignment={
                "active": True,
                "cancel_requested": False,
                "intent_state": "published",
                "candidate_state": "none",
                "start_published": False,
                "claim_id": "claim-1",
                "run_id": "run-1",
                "_intent_durable_runtime": True,
            },
            game_state={"phase": 3, "run": {"run_id": "run-1"}},
        )
        workers: dict[str, Any] = {}
        namespace.update({
            "threading": types.SimpleNamespace(Thread=FakeThread),
            "store": store,
            "_start_assignment_workers": workers,
            "_start_assignment_workers_lock": threading.Lock(),
            "_run_start_booking_assignment": mock.Mock(),
        })

        with self.assertLogs("dashboard_assignment_test", level="CRITICAL"):
            self.assertFalse(namespace["_ensure_active_start_assignment_worker"]())
        self.assertEqual(workers, {})
        self.assertTrue(namespace["_ensure_active_start_assignment_worker"]())
        self.assertTrue(namespace["_ensure_active_start_assignment_worker"]())
        self.assertEqual(len(created), 2)
        self.assertIs(workers["claim-1"], created[1])
        self.assertTrue(created[1].started)

    def test_concurrent_progress_triggers_publish_start_and_launch_worker_once(self) -> None:
        namespace = self.extracted_namespace({
            "_publish_start_for_claim",
            "_ensure_active_start_assignment_worker",
        })

        class Store:
            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": 2, "run": {"run_id": "run-1"}}
                self.start_assignment = {
                    "active": True,
                    "cancel_requested": False,
                    "intent_state": "authorized",
                    "candidate_state": "none",
                    "start_published": False,
                    "claim_id": "claim-1",
                    "run_id": "run-1",
                    "_intent_durable_runtime": True,
                }

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                self.start_assignment = dict(value)
                return True

        store = Store()
        publish_entered = threading.Event()
        release_publish = threading.Event()
        publishes: list[dict[str, Any]] = []
        launches: list[str] = []
        launch_lock = threading.Lock()

        def publish(_topic: str, payload: dict[str, Any]) -> bool:
            publishes.append(dict(payload))
            publish_entered.set()
            self.assertTrue(release_publish.wait(1.0))
            return True

        def launch(claim_id: str, resumed: bool = False) -> bool:
            with launch_lock:
                if claim_id not in launches:
                    launches.append(claim_id)
            return True

        namespace.update({
            "store": store,
            "mqtt_publish": publish,
            "TOPIC_GAME_CMD": "game/cmd",
            "_launch_start_assignment_worker": launch,
        })
        threads = [threading.Thread(target=namespace["_ensure_active_start_assignment_worker"]) for _ in range(2)]
        threads[0].start()
        self.assertTrue(publish_entered.wait(1.0))
        threads[1].start()
        release_publish.set()
        for thread in threads:
            thread.join(2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(publishes, [{"cmd": "start"}])
        self.assertEqual(launches, ["claim-1"])

    def test_idempotent_start_api_ensures_existing_claim_worker(self) -> None:
        namespace = self.extracted_namespace({"api_phase"})
        claim = {
            "active": True,
            "cancel_requested": False,
            "intent_state": "published",
            "candidate_state": "none",
            "start_published": True,
            "claim_id": "claim-1",
            "run_id": "run-1",
            "_start_published_runtime": True,
        }

        class Store:
            @staticmethod
            def claim_start_assignment(_reference_time: datetime) -> tuple[dict[str, Any], bool]:
                return dict(claim), False

            @staticmethod
            def get_start_assignment() -> dict[str, Any]:
                return dict(claim)

        resume = mock.Mock()
        ensure = mock.Mock(return_value=True)
        publishes: list[tuple[str, Any]] = []
        namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"action": "start", "start_clicked_at_ms": 1}),
            "jsonify": lambda payload: payload,
            "store": Store(),
            "_parse_start_clicked_at_ms": lambda _value: datetime.now(timezone.utc),
            "_maybe_resume_persisted_start_assignment": resume,
            "_ensure_active_start_assignment_worker": ensure,
            "mqtt_publish": lambda topic, payload: publishes.append((topic, payload)) or True,
            "TOPIC_GAME_CMD": "game/cmd",
        })
        response = namespace["api_phase"]()
        self.assertTrue(response["ok"])
        self.assertTrue(response["idempotent"])
        self.assertTrue(response["mqtt_queued"])
        self.assertTrue(response["assignment_worker_started"])
        resume.assert_called_once_with()
        ensure.assert_called_once_with()
        self.assertEqual(publishes, [])

    def test_booking_claim_selection_reuses_snapshot_and_legacy_id_exactly(self) -> None:
        namespace = self.extracted_namespace({"_select_booking_for_start_claim"})
        nearest = mock.Mock(return_value={"id": "nearest", "kind": "booking"})
        namespace.update({
            "normalize_booking_selection": lambda booking: dict(booking),
            "_booking_is_cancelled": lambda _booking: False,
            "nearest_booking_for_start": nearest,
        })
        reference_time = datetime.now(timezone.utc)
        snapshot = {"id": "chosen", "kind": "booking", "customerEmail": "original@example.invalid"}
        changed_bookings = [
            {"id": "new-nearest", "kind": "booking"},
            {"id": "chosen", "kind": "booking", "customerEmail": "changed@example.invalid"},
        ]

        selected = namespace["_select_booking_for_start_claim"](
            {"booking_snapshot": snapshot, "booking_id": "chosen"},
            changed_bookings,
            reference_time,
        )
        self.assertEqual(selected, snapshot)
        nearest.assert_not_called()

        selected = namespace["_select_booking_for_start_claim"](
            {"booking_id": "chosen"},
            changed_bookings,
            reference_time,
        )
        self.assertEqual(selected["customerEmail"], "changed@example.invalid")
        nearest.assert_not_called()
        self.assertIsNone(namespace["_select_booking_for_start_claim"](
            {"booking_id": "missing"},
            changed_bookings,
            reference_time,
        ))
        nearest.assert_not_called()

    def test_start_assignment_worker_persists_snapshot_before_booking_publish(self) -> None:
        namespace = self.extracted_namespace({"_run_start_booking_assignment"})
        selected = {
            "id": "booking-7",
            "kind": "booking",
            "players": 4,
            "customerEmail": "guest@example.invalid",
        }
        writes: list[dict[str, Any]] = []
        publishes: list[dict[str, Any]] = []

        class Store:
            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.start_assignment = {
                    "active": True,
                    "cancel_requested": False,
                    "intent_state": "published",
                    "candidate_state": "none",
                    "start_published": True,
                    "status": "waiting_for_run",
                    "claim_id": "claim-1",
                    "run_id": "run-1",
                    "start_clicked_at_ms": 1000,
                    "state_revision": 1,
                    "_intent_durable_runtime": True,
                    "_start_published_runtime": True,
                }
                self.game_state_revision = 2
                self.game_state = {"phase": 3, "run": {"run_id": "run-1", "booking": {}}}
                self.selected_booking: dict[str, Any] = {}
                self.persistence_degraded = False

            def get_start_assignment(self) -> dict[str, Any]:
                return copy.deepcopy(self.start_assignment)

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                self.start_assignment = copy.deepcopy(value)
                writes.append(copy.deepcopy(value))
                return True

            def update_start_assignment(self, claim_id: str, **updates: Any) -> bool:
                if claim_id != self.start_assignment.get("claim_id"):
                    return False
                self._set_start_assignment_locked({**self.start_assignment, **updates})
                return True

            def set_selected_booking(self, booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
                self.selected_booking = copy.deepcopy(booking)
                return self.selected_booking

        store = Store()

        def publish_booking(booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
            self.assertEqual(store.start_assignment.get("booking_snapshot"), selected)
            self.assertEqual(store.start_assignment.get("candidate_state"), "selected")
            self.assertTrue(store.start_assignment.get("_candidate_directory_synced"))
            publishes.append(copy.deepcopy(booking))
            store.game_state["run"]["booking"] = copy.deepcopy(booking)
            return copy.deepcopy(booking)

        namespace.update({
            "store": store,
            "time": types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _seconds: None),
            "_rome_datetime_from_timestamp": lambda _timestamp: datetime.now(timezone.utc),
            "load_bookings_for_start_assignment": lambda _limit: [selected],
            "_select_booking_for_start_claim": lambda _claim, _bookings, _reference: copy.deepcopy(selected),
            "normalize_booking_selection": lambda booking: copy.deepcopy(booking),
            "publish_booking_selection": publish_booking,
            "_booking_selection_key": lambda booking: tuple(sorted(booking.items())),
        })
        namespace["_run_start_booking_assignment"]("claim-1")

        self.assertEqual(publishes, [selected])
        publishing_write = next(item for item in writes if item.get("status") == "candidate_selected")
        self.assertEqual(publishing_write["booking_snapshot"], selected)
        self.assertEqual(store.start_assignment["status"], "assigned")
        self.assertEqual(store.selected_booking, selected)

    def test_candidate_persistence_faults_publish_nothing_and_retry_exact_snapshot(self) -> None:
        namespace = self.extracted_namespace({"_run_start_booking_assignment"})
        selected = {
            "id": "booking-original",
            "kind": "booking",
            "players": 4,
            "customerEmail": "original@example.invalid",
        }
        replacement = {
            "id": "booking-new-nearest",
            "kind": "booking",
            "players": 2,
            "customerEmail": "replacement@example.invalid",
        }

        for failure_mode in ("false", "exception"):
            with self.subTest(failure_mode=failure_mode):
                class Store:
                    def __init__(self) -> None:
                        self.lock = threading.RLock()
                        self.start_assignment = {
                            "active": True,
                            "cancel_requested": False,
                            "intent_state": "published",
                            "candidate_state": "none",
                            "start_published": True,
                            "status": "waiting_for_run",
                            "claim_id": "claim-1",
                            "run_id": "run-1",
                            "start_clicked_at_ms": 1000,
                            "_intent_durable_runtime": True,
                            "_start_published_runtime": True,
                        }
                        self.game_state = {"phase": 3, "run": {"run_id": "run-1", "booking": {}}}
                        self.selected_booking: dict[str, Any] = {}
                        self.persistence_degraded = False
                        self.candidate_attempts = 0

                    def get_start_assignment(self) -> dict[str, Any]:
                        return copy.deepcopy(self.start_assignment)

                    def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                        if value.get("status") == "candidate_selected":
                            self.candidate_attempts += 1
                            if self.candidate_attempts == 1:
                                if failure_mode == "exception":
                                    raise OSError("candidate write failed")
                                self.start_assignment = copy.deepcopy(value)
                                self.persistence_degraded = True
                                return False
                        self.start_assignment = copy.deepcopy(value)
                        return True

                    def update_start_assignment(self, claim_id: str, **updates: Any) -> bool:
                        if claim_id != self.start_assignment.get("claim_id"):
                            return False
                        return self._set_start_assignment_locked({**self.start_assignment, **updates})

                    def set_selected_booking(self, booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
                        self.selected_booking = copy.deepcopy(booking)
                        return self.selected_booking

                store = Store()
                available = [copy.deepcopy(selected)]
                load_calls: list[list[dict[str, Any]]] = []
                publishes: list[dict[str, Any]] = []

                def load_bookings(_limit: int) -> list[dict[str, Any]]:
                    load_calls.append(copy.deepcopy(available))
                    return copy.deepcopy(available)

                def choose(claim: dict[str, Any], bookings: list[dict[str, Any]], _reference: datetime) -> dict[str, Any] | None:
                    snapshot = claim.get("booking_snapshot")
                    return copy.deepcopy(snapshot if isinstance(snapshot, dict) else (bookings[0] if bookings else None))

                def publish(booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
                    publishes.append(copy.deepcopy(booking))
                    store.game_state["run"]["booking"] = copy.deepcopy(booking)
                    return copy.deepcopy(booking)

                namespace.update({
                    "store": store,
                    "time": types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _seconds: None),
                    "_rome_datetime_from_timestamp": lambda _timestamp: datetime.now(timezone.utc),
                    "load_bookings_for_start_assignment": load_bookings,
                    "_select_booking_for_start_claim": choose,
                    "normalize_booking_selection": lambda booking: copy.deepcopy(booking),
                    "publish_booking_selection": publish,
                    "_booking_selection_key": lambda booking: tuple(sorted(booking.items())),
                })

                log_level = "ERROR" if failure_mode == "exception" else "CRITICAL"
                with self.assertLogs("dashboard_assignment_test", level=log_level):
                    namespace["_run_start_booking_assignment"]("claim-1")
                self.assertEqual(publishes, [])
                self.assertEqual(len(load_calls), 1)
                retained = store.start_assignment.get("booking_snapshot") or store.start_assignment.get("_candidate_retry_snapshot")
                self.assertEqual(retained, selected)
                self.assertTrue(store.start_assignment["active"])

                available[:] = [copy.deepcopy(replacement)]
                namespace["_run_start_booking_assignment"]("claim-1")
                self.assertEqual(len(load_calls), 1)
                self.assertEqual(publishes, [selected])
                self.assertEqual(store.selected_booking, selected)
                self.assertEqual(store.start_assignment["status"], "assigned")

    def test_worker_rechecks_manual_cancellation_after_fetch_and_candidate_persistence(self) -> None:
        namespace = self.extracted_namespace({"_run_start_booking_assignment"})
        selected = {"id": "booking-auto", "kind": "booking", "players": 4}

        for cancellation_point in ("fetch", "persistence"):
            with self.subTest(cancellation_point=cancellation_point):
                publishes: list[dict[str, Any]] = []

                class Store:
                    def __init__(self) -> None:
                        self.lock = threading.RLock()
                        self.start_assignment = {
                            "active": True,
                            "cancel_requested": False,
                            "intent_state": "published",
                            "candidate_state": "none",
                            "start_published": True,
                            "status": "waiting_for_run",
                            "claim_id": "claim-1",
                            "run_id": "run-1",
                            "start_clicked_at_ms": 1000,
                            "_intent_durable_runtime": True,
                        }
                        self.game_state = {"phase": 3, "run": {"run_id": "run-1", "booking": {}}}
                        self.persistence_degraded = False

                    def get_start_assignment(self) -> dict[str, Any]:
                        return copy.deepcopy(self.start_assignment)

                    def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                        self.start_assignment = copy.deepcopy(value)
                        if cancellation_point == "persistence" and value.get("status") == "candidate_selected":
                            self.start_assignment.update({
                                "active": False,
                                "cancel_requested": True,
                                "intent_state": "terminal",
                                "status": "manual",
                            })
                        return True

                    def update_start_assignment(self, claim_id: str, **updates: Any) -> bool:
                        if claim_id != self.start_assignment.get("claim_id"):
                            return False
                        return self._set_start_assignment_locked({**self.start_assignment, **updates})

                    @staticmethod
                    def set_selected_booking(booking: dict[str, Any], *, expected_run_id: str = "") -> dict[str, Any]:
                        return copy.deepcopy(booking)

                store = Store()

                def load_bookings(_limit: int) -> list[dict[str, Any]]:
                    if cancellation_point == "fetch":
                        store.start_assignment.update({
                            "active": False,
                            "cancel_requested": True,
                            "intent_state": "terminal",
                            "status": "manual",
                        })
                    return [copy.deepcopy(selected)]

                namespace.update({
                    "store": store,
                    "time": types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _seconds: None),
                    "_rome_datetime_from_timestamp": lambda _timestamp: datetime.now(timezone.utc),
                    "load_bookings_for_start_assignment": load_bookings,
                    "_select_booking_for_start_claim": lambda _claim, bookings, _reference: copy.deepcopy(bookings[0]),
                    "normalize_booking_selection": lambda booking: copy.deepcopy(booking),
                    "publish_booking_selection": lambda booking, **_kwargs: publishes.append(copy.deepcopy(booking)) or booking,
                    "_booking_selection_key": lambda booking: tuple(sorted(booking.items())),
                })

                namespace["_run_start_booking_assignment"]("claim-1")
                self.assertEqual(publishes, [])
                self.assertFalse(store.start_assignment["active"])
                self.assertTrue(store.start_assignment["cancel_requested"])

    def test_manual_override_durably_cancels_and_saves_before_mqtt(self) -> None:
        namespace = self.extracted_namespace({"publish_booking_selection"})
        booking = {"id": "booking-manual", "kind": "booking", "players": 3}

        for failure_mode in ("success", "cancel_false", "cancel_exception", "selection_failure"):
            with self.subTest(failure_mode=failure_mode):
                events: list[str] = []

                class Store:
                    def __init__(self) -> None:
                        self.lock = threading.RLock()
                        self.game_state = {"phase": 3, "run": {"run_id": "run-1"}}
                        self.selected_booking = {"id": "booking-old", "kind": "booking", "players": 2}
                        self.start_assignment = {
                            "active": True,
                            "cancel_requested": False,
                            "intent_state": "published",
                            "candidate_state": "none",
                            "claim_id": "claim-1",
                            "run_id": "run-1",
                        }

                    def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                        events.append("cancel")
                        if failure_mode == "cancel_exception":
                            raise OSError("cancel write failed")
                        self.start_assignment = copy.deepcopy(value)
                        return failure_mode != "cancel_false"

                    def set_selected_booking(
                        self,
                        selected: dict[str, Any],
                        *,
                        expected_run_id: str = "",
                        require_durable: bool = False,
                    ) -> dict[str, Any]:
                        self.assert_durable(require_durable)
                        events.append("selected")
                        if failure_mode == "selection_failure":
                            raise RuntimeError("selected write failed")
                        self.selected_booking = copy.deepcopy(selected)
                        return self.selected_booking

                    @staticmethod
                    def assert_durable(require_durable: bool) -> None:
                        if not require_durable:
                            raise AssertionError("manual selection did not require a durability barrier")

                store = Store()
                namespace.update({
                    "store": store,
                    "normalize_booking_selection": lambda selected: copy.deepcopy(selected),
                    "_booking_selection_identity": lambda selected: (selected.get("id"),),
                    "TOPIC_GAME_CMD": "game/cmd",
                    "mqtt_publish": lambda _topic, _payload: events.append("mqtt") or True,
                })

                if failure_mode == "success":
                    result = namespace["publish_booking_selection"](
                        booking,
                        expected_run_id="run-1",
                        supersede_automatic=True,
                    )
                    self.assertEqual(result, booking)
                    self.assertEqual(events, ["cancel", "selected", "mqtt"])
                    self.assertFalse(store.start_assignment["active"])
                    self.assertEqual(store.start_assignment["intent_state"], "terminal")
                    continue

                with self.assertRaises((RuntimeError, OSError)):
                    namespace["publish_booking_selection"](
                        booking,
                        expected_run_id="run-1",
                        supersede_automatic=True,
                    )
                self.assertNotIn("mqtt", events)
                if failure_mode.startswith("cancel"):
                    self.assertNotIn("selected", events)
                if failure_mode == "cancel_false":
                    self.assertTrue(store.start_assignment["cancellation_durability_pending"])

    def test_manual_override_retries_unsynced_cancellation_and_power_loss_keeps_it_terminal(self) -> None:
        names = {
            "_idle_start_assignment",
            "_fsync_parent_directory",
            "_ensure_dashboard_directory_durable",
            "_atomic_write_dashboard_json",
            "_minimal_start_assignment",
            "_terminal_start_assignment",
            "_quarantine_start_assignment_file",
            "_invalidate_unsynced_start_assignment_file",
            "load_start_assignment",
            "save_start_assignment",
            "publish_booking_selection",
            "_resume_persisted_start_assignment",
            "_publish_start_for_claim",
            "_ensure_active_start_assignment_worker",
            "_maybe_resume_persisted_start_assignment",
        }
        namespace = self.extracted_namespace(names)
        booking = {"id": "booking-manual", "kind": "booking", "players": 3}

        with tempfile.TemporaryDirectory() as temp_dir:
            assignment_path = Path(temp_dir) / "start-assignment.json"
            namespace.update({
                "START_ASSIGNMENT_PATH": assignment_path,
                "START_ASSIGNMENT_SCHEMA": "er1.dashboard.start_assignment",
                "START_ASSIGNMENT_VERSION": 2,
                "MAX_START_BOOKING_BYTES": 32768,
                "normalize_booking_selection": lambda selected: copy.deepcopy(selected),
                "_booking_selection_identity": lambda selected: (selected.get("id"),),
                "TOPIC_GAME_CMD": "game/cmd",
            })
            active_claim = {
                "active": True,
                "cancel_requested": False,
                "cancellation_durability_pending": False,
                "intent_state": "published",
                "candidate_state": "none",
                "start_published": True,
                "status": "waiting_for_run",
                "claim_id": "claim-1",
                "run_id": "run-1",
                "start_clicked_at_ms": 1000,
            }
            self.assertTrue(namespace["save_start_assignment"](active_claim))

            real_atomic_write = namespace["_atomic_write_dashboard_json"]
            cancellation_writes: list[dict[str, Any]] = []
            last_directory_synced_assignment = copy.deepcopy(active_claim)

            def controlled_atomic_write(path: Path, payload: dict[str, Any]) -> bool:
                directory_synced = real_atomic_write(path, payload)
                assignment = payload.get("assignment") if isinstance(payload.get("assignment"), dict) else {}
                if assignment.get("status") == "manual":
                    cancellation_writes.append(copy.deepcopy(assignment))
                    if len(cancellation_writes) == 1:
                        return False
                    last_directory_synced_assignment.clear()
                    last_directory_synced_assignment.update(copy.deepcopy(assignment))
                return directory_synced

            namespace["_atomic_write_dashboard_json"] = controlled_atomic_write
            events: list[str] = []

            class Store:
                def __init__(self) -> None:
                    self.lock = threading.RLock()
                    self.game_state = {"phase": 3, "run": {"run_id": "run-1"}}
                    self.selected_booking = {"id": "booking-old", "kind": "booking", "players": 2}
                    self.start_assignment = {**copy.deepcopy(active_claim), "_intent_durable_runtime": True}

                def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                    directory_synced = namespace["save_start_assignment"](value)
                    self.start_assignment = copy.deepcopy(value)
                    return directory_synced

                def set_selected_booking(
                    self,
                    selected: dict[str, Any],
                    *,
                    expected_run_id: str = "",
                    require_durable: bool = False,
                ) -> dict[str, Any]:
                    self.assert_durable(require_durable)
                    events.append("selected")
                    self.selected_booking = copy.deepcopy(selected)
                    return self.selected_booking

                @staticmethod
                def assert_durable(require_durable: bool) -> None:
                    if not require_durable:
                        raise AssertionError("manual selection did not require durability")

            store = Store()
            namespace.update({
                "store": store,
                "mqtt_publish": lambda _topic, _payload: events.append("mqtt") or True,
                "_launch_start_assignment_worker": lambda _claim_id, resumed=False: events.append("worker") or True,
            })

            with self.assertRaisesRegex(RuntimeError, "nicht dauerhaft beendet"):
                namespace["publish_booking_selection"](
                    booking,
                    expected_run_id="run-1",
                    supersede_automatic=True,
                )
            self.assertFalse(store.start_assignment["active"])
            self.assertTrue(store.start_assignment["cancellation_durability_pending"])
            self.assertEqual(len(cancellation_writes), 1)
            self.assertEqual(events, [])
            self.assertTrue(last_directory_synced_assignment["active"])
            visible_after_first = json.loads(assignment_path.read_text(encoding="utf-8"))["assignment"]
            self.assertFalse(visible_after_first["active"])
            self.assertTrue(visible_after_first["cancellation_durability_pending"])

            selected = namespace["publish_booking_selection"](
                booking,
                expected_run_id="run-1",
                supersede_automatic=True,
            )
            self.assertEqual(selected, booking)
            self.assertEqual(len(cancellation_writes), 2)
            self.assertEqual(events, ["selected", "mqtt"])
            self.assertFalse(store.start_assignment["cancellation_durability_pending"])

            durable = json.loads(assignment_path.read_text(encoding="utf-8"))["assignment"]
            self.assertFalse(durable["active"])
            self.assertTrue(durable["cancel_requested"])
            self.assertTrue(durable["cancellation_durability_pending"])
            self.assertEqual(durable["intent_state"], "terminal")

            # A power loss retains only the last assignment whose directory
            # barrier completed, not merely the last visible replacement.
            real_atomic_write(assignment_path, {
                "schema": "er1.dashboard.start_assignment",
                "version": 2,
                "assignment": copy.deepcopy(last_directory_synced_assignment),
            })
            restored = namespace["load_start_assignment"]()
            self.assertFalse(restored["active"])
            self.assertNotIn("_restored_from_disk", restored)
            self.assertFalse(restored["cancellation_durability_pending"])

            startup_events: list[str] = []
            namespace.update({
                "store": types.SimpleNamespace(
                    lock=threading.RLock(),
                    start_assignment=restored,
                    game_state={"phase": 3, "run": {"run_id": "run-1"}},
                ),
                "mqtt_publish": lambda _topic, _payload: startup_events.append("mqtt") or True,
                "_launch_start_assignment_worker": lambda _claim_id, resumed=False: startup_events.append("worker") or True,
            })
            namespace["_maybe_resume_persisted_start_assignment"]()
            self.assertEqual(startup_events, [])

    def test_selected_booking_write_is_atomic_and_errors_propagate(self) -> None:
        names = {
            "_fsync_parent_directory",
            "_ensure_dashboard_directory_durable",
            "_atomic_write_dashboard_json",
            "save_selected_booking",
        }
        namespace = self.extracted_namespace(names)
        namespace["normalize_booking_selection"] = lambda value: dict(value)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "selected.json"
            namespace["SELECTED_BOOKING_PATH"] = path
            namespace["save_selected_booking"]({"id": "booking-1", "players": 4})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["id"], "booking-1")
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

            namespace["_atomic_write_dashboard_json"] = mock.Mock(side_effect=OSError("selected write failed"))
            with self.assertRaisesRegex(OSError, "selected write failed"):
                namespace["save_selected_booking"]({"id": "booking-2"})

        methods = self.extracted_method_namespace({"set_selected_booking"})
        methods["normalize_booking_selection"] = lambda value: dict(value)
        methods["save_selected_booking"] = mock.Mock(side_effect=OSError("selected write failed"))

        class Store:
            set_selected_booking = methods["set_selected_booking"]

            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.selected_booking = {"id": "old", "players": 2}
                self.local_players_count_override = None
                self.persistence_degraded = False
                self.game_state = {
                    "phase": 3,
                    "run": {"run_id": "run-1", "booking": dict(self.selected_booking)},
                }

        store = Store()
        before = copy.deepcopy(store.game_state)
        with self.assertRaisesRegex(OSError, "selected write failed"):
            store.set_selected_booking({"id": "new", "players": 4}, expected_run_id="run-1")
        self.assertEqual(store.selected_booking["id"], "old")
        self.assertEqual(store.game_state, before)

        methods["save_selected_booking"] = mock.Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "nicht dauerhaft bestätigt"):
            store.set_selected_booking(
                {"id": "new", "players": 4},
                expected_run_id="run-1",
                require_durable=True,
            )
        self.assertEqual(store.selected_booking["id"], "new")
        self.assertEqual(store.game_state["run"]["booking"]["id"], "new")
        self.assertTrue(store.persistence_degraded)

    def test_dashboard_surfaces_persistence_degradation_without_disabling_controls(self) -> None:
        source = (ROOT / "web" / "er1_dashboard" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("state.meta?.persistence_degraded", source)
        self.assertIn("sectionChanged(data, 'meta')", source)
        self.assertIn("Speicherwarnung:", source)
        self.assertNotIn("startButton.disabled = Boolean(state.meta", source)

    def test_firmware_safe_state_preserves_restored_claim_and_rich_state_syncs_booking(self) -> None:
        namespace = self.extracted_method_namespace({"update_game_state"})
        saved_bookings: list[dict[str, Any]] = []

        def normalize_booking(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict) or not value:
                return {"id": "__empty__", "kind": "empty", "players": 0}
            return dict(value)

        namespace.update({
            "normalize_booking_selection": normalize_booking,
            "save_selected_booking": lambda booking: saved_bookings.append(dict(booking)),
            "parse_players_count_input": lambda value: int(value),
        })

        class Store:
            update_game_state = namespace["update_game_state"]

            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": 0}
                self.game_state_revision = 0
                self.local_players_count_override = None
                self.selected_booking = {"id": "old-booking", "kind": "booking", "players": 2}
                self.start_assignment = {
                    "active": True,
                    "cancel_requested": False,
                    "status": "waiting_for_run",
                    "claim_id": "claim-1",
                    "run_id": "run-1",
                    "_restored_from_disk": True,
                }

            def _reset_riddle_display_state_locked(self) -> None:
                pass

            def _reset_selected_booking_locked(self) -> None:
                self.selected_booking = normalize_booking({})

            def _reset_start_assignment_locked(self) -> None:
                self.start_assignment = {"active": False, "claim_id": "", "run_id": ""}

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> None:
                self.start_assignment = dict(value)

        store = Store()
        store.update_game_state({"phase": 2, "timer_running": False}, merge=True)
        self.assertTrue(store.start_assignment["active"])
        self.assertEqual(store.start_assignment["run_id"], "run-1")
        self.assertEqual(store.selected_booking["id"], "old-booking")

        store.update_game_state({
            "phase": 2,
            "timer_running": False,
            "run": {"run_id": "run-1", "booking": {}},
        })
        self.assertTrue(store.start_assignment["active"])
        self.assertEqual(store.selected_booking["id"], "__empty__")

        booking = {"id": "booking-7", "kind": "booking", "players": 4, "bookingCode": "B-7"}
        store.update_game_state({
            "phase": 3,
            "timer_running": True,
            "run": {"run_id": "run-1", "booking": booking},
        })
        self.assertEqual(store.selected_booking["id"], "booking-7")
        self.assertEqual(saved_bookings[-1]["bookingCode"], "B-7")


if __name__ == "__main__":
    unittest.main()
