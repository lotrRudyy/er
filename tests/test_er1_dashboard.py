from __future__ import annotations

import ast
import copy
import contextlib
import json
import math
import os
import re
import sqlite3
import subprocess
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

from riddle_time_correction import plan_riddle_time_change

APP_PATH = ROOT / "web" / "er1_dashboard" / "app.py"
JS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "app.js"
CSS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "style.css"
DASHBOARD_CSS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "dashboard.css"
HTML_PATH = ROOT / "web" / "er1_dashboard" / "templates" / "index.html"
GAME_VIEWER_JS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "game_viewer.js"
GAME_VIEWER_HTML_PATH = ROOT / "web" / "er1_dashboard" / "templates" / "game_viewer.html"
DEFAULT_HINTS_PATH = ROOT / "web" / "er1_dashboard" / "hint_templates.defaults.json"
ENV_EXAMPLE_PATH = ROOT / "web" / "er1_dashboard" / ".env.example"


def extracted(*names: str) -> dict[str, Any]:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    requested = set(names)
    namespace: dict[str, Any] = {
        "Any": Any,
        "Path": Path,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "json": json,
        "math": math,
        "os": os,
        "re": re,
        "threading": threading,
        "time": time,
        "unicodedata": __import__("unicodedata"),
    }
    found: set[str] = set()
    for original in tree.body:
        if not isinstance(original, (ast.FunctionDef, ast.AsyncFunctionDef)) or original.name not in requested:
            continue
        node = copy.deepcopy(original)
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(APP_PATH), "exec"), namespace)
        found.add(original.name)
    missing = requested - found
    if missing:
        raise AssertionError(f"dashboard definitions missing: {sorted(missing)}")
    return namespace


def extracted_method(class_name: str, method_name: str) -> tuple[Any, dict[str, Any]]:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = copy.deepcopy(next(
        node for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    ))
    method.decorator_list = []
    namespace: dict[str, Any] = {
        "Any": Any,
        "datetime": datetime,
        "math": math,
        "timezone": timezone,
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(APP_PATH), "exec"),
        namespace,
    )
    return namespace[method_name], namespace


def extracted_class(class_name: str) -> Any:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    class_node = copy.deepcopy(next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ))
    namespace: dict[str, Any] = {
        "Any": Any,
        "Path": Path,
        "closing": contextlib.closing,
        "os": os,
        "sqlite3": sqlite3,
        "time": time,
        "uuid": uuid,
        "_ensure_dashboard_directory_durable": lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[class_node], type_ignores=[])), str(APP_PATH), "exec"),
        namespace,
    )
    return namespace[class_name]


class JsonResponse(dict):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.headers: dict[str, str] = {}


def booking_namespace() -> dict[str, Any]:
    namespace = extracted(
        "_safe_int",
        "normalize_hint_language",
        "normalize_booking_selection",
        "_booking_is_cancelled",
        "_booking_is_eligible",
        "_last_sunday_of_month",
        "_rome_datetime_from_local",
        "_rome_datetime_from_timestamp",
        "_booking_start_in_rome",
        "nearest_booking_for_start",
    )
    namespace.update({
        "TEST_BOOKING_DEFAULT": {"customerEmail": "test@example.invalid"},
        "EUROPE_ROME_TZ": None,
        "AUTOMATIC_BOOKING_WINDOW_S": 30 * 60,
    })
    return namespace


class BookingRulesTests(unittest.TestCase):
    def test_empty_booking_never_becomes_synthetic_booking(self) -> None:
        normalize = booking_namespace()["normalize_booking_selection"]
        result = normalize({})
        self.assertEqual(result["id"], "__empty__")
        self.assertEqual(result["kind"], "empty")
        self.assertEqual(result["players"], 0)
        self.assertEqual(result["label"], "Keine Buchung ausgewählt")

    def test_automatic_window_is_strictly_inclusive_at_both_boundaries(self) -> None:
        namespace = booking_namespace()
        nearest = namespace["nearest_booking_for_start"]
        reference = datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))

        def booking(identifier: str, slot: str) -> dict[str, Any]:
            return {
                "id": identifier,
                "kind": "booking",
                "date": "2026-10-01",
                "slot": slot,
                "players": 2,
                "bookingStatus": "confirmed",
                "paymentStatus": "onsite_pending",
            }

        self.assertEqual(nearest([booking("minus", "11:30")], reference)["id"], "minus")
        self.assertEqual(nearest([booking("plus", "12:30")], reference)["id"], "plus")
        self.assertIsNone(nearest([booking("too-early", "11:29")], reference))
        self.assertIsNone(nearest([booking("too-late", "12:31")], reference))
        self.assertEqual(
            nearest([booking("far", "14:00"), booking("inside", "12:29")], reference)["id"],
            "inside",
        )

    def test_booking_choices_only_keep_confirmed_unfinished_slots(self) -> None:
        eligible = booking_namespace()["_booking_is_eligible"]
        reference = datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))

        def booking(**updates: Any) -> dict[str, Any]:
            value = {
                "id": "booking-1",
                "kind": "booking",
                "date": "2026-10-01",
                "slot": "11:30",
                "players": 2,
                "bookingStatus": "confirmed",
                "paymentStatus": "paid",
            }
            value.update(updates)
            return value

        self.assertTrue(eligible(booking(), reference))
        self.assertFalse(eligible(booking(slot="11:00"), reference))
        self.assertFalse(eligible(booking(bookingStatus="pending_approval"), reference))
        self.assertFalse(eligible(booking(paymentStatus="refunded"), reference))
        self.assertFalse(eligible(booking(completedAt="2026-10-01T11:59:00+02:00"), reference))
        self.assertFalse(eligible(booking(deletedAt="2026-10-01T11:59:00+02:00"), reference))
        self.assertFalse(eligible(booking(slot="invalid"), reference))
        self.assertFalse(eligible(booking(slot="invalid 12:30 text"), reference))
        self.assertFalse(eligible(booking(date="2026-10-01junk"), reference))

    def test_stale_cache_cannot_feed_automatic_match(self) -> None:
        namespace = extracted("load_bookings_for_start_assignment")
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "bookings.sqlite3"
            cache.write_bytes(b"cache")
            namespace.update({
                "list_bookings_via_ssh_copy": lambda _limit: (_ for _ in ()).throw(RuntimeError("ssh down")),
                "booking_ssh_config": lambda: {"local_db_path": cache},
                "booking_cache_max_age_s": lambda: 60,
                "list_bookings_from_local_db": lambda _path, _limit: [{"id": "cached"}],
            })

            os.utime(cache, (time.time() - 59, time.time() - 59))
            self.assertEqual(namespace["load_bookings_for_start_assignment"]()[0]["id"], "cached")

            os.utime(cache, (time.time() - 61, time.time() - 61))
            with self.assertRaisesRegex(RuntimeError, "älter als die zulässigen 60 Sekunden"):
                namespace["load_bookings_for_start_assignment"]()


class DashboardMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 10, "run": {"run_id": "run-10"}},
        )

    def test_hint_counts_use_the_atomic_dashboard_writer(self) -> None:
        namespace = extracted("save_hint_store")
        writes: list[tuple[Path, dict[str, int]]] = []
        path = Path("data/dashboard_hint_counts.json")
        namespace.update({
            "HINTS_PATH": path,
            "_atomic_write_dashboard_json": lambda target, payload: writes.append((target, payload)) or True,
        })

        namespace["save_hint_store"]({"images": 2, "piano": -1})

        self.assertEqual(writes, [(path, {"images": 2, "piano": 0})])

    def test_operation_ledger_replays_results_and_never_reclaims_uncertain_work(self) -> None:
        ledger_type = extracted_class("OperationLedger")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "operations.sqlite3"
            first = ledger_type(path, owner_id="process-a")
            operation_id = str(uuid.uuid4())

            self.assertEqual(first.claim(operation_id, "POST", "/api/hints", "hash-a")[0], "not_prepared")
            self.assertEqual(first.prepare(operation_id, "POST", "/api/hints", "hash-a")[0], "prepared")
            self.assertEqual(first.prepare(operation_id, "POST", "/api/hints", "hash-a")[0], "prepared")
            outcome, _record = first.claim(operation_id, "POST", "/api/hints", "hash-a")
            self.assertEqual(outcome, "claimed")
            self.assertEqual(first.claim(operation_id, "POST", "/api/hints", "hash-a")[0], "in_progress")
            first.complete(operation_id, 200, b'{"ok":true,"hint_count":1}', "application/json")

            restarted = ledger_type(path, owner_id="process-b")
            outcome, record = restarted.claim(operation_id, "POST", "/api/hints", "hash-a")
            self.assertEqual(outcome, "completed")
            self.assertEqual(record["http_status"], 200)
            self.assertEqual(bytes(record["response_body"]), b'{"ok":true,"hint_count":1}')
            self.assertEqual(restarted.claim(operation_id, "POST", "/api/hints", "different")[0], "conflict")

            interrupted_id = str(uuid.uuid4())
            first.prepare(interrupted_id, "POST", "/api/lock", "hash-b")
            self.assertEqual(first.claim(interrupted_id, "POST", "/api/lock", "hash-b")[0], "claimed")
            interrupted = restarted.get(interrupted_id)
            self.assertEqual(interrupted["state"], "uncertain")
            self.assertEqual(restarted.claim(interrupted_id, "POST", "/api/lock", "hash-b")[0], "uncertain")
            self.assertEqual(restarted.acknowledge(interrupted_id)["state"], "acknowledged")
            self.assertEqual(restarted.claim(interrupted_id, "POST", "/api/lock", "hash-b")[0], "acknowledged")

    def test_prepared_operation_survives_restart_and_only_one_concurrent_claim_wins(self) -> None:
        ledger_type = extracted_class("OperationLedger")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "operations.sqlite3"
            first = ledger_type(path, owner_id="process-a")
            operation_id = str(uuid.uuid4())
            first.prepare(operation_id, "POST", "/api/light", "hash-a")

            restarted = ledger_type(path, owner_id="process-b")
            barrier = threading.Barrier(3)
            outcomes: list[str] = []

            def claim() -> None:
                barrier.wait()
                outcomes.append(restarted.claim(operation_id, "POST", "/api/light", "hash-a")[0])

            threads = [threading.Thread(target=claim) for _index in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

            self.assertCountEqual(outcomes, ["claimed", "in_progress"])
            restarted.complete(operation_id, 200, b'{"ok":true}', "application/json")
            self.assertEqual(restarted.claim(operation_id, "POST", "/api/light", "hash-a")[0], "completed")

            stale_id = str(uuid.uuid4())
            restarted.prepare(stale_id, "POST", "/api/light", "hash-stale")
            with contextlib.closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE dashboard_operations SET updated_at = ? WHERE operation_id = ?",
                    (time.time() - restarted.PREPARED_RETENTION_S - 1, stale_id),
                )
            restarted.prepare(str(uuid.uuid4()), "POST", "/api/light", "hash-new")
            self.assertIsNone(restarted.get(stale_id))

    def test_stale_action_guard_requires_phase_and_run_identity(self) -> None:
        namespace = extracted("_action_guard_error")
        namespace["store"] = self.store
        guard = namespace["_action_guard_error"]
        self.assertIsNone(guard({"expected_phase": 10, "expected_run_id": "run-10"}))
        self.assertIn("aktualisieren", guard({"expected_phase": 9, "expected_run_id": "run-10"}))
        self.assertIn("aktualisieren", guard({"expected_phase": 10, "expected_run_id": "old-run"}))
        self.assertIn("aktualisieren", guard({}))

    def test_node_reboot_is_whitelisted_and_publishes_plain_reboot(self) -> None:
        namespace = extracted("api_node_reboot")
        published: list[tuple[str, Any]] = []
        store = types.SimpleNamespace(lock=threading.RLock(), game_state={"phase": 0})

        def publish_batch(commands: list[tuple[str, Any]]) -> dict[str, Any]:
            published.extend(commands)
            return {
                "mqtt_queued": True,
                "command_count": len(commands),
                "queued_count": len(commands),
                "failed_commands": [],
                "partial": False,
            }

        namespace.update({
            "jsonify": JsonResponse,
            "REBOOT_NODE_IDS": frozenset({
                "lighting", "maglock", "images_piano", "chess", "knocking",
                "candles", "star_slider", "star_sky",
            }),
            "store": store,
            "_stale_action_response": lambda _data: None,
            "mqtt_publish_batch": publish_batch,
        })
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "images_piano"})
        response = namespace["api_node_reboot"]()
        self.assertTrue(response["mqtt_queued"])
        self.assertFalse(response["device_confirmed"])
        self.assertEqual(published, [("images_piano/sys/cmd", "REBOOT")])

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "images"})
        response, status = namespace["api_node_reboot"]()
        self.assertEqual(status, 400)
        self.assertFalse(response["ok"])
        self.assertEqual(len(published), 1)

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "all"})
        response = namespace["api_node_reboot"]()
        self.assertTrue(response["mqtt_queued"])
        self.assertEqual(response["queued_count"], 8)
        self.assertEqual(len(published), 9)
        self.assertTrue(all(topic.endswith("/sys/cmd") and payload == "REBOOT" for topic, payload in published))

        store.game_state["phase"] = 3
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "all"})
        response, status = namespace["api_node_reboot"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(len(published), 9)

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "maglock"})
        response, status = namespace["api_node_reboot"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(len(published), 9)

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "lighting"})
        response = namespace["api_node_reboot"]()
        self.assertTrue(response["ok"])
        self.assertEqual(response["queued_count"], 1)
        self.assertEqual(published[-1], ("lighting/sys/cmd", "REBOOT"))

    def test_service_restart_is_whitelisted_scheduled_and_requires_confirmation(self) -> None:
        helper = extracted("_schedule_service_restart")
        calls: list[tuple[list[str], int, str]] = []
        helper.update({
            "SERVICE_RESTART_TARGETS": {
                "game_master": {"label": "Game Master", "unit": "game_master.service"},
                "dashboard": {"label": "Dashboard", "unit": "er1-web.service"},
            },
            "_run_process": lambda command, *, timeout, label: calls.append((command, timeout, label)),
        })
        config = helper["_schedule_service_restart"]("dashboard")
        self.assertEqual(config["unit"], "er1-web.service")
        self.assertEqual(calls, [([
            "/usr/bin/sudo", "-n", "/usr/bin/systemd-run", "--quiet", "--collect", "--on-active=1s",
            "/usr/bin/systemctl", "restart", "er1-web.service",
        ], 8, "Neustart von Dashboard")])
        with self.assertRaisesRegex(ValueError, "Unbekannter Systemdienst"):
            helper["_schedule_service_restart"]("database")

        namespace = extracted("api_service_restart")
        scheduled: list[str] = []
        namespace.update({
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "_schedule_service_restart": lambda target: scheduled.append(target) or {
                "label": "Game Master",
                "unit": "game_master.service",
            },
            "LOG": types.SimpleNamespace(error=lambda *_args: None),
        })
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {
            "target": "game_master",
            "confirmed": False,
            "expected_phase": 10,
            "expected_run_id": "run-10",
        })
        response, status = namespace["api_service_restart"]()
        self.assertEqual(status, 400)
        self.assertFalse(response["ok"])
        self.assertEqual(scheduled, [])

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {
            "target": "game_master",
            "confirmed": True,
            "expected_phase": 10,
            "expected_run_id": "run-10",
        })
        response = namespace["api_service_restart"]()
        self.assertTrue(response["scheduled"])
        self.assertEqual(response["service"], "game_master.service")
        self.assertEqual(scheduled, ["game_master"])

    def test_summary_email_outage_is_retryable_and_keeps_completed_game(self) -> None:
        namespace = extracted("api_send_summary_email")
        namespace.update({
            "request": types.SimpleNamespace(get_json=lambda silent: {}),
            "jsonify": JsonResponse,
            "current_raw_run_payload": lambda: {"run_id": "run-1", "leaderboard_code": "123456"},
            "normalize_booking_selection": lambda booking: dict(booking),
            "store": types.SimpleNamespace(get_selected_booking=lambda: {
                "kind": "booking",
                "players": 4,
                "customerEmail": "guest@example.invalid",
            }),
            "send_summary_email_via_ssh": lambda _payload: (_ for _ in ()).throw(RuntimeError("network offline")),
            "LOG": types.SimpleNamespace(warning=lambda *_args: None),
        })

        response, status = namespace["api_send_summary_email"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertTrue(response["retryable"])
        self.assertTrue(response["game_saved"])

    def test_hint_delta_endpoint_accepts_only_minus_or_plus_one(self) -> None:
        namespace = extracted("api_set_hint_count")
        calls: list[tuple[str, int]] = []
        namespace.update({
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "publish_hint_count_change": lambda riddle, **kwargs: calls.append((riddle, kwargs["delta"])) or 3,
        })
        for body in (
            {"riddle": "images", "delta": 2},
            {"riddle": "images", "delta": 0},
            {"riddle": "images", "count": 4},
        ):
            namespace["request"] = types.SimpleNamespace(get_json=lambda force, body=body: body)
            response, status = namespace["api_set_hint_count"]()
            self.assertEqual(status, 400)
            self.assertFalse(response["ok"])
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {
            "riddle": "images",
            "delta": -1,
            "expected_phase": 10,
            "expected_run_id": "run-10",
        })
        response = namespace["api_set_hint_count"]()
        self.assertEqual(response["hint_count"], 3)
        self.assertEqual(calls, [("images", -1)])

    def test_hint_delta_payload_and_durable_rollback(self) -> None:
        namespace = extracted("publish_hint_count_change")
        store = types.SimpleNamespace(lock=threading.RLock(), local_hint_counts={"images": 2})
        writes: list[dict[str, int]] = []
        published: list[tuple[str, dict[str, Any]]] = []
        namespace.update({
            "store": store,
            "TOPIC_GAME_CMD": "game/cmd",
            "save_hint_store": lambda counts: writes.append(dict(counts)),
            "mqtt_publish": lambda topic, payload: published.append((topic, payload)) or True,
        })

        result = namespace["publish_hint_count_change"](
            "images",
            delta=1,
            expected_phase=3,
            expected_run_id="run-guard",
        )
        self.assertEqual(result, 3)
        self.assertEqual(writes, [{"images": 3}])
        self.assertEqual(published, [("game/cmd", {
            "cmd": "adjust_hint_count",
            "riddle": "images",
            "delta": 1,
            "expected_phase": 3,
            "expected_run_id": "run-guard",
        })])

        writes.clear()
        namespace["mqtt_publish"] = lambda _topic, _payload: False
        with self.assertRaisesRegex(RuntimeError, "Tippzähler"):
            namespace["publish_hint_count_change"](
                "images",
                delta=-1,
                expected_phase=3,
                expected_run_id="run-guard",
            )
        self.assertEqual(store.local_hint_counts, {"images": 3})
        self.assertEqual(writes, [{"images": 2}, {"images": 3}])

    def test_manual_booking_preserves_run_scope_from_prepare_through_live_game(self) -> None:
        namespace = extracted("api_select_booking")
        expected_run_ids: list[str] = []
        store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 2, "run": {"run_id": "prepared-run"}},
            get_start_assignment=lambda: {},
        )
        namespace.update({
            "jsonify": JsonResponse,
            "store": store,
            "_stale_action_response": lambda _data: None,
            "normalize_booking_selection": lambda booking: booking,
            "publish_booking_selection": lambda booking, *, expected_run_id, expected_phase, supersede_automatic: (
                expected_run_ids.append(expected_run_id) or booking
            ),
        })
        body = {
            "booking": {"id": "manual", "players": 2},
            "expected_phase": 2,
            "expected_run_id": "prepared-run",
        }
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: body)
        self.assertTrue(namespace["api_select_booking"]()["ok"])
        self.assertEqual(expected_run_ids, ["prepared-run"])

        store.game_state["phase"] = 10
        body["expected_phase"] = 10
        self.assertTrue(namespace["api_select_booking"]()["ok"])
        self.assertEqual(expected_run_ids, ["prepared-run", "prepared-run"])

    def test_booking_changes_are_local_without_mutating_absent_or_completed_runs(self) -> None:
        namespace = extracted("api_select_booking")

        class Store:
            def __init__(self, phase: int) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": phase, "run": {"run_id": "completed-run"} if phase == 14 else None}
                self.selected = {"id": "__empty__", "kind": "empty", "players": 0, "language": "de"}

            def set_selected_booking(
                self,
                booking: dict[str, Any],
                *,
                expected_phase: int | None = None,
                local_only: bool = False,
                require_durable: bool = False,
            ) -> dict[str, Any]:
                self.assert_durable = require_durable
                self.expected_phase = expected_phase
                self.local_only = local_only
                self.selected = dict(booking)
                return self.selected

            def get_selected_booking(self) -> dict[str, Any]:
                return dict(self.selected)

            @staticmethod
            def get_start_assignment() -> dict[str, Any]:
                return {}

        published = []
        namespace.update({
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "normalize_booking_selection": lambda booking: dict(booking),
            "normalize_hint_language": lambda language: str(language),
            "publish_booking_selection": lambda *_args, **_kwargs: published.append(True),
        })
        for phase, expected_scope in ((0, "next_run"), (1, "next_run"), (2, "next_run"), (14, "completed_summary")):
            with self.subTest(phase=phase):
                store = Store(phase)
                namespace["store"] = store
                namespace["request"] = types.SimpleNamespace(get_json=lambda force, phase=phase: {
                    "booking": {"id": f"booking-{phase}", "kind": "booking", "players": 4},
                    "expected_phase": phase,
                    "expected_run_id": "completed-run" if phase == 14 else "",
                })
                response = namespace["api_select_booking"]()
                self.assertTrue(response["ok"])
                self.assertTrue(response["applied"])
                self.assertFalse(response["mqtt_queued"])
                self.assertEqual(response["scope"], expected_scope)
                self.assertTrue(store.assert_durable)
                self.assertEqual(store.expected_phase, phase)
                self.assertTrue(store.local_only)
        self.assertEqual(published, [])

    def test_prepared_run_accepts_scoped_booking_selection(self) -> None:
        set_selected_booking, namespace = extracted_method("DashboardStore", "set_selected_booking")
        persisted: list[dict[str, Any]] = []
        namespace.update({
            "normalize_booking_selection": lambda booking: dict(booking),
            "save_selected_booking": lambda booking, **_kwargs: persisted.append(dict(booking)) or True,
        })
        store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 2, "run": {"run_id": "prepared-run"}},
            selected_booking={},
            local_players_count_override=None,
            local_booking_override_phase=None,
            persistence_degraded=False,
        )

        selected = set_selected_booking(
            store,
            {"id": "booking-1", "players": 4, "bookingCode": "BOOK-1", "customerEmail": "team@example.invalid"},
            expected_run_id="prepared-run",
            require_durable=True,
        )

        self.assertEqual(selected["id"], "booking-1")
        self.assertEqual(store.game_state["run"]["booking"], selected)
        self.assertEqual(store.game_state["players_count"], 4)
        self.assertEqual(persisted, [selected])

    def test_local_booking_is_atomic_and_does_not_mutate_completed_run(self) -> None:
        set_selected_booking, namespace = extracted_method("DashboardStore", "set_selected_booking")
        persisted: list[tuple[dict[str, Any], int | None]] = []
        namespace.update({
            "normalize_booking_selection": lambda booking: dict(booking),
            "save_selected_booking": lambda booking, *, local_override_phase=None: (
                persisted.append((dict(booking), local_override_phase)) or True
            ),
        })
        completed_run = {
            "run_id": "completed-run",
            "players_count": 2,
            "booking": {"id": "original", "players": 2},
        }
        store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 14, "run": copy.deepcopy(completed_run)},
            selected_booking={},
            local_players_count_override=None,
            local_booking_override_phase=None,
            persistence_degraded=False,
        )

        selected = set_selected_booking(
            store,
            {"id": "summary-recipient", "players": 4},
            expected_phase=14,
            local_only=True,
            require_durable=True,
        )

        self.assertEqual(store.game_state["run"], completed_run)
        self.assertEqual(store.game_state["booking"], selected)
        self.assertEqual(store.local_booking_override_phase, 14)
        self.assertEqual(persisted, [(selected, 14)])

        store.game_state = {"phase": 2, "run": {"run_id": "new-run"}}
        with self.assertRaisesRegex(ValueError, "nicht mehr zulässig"):
            set_selected_booking(
                store,
                {"id": "too-late", "players": 3},
                expected_phase=2,
                local_only=True,
            )
        self.assertEqual(persisted, [(selected, 14)])

    def test_riddle_time_rejects_zero_and_negative_before_mqtt(self) -> None:
        namespace = extracted("parse_mmss_input", "api_riddle_time")
        published: list[tuple[str, Any]] = []
        namespace.update({
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "_canonical_riddle_name": lambda value: str(value),
            "store": types.SimpleNamespace(snapshot=lambda: {"riddles": [{"id": "images", "phase_state": "solved"}]}),
            "build_riddle_time_preview": lambda *_args: {},
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda topic, payload: published.append((topic, payload)) or True,
        })
        for value in ("0", "-1", "00:00"):
            namespace["request"] = types.SimpleNamespace(
                get_json=lambda force, value=value: {"riddle": "images", "time_text": value, "adjustment_side": "next"}
            )
            response, status = namespace["api_riddle_time"]()
            self.assertEqual(status, 400)
            self.assertIn("mindestens eine Sekunde", response["error"])
        self.assertEqual(published, [])

        namespace["request"] = types.SimpleNamespace(
            get_json=lambda force: {
                "riddle": "images",
                "time_text": "1",
                "adjustment_side": "next",
                "expected_phase": 10,
                "expected_run_id": "run-10",
            }
        )
        response = namespace["api_riddle_time"]()
        self.assertEqual(response["solve_time_s"], 1.0)
        self.assertEqual(published[-1][1]["solve_time_s"], 1.0)
        self.assertEqual(published[-1][1]["adjustment_side"], "next")
        self.assertEqual(published[-1][1]["expected_phase"], 10)
        self.assertEqual(published[-1][1]["expected_run_id"], "run-10")

    def test_game_command_routes_forward_phase_and_run_guards(self) -> None:
        guard = {"expected_phase": 10, "expected_run_id": "run-10"}

        phase_namespace = extracted("api_phase")
        phase_published: list[dict[str, Any]] = []
        phase_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"action": "maintenance", **guard}),
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: phase_published.append(payload) or True,
            "store": types.SimpleNamespace(set_local_phase=lambda _action: None),
        })
        self.assertTrue(phase_namespace["api_phase"]()["ok"])
        self.assertEqual(phase_published, [{
            "cmd": "set_mode",
            "mode": "maintenance",
            **guard,
        }])

        players_namespace = extracted("parse_players_count_input", "api_players_count")
        players_published: list[dict[str, Any]] = []
        players_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"players_count": 4, **guard}),
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: players_published.append(payload) or True,
            "store": types.SimpleNamespace(set_local_players_count=lambda _count: None),
        })
        self.assertTrue(players_namespace["api_players_count"]()["ok"])
        self.assertEqual(players_published, [{"cmd": "set_players_count", "players_count": 4, **guard}])

        solve_namespace = extracted("api_solve")
        solve_published: list[dict[str, Any]] = []
        solve_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {"node": "knocking", **guard}),
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: solve_published.append(payload) or True,
        })
        self.assertTrue(solve_namespace["api_solve"]()["ok"])
        self.assertEqual(solve_published, [{
            "cmd": "solve",
            "node": "knocking",
            "riddle": "knocking",
            **guard,
        }])

        outcome_namespace = extracted("api_riddle_outcome")
        outcome_published: list[dict[str, Any]] = []
        outcome_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {
                "riddle": "images",
                "outcome": "skipped",
                "advance": False,
                **guard,
            }),
            "jsonify": JsonResponse,
            "_canonical_riddle_name": lambda value: str(value),
            "_stale_action_response": lambda _data: None,
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: outcome_published.append(payload) or True,
        })
        self.assertTrue(outcome_namespace["api_riddle_outcome"]()["ok"])
        self.assertEqual(outcome_published, [{
            "cmd": "set_riddle_outcome",
            "riddle": "images",
            "outcome": "skipped",
            "advance": False,
            **guard,
        }])

    def test_booking_and_durable_start_publish_actual_guards(self) -> None:
        booking_namespace = extracted("publish_booking_selection")
        booking_published: list[dict[str, Any]] = []
        booking_store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 3, "run": {"run_id": "run-3"}},
            start_assignment={},
        )
        booking_namespace.update({
            "store": booking_store,
            "normalize_booking_selection": lambda booking: dict(booking),
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: booking_published.append(payload) or True,
        })
        booking = {"id": "booking-1", "kind": "booking", "players": 2}
        booking_namespace["publish_booking_selection"](
            booking,
            expected_phase=3,
            expected_run_id="run-3",
        )
        self.assertEqual(booking_published, [{
            "cmd": "set_booking",
            "booking": booking,
            "expected_phase": 3,
            "expected_run_id": "run-3",
        }])

        start_namespace = extracted("_publish_start_for_claim")
        start_published: list[dict[str, Any]] = []

        class StartStore:
            def __init__(self) -> None:
                self.lock = threading.RLock()
                self.game_state = {"phase": 2, "run": {"run_id": "prepared-run"}}
                self.start_assignment = {
                    "active": True,
                    "cancel_requested": False,
                    "intent_state": "authorized",
                    "claim_id": "claim-1",
                    "run_id": "prepared-run",
                    "_intent_durable_runtime": True,
                }

            def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
                self.start_assignment = dict(value)
                return True

        start_namespace.update({
            "store": StartStore(),
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda _topic, payload: start_published.append(payload) or True,
            "LOG": types.SimpleNamespace(critical=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None),
        })
        self.assertTrue(start_namespace["_publish_start_for_claim"]("claim-1"))
        self.assertEqual(start_published, [{
            "cmd": "start",
            "expected_phase": 2,
            "expected_run_id": "prepared-run",
        }])

    def test_maintenance_rejects_hidden_hint_time_and_outcome_mutations(self) -> None:
        guard = {"expected_phase": 1, "expected_run_id": ""}

        hint_namespace = extracted("api_set_hint_count")
        hint_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {
                "riddle": "images",
                "delta": 1,
                **guard,
            }),
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "publish_hint_count_change": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("published")),
        })
        response, status = hint_namespace["api_set_hint_count"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])

        time_namespace = extracted("parse_mmss_input", "api_riddle_time")
        time_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {
                "riddle": "images",
                "time_text": "12",
                "adjustment_side": "next",
                **guard,
            }),
            "jsonify": JsonResponse,
            "_canonical_riddle_name": lambda value: str(value),
            "_stale_action_response": lambda _data: None,
        })
        response, status = time_namespace["api_riddle_time"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])

        outcome_namespace = extracted("api_riddle_outcome")
        outcome_namespace.update({
            "request": types.SimpleNamespace(get_json=lambda force: {
                "riddle": "images",
                "outcome": "skipped",
                **guard,
            }),
            "jsonify": JsonResponse,
            "_canonical_riddle_name": lambda value: str(value),
            "_stale_action_response": lambda _data: None,
        })
        response, status = outcome_namespace["api_riddle_outcome"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])


class HintTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = json.loads(DEFAULT_HINTS_PATH.read_text(encoding="utf-8"))

    def hint_namespace(self) -> dict[str, Any]:
        namespace = extracted("_validate_hint_templates", "save_hint_templates")
        namespace.update({
            "HINT_TEMPLATE_RIDDLES": tuple(self.defaults["templates"]),
            "HINT_TEMPLATE_LANGUAGES": ("de", "en", "it"),
            "HINT_TEMPLATE_VERSION": 2,
            "MAX_HINT_TIPS_PER_LANGUAGE": 20,
            "MAX_HINT_TIP_CHARS": 2000,
            "MAX_HINT_OFFER_AFTER_S": 86400,
            "MAX_HINT_TEMPLATE_BYTES": 96 * 1024,
            "_hint_templates_lock": threading.RLock(),
            "_hint_templates": {},
        })
        return namespace

    def test_canonical_texts_and_neutral_empty_templates_are_server_owned(self) -> None:
        templates = self.defaults["templates"]
        self.assertEqual(self.defaults["version"], 2)
        self.assertEqual(
            templates["images"]["de"][0]["text"],
            "Fällt euch irgendetwas Ungewöhnliches im Raum auf? Was könnte man damit machen?",
        )
        self.assertEqual(
            {key: value for key, value in templates["images"]["de"][0].items() if key != "text"},
            {"offer_after_total_s": None, "offer_after_riddle_s": None},
        )
        self.assertEqual(templates["tangram"], {"de": [], "en": [], "it": []})
        self.assertEqual(templates["sissi"], {"de": [], "en": [], "it": []})
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("const HINT_TEMPLATES", javascript)
        self.assertIn("NO_HINT_TEMPLATE_TEXT", javascript)

    def test_hint_persistence_is_validated_bounded_and_atomic(self) -> None:
        namespace = self.hint_namespace()
        writes: list[tuple[Path, dict[str, Any]]] = []
        runtime = Path("ignored-runtime-hints.json")
        namespace.update({
            "HINT_TEMPLATE_RUNTIME_PATH": runtime,
            "_atomic_write_dashboard_json": lambda path, payload: writes.append((path, copy.deepcopy(payload))) or True,
        })
        saved, synced = namespace["save_hint_templates"](self.defaults)
        self.assertTrue(synced)
        self.assertEqual(saved, self.defaults)
        self.assertEqual(writes, [(runtime, self.defaults)])

        oversized = copy.deepcopy(self.defaults)
        oversized["templates"]["images"]["de"][0]["text"] = "x" * 2001
        with self.assertRaisesRegex(ValueError, "länger als 2000"):
            namespace["save_hint_templates"](oversized)
        self.assertEqual(len(writes), 1)

    def test_v1_files_migrate_losslessly_but_saves_require_strict_v2_objects(self) -> None:
        namespace = extracted("_validate_hint_templates", "_read_hint_templates")
        namespace.update({
            "HINT_TEMPLATE_RIDDLES": tuple(self.defaults["templates"]),
            "HINT_TEMPLATE_LANGUAGES": ("de", "en", "it"),
            "HINT_TEMPLATE_VERSION": 2,
            "MAX_HINT_TIPS_PER_LANGUAGE": 20,
            "MAX_HINT_TIP_CHARS": 2000,
            "MAX_HINT_OFFER_AFTER_S": 86400,
            "MAX_HINT_TEMPLATE_BYTES": 96 * 1024,
        })
        legacy = {
            "version": 1,
            "templates": {
                riddle: {
                    language: [tip["text"] for tip in tips]
                    for language, tips in languages.items()
                }
                for riddle, languages in self.defaults["templates"].items()
            },
        }
        legacy["templates"]["images"]["de"][0] = "  Text bleibt unverändert.  "
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            migrated = namespace["_read_hint_templates"](path)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["templates"]["images"]["de"][0], {
            "text": "  Text bleibt unverändert.  ",
            "offer_after_total_s": None,
            "offer_after_riddle_s": None,
        })

        with self.assertRaisesRegex(ValueError, "Version 2"):
            namespace["_validate_hint_templates"](legacy)
        invalid = copy.deepcopy(self.defaults)
        invalid["templates"]["images"]["de"][0]["offer_after_total_s"] = True
        with self.assertRaisesRegex(ValueError, "0 und 86400"):
            namespace["_validate_hint_templates"](invalid)
        invalid = copy.deepcopy(self.defaults)
        invalid["templates"]["images"]["de"][0]["extra"] = 1
        with self.assertRaisesRegex(ValueError, "genau Text"):
            namespace["_validate_hint_templates"](invalid)

    def test_default_and_runtime_v1_files_both_load_as_v2(self) -> None:
        namespace = extracted("_validate_hint_templates", "_read_hint_templates", "load_hint_templates")
        namespace.update({
            "HINT_TEMPLATE_RIDDLES": tuple(self.defaults["templates"]),
            "HINT_TEMPLATE_LANGUAGES": ("de", "en", "it"),
            "HINT_TEMPLATE_VERSION": 2,
            "MAX_HINT_TIPS_PER_LANGUAGE": 20,
            "MAX_HINT_TIP_CHARS": 2000,
            "MAX_HINT_OFFER_AFTER_S": 86400,
            "MAX_HINT_TEMPLATE_BYTES": 96 * 1024,
            "LOG": types.SimpleNamespace(error=lambda *_args, **_kwargs: None),
        })
        legacy = {
            "version": 1,
            "templates": {
                riddle: {
                    language: [tip["text"] for tip in tips]
                    for language, tips in languages.items()
                }
                for riddle, languages in self.defaults["templates"].items()
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            defaults_path = Path(temp_dir) / "defaults.json"
            runtime_path = Path(temp_dir) / "runtime.json"
            defaults_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            namespace.update({
                "HINT_TEMPLATE_DEFAULTS_PATH": defaults_path,
                "HINT_TEMPLATE_RUNTIME_PATH": runtime_path,
            })
            self.assertEqual(namespace["load_hint_templates"]()["version"], 2)

            defaults_path.write_text(json.dumps(self.defaults, ensure_ascii=False), encoding="utf-8")
            runtime_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            loaded = namespace["load_hint_templates"]()
            self.assertEqual(loaded["version"], 2)
            self.assertEqual(loaded["templates"]["images"]["de"][0]["text"], legacy["templates"]["images"]["de"][0])

    def test_hint_edit_endpoint_is_gated_to_maintenance(self) -> None:
        namespace = extracted("api_hint_templates")
        store = types.SimpleNamespace(
            lock=threading.RLock(),
            game_state={"phase": 3},
            persistence_degraded=False,
        )
        namespace.update({
            "jsonify": JsonResponse,
            "store": store,
            "_stale_action_response": lambda _data: None,
            "save_hint_templates": lambda payload: (payload, True),
        })
        namespace["request"] = types.SimpleNamespace(get_json=lambda silent: {"templates": self.defaults})
        response, status = namespace["api_hint_templates"]()
        self.assertEqual(status, 409)
        self.assertIn("Phase 1", response["error"])

        store.game_state["phase"] = 1
        response = namespace["api_hint_templates"]()
        self.assertTrue(response["ok"])


class DashboardPreviewTests(unittest.TestCase):
    RIDDLE_KEYS = (
        "images", "piano", "prison", "wheel", "chains", "tangram",
        "magnet", "chess", "knocking", "candles", "stars", "sissi",
    )

    def snapshot(self) -> dict[str, Any]:
        return {
            "game": {"run_id": "run-live", "elapsed_s": -5},
            "riddles": [
                {
                    "id": key,
                    "label": key.title(),
                    "display_time_s": 0,
                    "hint_count": 0,
                    "phase_state": "pending",
                    "skipped": False,
                }
                for key in self.RIDDLE_KEYS
            ],
        }

    def ranking_namespace(self) -> dict[str, Any]:
        namespace = extracted(
            "_nonnegative_preview_number",
            "build_ranking_preview_candidate",
            "_ranking_value",
            "build_ranking_preview_dashboard_dto",
        )
        namespace.update({
            "RIDDLE_ORDER_V2": list(self.RIDDLE_KEYS),
            "RIDDLE_LABELS_V2": {key: key.title() for key in self.RIDDLE_KEYS},
            "_canonical_riddle_name": lambda value: str(value or ""),
        })
        return namespace

    def test_ranking_candidate_uses_all_raw_rows_and_nonnegative_current_duration(self) -> None:
        namespace = self.ranking_namespace()
        snapshot = self.snapshot()
        snapshot["riddles"][0].update({
            "display_time_s": 12.345,
            "hint_count": 2,
            "phase_state": "solved",
        })
        snapshot["riddles"][5].update({
            "display_time_s": 30,
            "hint_count": 1,
            "phase_state": "skipped",
            "skipped": True,
        })

        candidate = namespace["build_ranking_preview_candidate"](snapshot)

        self.assertEqual(candidate["runId"], "run-live")
        self.assertEqual(candidate["durationS"], 0.0)
        self.assertEqual(candidate["hintCount"], 3)
        self.assertEqual([row["puzzleKey"] for row in candidate["rawPuzzles"]], list(self.RIDDLE_KEYS))
        self.assertEqual(candidate["rawPuzzles"][0], {
            "puzzleKey": "images",
            "timeS": 12.345,
            "hintCount": 2,
            "solved": True,
            "skipped": False,
        })
        self.assertEqual(candidate["rawPuzzles"][5], {
            "puzzleKey": "tangram",
            "timeS": 30.0,
            "hintCount": 1,
            "solved": False,
            "skipped": True,
        })

        website_result = {
            "ok": True,
            "overallRank": 2,
            "overallRankTotal": 9,
            "rawPuzzles": [
                {"puzzleKey": key, "rank": (3 if key == "images" else None), "rankTotal": 8}
                for key in self.RIDDLE_KEYS
            ],
        }
        dto = namespace["build_ranking_preview_dashboard_dto"](candidate, website_result)
        self.assertEqual(dto["overall"], {"rank": 2, "rank_total": 9})
        self.assertEqual(dto["riddles"][0], {
            "id": "images", "label": "Images", "rank": 3, "rank_total": 8,
        })
        self.assertEqual(len(dto["riddles"]), 12)

    def test_ranking_route_calls_website_once_and_controls_outages(self) -> None:
        namespace = extracted("_no_store_json", "api_ranking_preview")
        snapshot = self.snapshot()
        snapshot_calls = 0
        website_calls: list[tuple[str, dict[str, Any]]] = []

        class FakeStore:
            def snapshot(inner_self) -> dict[str, Any]:
                nonlocal snapshot_calls
                snapshot_calls += 1
                return snapshot

        candidate = {"runId": "run-live", "durationS": 1, "hintCount": 0, "rawPuzzles": []}
        dto = {"ok": True, "run_id": "run-live", "overall": {"rank": 1, "rank_total": 1}, "riddles": []}
        namespace.update({
            "jsonify": JsonResponse,
            "store": FakeStore(),
            "LOG": types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
            "build_ranking_preview_candidate": lambda value: candidate if value is snapshot else None,
            "build_ranking_preview_dashboard_dto": lambda sent, received: dto if sent is candidate and received == {"ok": True} else None,
            "post_website_json": lambda path, payload: website_calls.append((path, payload)) or {"ok": True},
        })

        response = namespace["api_ranking_preview"]()
        self.assertEqual(response, dto)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(snapshot_calls, 1)
        self.assertEqual(website_calls, [("/api/leaderboard/ranking-preview", candidate)])

        namespace["post_website_json"] = lambda *_args: (_ for _ in ()).throw(RuntimeError("website down"))
        response, status = namespace["api_ranking_preview"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(snapshot_calls, 2)

    def riddle_preview_namespace(self) -> dict[str, Any]:
        namespace = extracted("build_riddle_time_preview")
        namespace.update({
            "RIDDLE_ORDER_V2": list(self.RIDDLE_KEYS),
            "RIDDLE_LABELS_V2": {
                **{key: key.title() for key in self.RIDDLE_KEYS},
                "magnet": "Magnet",
                "chess": "Schach",
            },
            "MAX_RIDDLE_TIME_PREVIEW_S": 7 * 24 * 60 * 60,
            "_canonical_riddle_name": lambda value: str(value or ""),
            "plan_riddle_time_change": plan_riddle_time_change,
        })
        return namespace

    def test_riddle_time_preview_matches_parallel_stage_and_active_receiver_semantics(self) -> None:
        preview = self.riddle_preview_namespace()["build_riddle_time_preview"]
        snapshot = self.snapshot()
        rows = {row["id"]: row for row in snapshot["riddles"]}
        rows["tangram"].update({"label": "Tangram", "display_time_s": 120, "phase_state": "solved"})
        rows["magnet"].update({"label": "Magnet", "display_time_s": 100, "phase_state": "solved"})
        rows["chess"].update({"label": "Schach", "display_time_s": 60, "phase_state": "active"})

        result = preview(snapshot, "tangram", 90, "next")
        self.assertEqual(result, {
            "source": {"riddle_id": "tangram", "label": "Tangram", "old_time_s": 120.0, "new_time_s": 90.0},
            "receiving": {"riddle_id": "chess", "label": "Schach", "old_time_s": 60.0, "new_time_s": 80.0},
            "adjustment_side": "next",
            "source_stage_delta_s": -20.0,
        })

        unchanged_stage = preview(snapshot, "magnet", 90, "next")
        self.assertEqual(unchanged_stage["source"]["new_time_s"], 90.0)
        self.assertEqual(unchanged_stage["source_stage_delta_s"], 0.0)
        self.assertIsNone(unchanged_stage["receiving"])

    def test_riddle_time_preview_uses_selected_adjacent_stage_and_enforces_parallel_floor(self) -> None:
        preview = self.riddle_preview_namespace()["build_riddle_time_preview"]
        snapshot = self.snapshot()
        rows = {row["id"]: row for row in snapshot["riddles"]}
        rows["wheel"].update({"display_time_s": 100, "phase_state": "solved"})
        rows["chains"].update({"label": "Ketten", "display_time_s": 40, "phase_state": "active"})
        result = preview(snapshot, "wheel", 90, "next")
        self.assertEqual(result["receiving"], {
            "riddle_id": "chains", "label": "Ketten", "old_time_s": 40.0, "new_time_s": 50.0,
        })

        rows["prison"].update({"label": "Gefängnis", "display_time_s": 30, "phase_state": "solved"})
        previous = preview(snapshot, "wheel", 80, "previous")
        self.assertEqual(previous["receiving"], {
            "riddle_id": "prison", "label": "Gefängnis", "old_time_s": 30.0, "new_time_s": 50.0,
        })

        rows["chains"].update({"display_time_s": 100, "phase_state": "solved"})
        rows["tangram"].update({"display_time_s": 80, "phase_state": "active"})
        rows["magnet"].update({"display_time_s": 60, "phase_state": "active"})
        with self.assertRaisesRegex(ValueError, "parallel receiving stage below 60.000"):
            preview(snapshot, "chains", 130, "next")

    def test_riddle_time_preview_options_return_both_server_calculations(self) -> None:
        namespace = self.riddle_preview_namespace()
        options_namespace = extracted("build_riddle_time_preview_options")
        options_namespace["build_riddle_time_preview"] = namespace["build_riddle_time_preview"]
        snapshot = self.snapshot()
        rows = {row["id"]: row for row in snapshot["riddles"]}
        rows["prison"].update({"display_time_s": 30, "phase_state": "solved"})
        rows["wheel"].update({"display_time_s": 100, "phase_state": "solved"})
        rows["chains"].update({"display_time_s": 40, "phase_state": "active"})

        options = options_namespace["build_riddle_time_preview_options"](snapshot, "wheel", 90)

        self.assertEqual([item["adjustment_side"] for item in options], ["previous", "next"])
        self.assertTrue(all(item["available"] for item in options))
        self.assertEqual(options[0]["receiving"]["new_time_s"], 40.0)
        self.assertEqual(options[1]["receiving"]["new_time_s"], 50.0)

    def test_parse_mmss_normalizes_strings_and_numbers_once(self) -> None:
        parse = extracted("parse_mmss_input")["parse_mmss_input"]
        self.assertEqual(parse("01:02,5"), 62.5)
        self.assertEqual(parse(12.25), 12.25)
        with self.assertRaises(ValueError):
            parse("01:75")
        with self.assertRaises(ValueError):
            parse(True)

    def test_riddle_time_preview_route_is_guarded_read_only_and_not_receipted(self) -> None:
        namespace = extracted("_no_store_json", "api_riddle_time_preview")
        snapshot_calls = 0

        class FakeStore:
            def snapshot(inner_self) -> dict[str, Any]:
                nonlocal snapshot_calls
                snapshot_calls += 1
                return {"snapshot": True}

        namespace.update({
            "jsonify": JsonResponse,
            "store": FakeStore(),
            "request": types.SimpleNamespace(args={
                "riddle": "chess",
                "time_text": "12.5",
                "expected_phase": "9",
                "expected_run_id": "run-live",
            }),
            "_stale_action_response": lambda _data: None,
            "_canonical_riddle_name": lambda value: str(value or ""),
            "parse_mmss_input": lambda value: float(value),
            "MAX_RIDDLE_TIME_PREVIEW_S": 1000,
            "build_riddle_time_preview_options": lambda value, riddle, seconds: [{
                "source": {"riddle_id": riddle, "old_time_s": 10, "new_time_s": seconds},
                "receiving": None,
                "adjustment_side": "previous",
                "source_stage_delta_s": 2.5,
                "available": True,
            }] if value == {"snapshot": True} else None,
        })
        response = namespace["api_riddle_time_preview"]()
        self.assertTrue(response["ok"])
        self.assertEqual(response["solve_time_s"], 12.5)
        self.assertEqual(len(response["options"]), 1)
        self.assertEqual(snapshot_calls, 1)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

        source = APP_PATH.read_text(encoding="utf-8")
        idempotent_block = source[source.index("IDEMPOTENT_API_PATHS"):source.index("def _normalize_operation_id")]
        self.assertNotIn('"/api/riddle-time-preview",', idempotent_block)
        self.assertIn("read-only GET", idempotent_block)


class DashboardSourceContractTests(unittest.TestCase):
    def test_effective_backend_labels_lights_and_active_nodes_match_er1(self) -> None:
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

        def last_literal(name: str) -> Any:
            values = [
                node.value
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
            ]
            self.assertTrue(values, name)
            return ast.literal_eval(values[-1])

        riddle_labels = last_literal("RIDDLE_LABELS_V2")
        phase_labels = last_literal("PHASE_LABELS_DE")
        light_groups = last_literal("LIGHT_GROUPS")
        node_labels = last_literal("NODE_LABELS")
        log_node_labels = last_literal("LOG_NODE_LABELS")
        self.assertEqual(riddle_labels["magnet"], "Magnet")
        self.assertEqual(riddle_labels["chess"], "Schach")
        self.assertEqual(phase_labels[0], "Aus")
        self.assertEqual(phase_labels[8], "Tangram & Magnet")
        self.assertEqual(phase_labels[9], "Schach")
        self.assertTrue(all(light_groups[key]["dimmable"] for key in ("r1", "r2_main", "r3_main")))
        active_nodes = [
            "lighting", "maglock", "images_piano", "chess", "knocking",
            "candles", "star_slider", "star_sky",
        ]
        self.assertEqual([node for node, _label in node_labels], active_nodes)
        self.assertEqual(list(log_node_labels), active_nodes)
        reboot_assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "REBOOT_NODE_IDS" for target in node.targets)
        )
        self.assertIsInstance(reboot_assignment.value, ast.Call)
        self.assertEqual(set(ast.literal_eval(reboot_assignment.value.args[0])), set(active_nodes))

        config_tree = ast.parse((GAME_MASTER_DIR / "config.py").read_text(encoding="utf-8"))
        physical_nodes = ast.literal_eval(next(
            node.value
            for node in config_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "PHYSICAL_NODE_IDS" for target in node.targets)
        ))
        self.assertEqual(list(physical_nodes), active_nodes)

    def test_custom_confirmation_and_action_contracts(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        html = HTML_PATH.read_text(encoding="utf-8")
        self.assertNotIn("window.confirm", javascript)
        self.assertIn('id="confirmDialog"', html)
        self.assertIn("function confirmAction", javascript)
        self.assertIn("window.setTimeout(() => cancelButton.focus(), 0)", javascript)
        self.assertIn("event.key === 'Enter'", javascript)
        self.assertIn("event.key === 'Escape'", javascript)
        self.assertNotIn("bookingConfirmDialog", javascript + html)
        self.assertIn(" Gelöst</button>", javascript)
        self.assertIn(" Überspringen</button>", javascript)
        self.assertIn("/api/riddle-time-preview", javascript)
        self.assertIn(">Speichern</button>", javascript)
        self.assertIn("riddle-time-options", javascript)
        self.assertIn("card.querySelector('.light-bulb-button')", javascript)
        self.assertIn("fmtDurationInput(seconds)", javascript)
        self.assertIn("/api/riddle-time-restore", javascript)
        self.assertIn("riddle-time-restore", javascript)
        self.assertIn("controlViewActive && gameMode", javascript)
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn("_installed_game_master_support_dir", source)
        self.assertIn('"User-Agent": "ER1-Dashboard/1.0"', source)
        self.assertNotIn("resetRiddle", javascript)
        self.assertNotIn("outcome: 'reset'", javascript)

        solve_start = javascript.index("async function solveRiddle")
        solve_end = javascript.index("async function toggleSkip", solve_start)
        start_start = javascript.index("async function handleStart")
        start_end = javascript.index("async function handlePhaseAction", start_start)
        time_start = javascript.index("async function saveRiddleTime")
        time_end = javascript.index("async function restoreRiddleTime", time_start)
        restore_end = javascript.index("function positionRiddleTimeEditor", time_end)
        self.assertIn("confirmAction", javascript[solve_start:solve_end])
        self.assertIn("confirmAction", javascript[start_start:start_end])
        self.assertNotIn("confirmAction", javascript[time_start:time_end])
        self.assertIn("confirmAction", javascript[time_end:restore_end])
        hint_start = javascript.index("async function changeHint")
        hint_end = javascript.index("function bindRiddleActions", hint_start)
        status_start = javascript.index("async function togglePastOutcome")
        status_end = javascript.index("function buildCurrentRiddleCard", status_start)
        self.assertNotIn("confirmAction", javascript[hint_start:hint_end])
        self.assertNotIn("confirmAction", javascript[status_start:status_end])

    def test_backend_routes_have_stale_guards_and_no_reset_or_clear_outcomes(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        guarded = {
            "api_phase", "api_select_booking", "api_solve", "api_lock", "api_light",
            "api_node_reboot", "api_service_restart", "api_set_hint_count", "api_riddle_time",
            "api_riddle_time_restore", "api_riddle_time_preview", "api_riddle_outcome",
            "api_log_archive_delete",
        }
        for name in guarded:
            segment = ast.get_source_segment(source, functions[name]) or ""
            self.assertIn("_stale_action_response", segment, name)
        outcome_source = ast.get_source_segment(source, functions["api_riddle_outcome"]) or ""
        self.assertNotIn('"reset_riddle"', outcome_source)
        self.assertNotIn('"clear_riddle_outcome"', outcome_source)
        self.assertNotIn('"mark_not_solved"', outcome_source)
        self.assertNotIn("reset_pending", source)

    def test_knocking_chess_polling_and_countdown_contracts_are_present(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertIn('state_payload.get("sequence_current")', source)
        self.assertIn("[1, 1, 1, 1, 1, 1, 2, 2, 3, 3, 3, 3]", javascript)
        self.assertIn("knockingIsCurrent ? 250 : 1000", javascript)
        self.assertNotIn("slot.expected", javascript)
        self.assertIn('class="chess-grid"', javascript)
        self.assertNotIn("elapsed_s -= GAME_COUNTDOWN_S", source)
        self.assertIn("fmtGameTime(readLocalTimer())", javascript)

        build_summary, namespace = extracted_method("DashboardStore", "_build_game_summary")
        namespace.update({
            "PHASE_META": {3: {"name": "start", "active": ("images",), "solved": ()}},
            "pretty_phase_name": lambda name: str(name),
            "_canonical_riddle_name": lambda name: str(name or ""),
            "GAME_COUNTDOWN_S": 5,
        })
        summary = build_summary(None, {
            "phase": 3,
            "timer_running": True,
            "current_riddle_name": "images",
            "run": {
                "run_id": "run-countdown",
                "live_duration_s": -5.0,
                "riddle_timings": {"images": {"status": "active", "live_time_s": -5.0}},
            },
        })
        self.assertEqual(summary["elapsed_s"], -5)
        self.assertEqual(summary["current_riddle_elapsed_s"], -5)

    def test_state_polling_recovers_quickly_and_phase_actions_require_fresh_state(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        html = HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("const STATE_FETCH_TIMEOUT_MS = 1000;", javascript)
        self.assertIn("const STATE_RETRY_DELAY_MS = 100;", javascript)
        self.assertIn("controller.abort()", javascript)
        self.assertIn("window.addEventListener('online', refreshStateAfterResume)", javascript)
        self.assertIn("document.addEventListener('visibilitychange', refreshStateAfterResume)", javascript)
        self.assertIn("async function refreshPhaseActionView()", javascript)

        start_begin = javascript.index("async function handleStart")
        start_end = javascript.index("async function handlePhaseAction", start_begin)
        phase_begin = start_end
        phase_end = javascript.index("async function sendSummaryEmail", phase_begin)
        self.assertIn("if (!await refreshPhaseActionView()) return;", javascript[start_begin:start_end])
        self.assertIn("bookingSaveBusy", javascript[start_begin:start_end])
        self.assertNotIn("bookingBusy", javascript[start_begin:start_end])
        self.assertIn("if (!await refreshPhaseActionView()) return;", javascript[phase_begin:phase_end])
        self.assertIn("clearRecoverableStateFeedback();", javascript)
        self.assertIn("app.js') }}?v=20261011-dashboard-v12", html)

    def test_time_popover_stars_and_hint_threshold_contracts(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        css = DASHBOARD_CSS_PATH.read_text(encoding="utf-8")
        self.assertIn('popover="auto"', javascript)
        self.assertIn("document.querySelectorAll('.riddle-time-editor:popover-open')", javascript)
        self.assertNotIn("window.setTimeout(() => {\n      riddleTimeEditing[riddle.id] = false", javascript)
        self.assertIn("selectDurationSegment(input, 'seconds')", javascript)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)
        self.assertIn("Waage ♎ (7), Wassermann ♒ (13), Skorpion ♏ (10)", javascript)
        self.assertIn("Zwillinge ♊ (7), Schütze ♐ (10), Löwe ♌ (10), Fische ♓ (13)", javascript)
        self.assertIn("Seit Spielstart", javascript)
        self.assertIn("Seit Rätselstart", javascript)
        self.assertIn("Fällig", javascript)

    def test_dashboard_mutations_use_durable_operation_receipts(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("class OperationLedger:", source)
        self.assertIn("@app.before_request", source)
        self.assertIn("@app.after_request", source)
        self.assertIn('@app.get("/api/operations/<operation_id>")', source)
        self.assertIn('"operation_status": "missing_idempotency_key"', source)
        self.assertIn("'Idempotency-Key': operation.id", javascript)
        self.assertIn("await prepareOperation(pending)", javascript)
        self.assertIn("operationState === 'prepared'", javascript)
        self.assertIn("async function executeIdempotentMutation", javascript)
        self.assertIn("async function reconcileStoredOperations", javascript)
        self.assertIn("mutationQueue", javascript)

    def test_dashboard_operation_client_runtime(self) -> None:
        result = subprocess.run(
            ["node", "--test", str(ROOT / "tests" / "test_dashboard_operation_client.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_maintenance_exposes_solve_but_not_run_mutations(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("function riddleMutationsAvailable()", javascript)
        self.assertIn("Number(state.game.phase || 0) >= 3 && Boolean(currentRunId())", javascript)
        actions_start = javascript.index("function riddleActionsHtml")
        actions_end = javascript.index("async function solveRiddle", actions_start)
        actions_source = javascript[actions_start:actions_end]
        self.assertIn("riddleMutationsAvailable()", actions_source)
        self.assertIn(" Gelöst</button>", actions_source)
        self.assertIn("canSkip ?", actions_source)

    def test_control_room_layout_contract_is_present(self) -> None:
        html = HTML_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        css = DASHBOARD_CSS_PATH.read_text(encoding="utf-8")

        self.assertIn('class="dashboard-body dashboard-v6"', html)
        self.assertIn("filename='dashboard.css'", html)
        self.assertIn("dashboard.css') }}?v=20261011-dashboard-v12", html)
        self.assertNotIn("filename='style.css'", html)
        self.assertIn('class="operator-tabs"', html)
        for label in ("Spielkontrolle", "Alle Spiele", "Admin"):
            self.assertIn(f">{label}</a>", html)
        self.assertLess(html.index('id="phaseValue"'), html.index('id="currentRiddlesPanel"'))
        self.assertLess(html.index('id="currentRiddlesPanel"'), html.index('id="allRiddlesPanel"'))
        for action in ("standby", "maintenance", "prepare"):
            self.assertIn(f'data-phase-action="{action}"', html)
        self.assertIn('id="startGameBtn"', html)
        self.assertNotIn('class="eyebrow"', html)
        self.assertIn('class="operator-log-toolbar"', html)
        phase_actions = html[html.index('class="phase-action-grid-v6"'):html.index('class="previous-phase"')]
        self.assertIn('id="startGameBtn"', phase_actions)
        for panel_id in ("bookingDetails", "allRiddlesPanel", "diagnosticsDetails", "maintenanceHintEditor"):
            self.assertIn(f'id="{panel_id}"', html)

        self.assertNotIn("emergencyOpen", javascript)
        self.assertIn("function isGameMode()", javascript)
        self.assertIn("Number(state.game.phase || 0) >= 2", javascript)
        self.assertIn("dashboard-preparation-mode", javascript)
        self.assertIn("function buildMaintenanceRiddleCard", javascript)
        self.assertIn("is-maintenance", javascript)
        self.assertNotIn("gameDrawerStrip", html)
        self.assertNotIn('id="hintTemplateDialog"', html)
        self.assertNotIn("openHintTemplateDialog", javascript)
        self.assertIn('data-service-restart="game_master"', html)
        self.assertIn('data-service-restart="dashboard"', html)
        self.assertIn("href=\"{{ url_for('game_viewer') }}\"", html)
        restart_start = javascript.index("async function restartSystemService")
        restart_end = javascript.index("async function", restart_start + 20)
        self.assertIn("confirmAction", javascript[restart_start:restart_end])
        self.assertNotIn("('stop_timer', 'Stop Timer')", html)

        self.assertIn("grid-template-columns: minmax(520px, 38%) minmax(0, 1fr);", css)
        self.assertIn('body.dashboard-v6[data-initial-view="control"]', css)
        self.assertIn("@media (max-width: 900px)", css)
        self.assertIn(".mission-grid,", css)
        self.assertIn(".mission-rail {", css)
        self.assertIn(".hidden,", css)
        self.assertIn("[hidden]", css)
        self.assertIn(".dashboard-preparation-mode .mission-metrics", css)
        self.assertIn('id="bookingDialog"', html)
        self.assertNotIn('id="bookingSelect" class="booking-select"', html[html.index('id="bookingDetails"'):html.index('id="currentRiddlesPanel"')])
        self.assertIn(".booking-dialog-form", css)
        self.assertIn('id="bookingEditBtn"', html)
        self.assertIn("bookingEditorOpen = Boolean(dialog?.open)", javascript)
        self.assertIn("let bookingDraft = null;", javascript)
        self.assertIn("bookingDraft = normalizeBooking", javascript)
        self.assertNotIn("active.id === 'hintLanguageSelect'", javascript)
        self.assertIn("Keine Live-Daten.", javascript)
        self.assertNotIn("Keine zusätzlichen Live-Daten.", javascript)
        self.assertIn("STAR_SIGN_SYMBOLS", javascript)
        self.assertIn("parallelIds.has(riddle.id)", javascript)
        self.assertIn("repeat(4, minmax(0, 1fr))", css)
        self.assertIn("minmax(0, 1.38fr) minmax(0, 0.78fr)", css)
        self.assertIn('id="startAssignmentClose"', html)
        self.assertIn('id="diagnosticsMqttStatus"', html)
        self.assertNotIn('class="hint-template-heading"', javascript)
        self.assertNotIn(".dashboard-preparation-mode .control-workspace", css)
        self.assertIn("grid-template-columns: minmax(45px, 1fr) 92px;", css)
        self.assertIn('font: 19px/1.35 Inter, "Segoe UI", Arial, sans-serif;', css)
        self.assertIn('aria-controls="${escapeAttr(editorId)}" aria-expanded="false"', javascript)
        self.assertIn('aria-pressed="false"', javascript)
        self.assertIn("editor.addEventListener('keydown'", javascript)

    def test_preview_dashboard_gets_a_distinct_default_mqtt_client_id(self) -> None:
        helper = extracted("_default_dashboard_mqtt_client_id")["_default_dashboard_mqtt_client_id"]
        self.assertEqual(helper(8080), "er1_dashboard")
        self.assertEqual(helper(8081), "er1_dashboard_8081")
        self.assertNotEqual(helper(8080), helper(8081))
        self.assertNotIn("\nER1_DASHBOARD_CLIENT_ID=", ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_paramiko_rejects_unknown_website_host_keys(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_paramiko_connect"
        )
        function_source = ast.get_source_segment(source, function)
        self.assertIn("client.load_system_host_keys()", function_source)
        self.assertIn("paramiko.RejectPolicy()", function_source)
        self.assertNotIn("AutoAddPolicy", function_source)

    def test_game_viewer_places_editable_details_below_selected_record(self) -> None:
        javascript = GAME_VIEWER_JS_PATH.read_text(encoding="utf-8")
        html = GAME_VIEWER_HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("selected-game-row", html)
        self.assertIn("function placeGameDetailsBelowSelectedRow()", javascript)
        self.assertIn("selectedRow.insertAdjacentElement('afterend', detailsRow)", javascript)
        self.assertIn("detailsRow.innerHTML = '<td colspan=\"9\"", javascript)
        self.assertGreaterEqual(javascript.count("placeGameDetailsBelowSelectedRow();"), 2)
        for section_id in ("gameSummarySection", "riddlesSection", "rawDbSection"):
            self.assertIn(section_id, javascript)


if __name__ == "__main__":
    unittest.main()
