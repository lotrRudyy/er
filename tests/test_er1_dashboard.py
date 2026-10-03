from __future__ import annotations

import ast
import copy
import json
import math
import os
import re
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "web" / "er1_dashboard" / "app.py"
JS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "app.js"
CSS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "style.css"
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
        "timezone": timezone,
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(APP_PATH), "exec"),
        namespace,
    )
    return namespace[method_name], namespace


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
            return {"id": identifier, "kind": "booking", "date": "2026-10-01", "slot": slot, "players": 2}

        self.assertEqual(nearest([booking("minus", "11:30")], reference)["id"], "minus")
        self.assertEqual(nearest([booking("plus", "12:30")], reference)["id"], "plus")
        self.assertIsNone(nearest([booking("too-early", "11:29")], reference))
        self.assertIsNone(nearest([booking("too-late", "12:31")], reference))
        self.assertEqual(
            nearest([booking("far", "14:00"), booking("inside", "12:29")], reference)["id"],
            "inside",
        )

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
                "candles", "star_slider", "star_sky", "stop_timer",
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
        self.assertEqual(response["queued_count"], 9)
        self.assertEqual(len(published), 10)
        self.assertTrue(all(topic.endswith("/sys/cmd") and payload == "REBOOT" for topic, payload in published))

        store.game_state["phase"] = 3
        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "all"})
        response, status = namespace["api_node_reboot"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(len(published), 10)

        namespace["request"] = types.SimpleNamespace(get_json=lambda force: {"node": "maglock"})
        response, status = namespace["api_node_reboot"]()
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(len(published), 10)

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

    def test_riddle_time_rejects_zero_and_negative_before_mqtt(self) -> None:
        namespace = extracted("parse_mmss_input", "api_riddle_time")
        published: list[tuple[str, Any]] = []
        namespace.update({
            "jsonify": JsonResponse,
            "_stale_action_response": lambda _data: None,
            "_canonical_riddle_name": lambda value: str(value),
            "store": types.SimpleNamespace(snapshot=lambda: {"riddles": [{"id": "images", "phase_state": "active"}]}),
            "TOPIC_GAME_CMD": "game/cmd",
            "mqtt_publish": lambda topic, payload: published.append((topic, payload)) or True,
        })
        for value in ("0", "-1", "00:00"):
            namespace["request"] = types.SimpleNamespace(
                get_json=lambda force, value=value: {"riddle": "images", "time_text": value}
            )
            response, status = namespace["api_riddle_time"]()
            self.assertEqual(status, 400)
            self.assertIn("mindestens eine Sekunde", response["error"])
        self.assertEqual(published, [])

        namespace["request"] = types.SimpleNamespace(
            get_json=lambda force: {
                "riddle": "images",
                "time_text": "1",
                "expected_phase": 10,
                "expected_run_id": "run-10",
            }
        )
        response = namespace["api_riddle_time"]()
        self.assertEqual(response["solve_time_s"], 1.0)
        self.assertEqual(published[-1][1]["solve_time_s"], 1.0)
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
            "HINT_TEMPLATE_VERSION": 1,
            "MAX_HINT_TIPS_PER_LANGUAGE": 20,
            "MAX_HINT_TIP_CHARS": 2000,
            "MAX_HINT_TEMPLATE_BYTES": 96 * 1024,
            "_hint_templates_lock": threading.RLock(),
            "_hint_templates": {},
        })
        return namespace

    def test_canonical_texts_and_neutral_empty_templates_are_server_owned(self) -> None:
        templates = self.defaults["templates"]
        self.assertEqual(
            templates["images"]["de"][0],
            "Fällt euch irgendetwas Ungewöhnliches im Raum auf? Was könnte man damit machen?",
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
        oversized["templates"]["images"]["de"] = ["x" * 2001]
        with self.assertRaisesRegex(ValueError, "länger als 2000"):
            namespace["save_hint_templates"](oversized)
        self.assertEqual(len(writes), 1)

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


class DashboardSourceContractTests(unittest.TestCase):
    def test_custom_confirmation_and_action_contracts(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        html = HTML_PATH.read_text(encoding="utf-8")
        self.assertNotIn("window.confirm", javascript)
        self.assertIn('id="confirmDialog"', html)
        self.assertIn("function confirmAction", javascript)
        self.assertIn("window.setTimeout(() => confirmButton.focus(), 0)", javascript)
        self.assertIn("event.key === 'Enter'", javascript)
        self.assertIn("event.key === 'Escape'", javascript)
        self.assertNotIn("bookingConfirmDialog", javascript + html)
        self.assertIn(">Gelöst</button>", javascript)
        self.assertIn(">Überspringen</button>", javascript)
        self.assertIn("min=\"1\"", javascript)
        self.assertIn(">OK</button>", javascript)
        self.assertNotIn("resetRiddle", javascript)
        self.assertNotIn("outcome: 'reset'", javascript)

        solve_start = javascript.index("async function solveRiddle")
        solve_end = javascript.index("async function toggleSkip", solve_start)
        start_start = javascript.index("async function handleStart")
        start_end = javascript.index("async function handlePhaseAction", start_start)
        time_start = javascript.index("async function saveRiddleTime")
        time_end = javascript.index("async function changeHint", time_start)
        self.assertIn("confirmAction", javascript[solve_start:solve_end])
        self.assertIn("confirmAction", javascript[start_start:start_end])
        self.assertNotIn("confirmAction", javascript[time_start:time_end])

    def test_backend_routes_have_stale_guards_and_no_reset_or_clear_outcomes(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        guarded = {
            "api_phase", "api_select_booking", "api_solve", "api_lock", "api_light",
            "api_node_reboot", "api_service_restart", "api_set_hint_count", "api_riddle_time", "api_riddle_outcome",
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
        self.assertIn("slot.expected", javascript)
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

    def test_maintenance_exposes_solve_but_not_run_mutations(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("function riddleMutationsAvailable()", javascript)
        self.assertIn("Number(state.game.phase || 0) >= 3 && Boolean(currentRunId())", javascript)
        actions_start = javascript.index("function riddleActionsHtml")
        actions_end = javascript.index("async function solveRiddle", actions_start)
        actions_source = javascript[actions_start:actions_end]
        self.assertIn("riddleMutationsAvailable()", actions_source)
        self.assertIn(">Gelöst</button>", actions_source)
        self.assertIn("canSkip ?", actions_source)

    def test_control_room_layout_contract_is_present(self) -> None:
        html = HTML_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        css = CSS_PATH.read_text(encoding="utf-8")

        self.assertIn('class="page dashboard-page dashboard-v5"', html)
        self.assertLess(html.index('id="nodeStatusBar"'), html.index('class="dashboard-summary-grid"'))
        self.assertLess(html.index('class="dashboard-summary-grid"'), html.index('id="currentRiddlesPanel"'))
        for action in ("standby", "maintenance", "prepare"):
            self.assertIn(f'data-phase-action="{action}"', html)
        self.assertIn('id="startGameBtn"', html)
        for panel_id in ("bookingDetails", "allRiddlesPanel", "diagnosticsDetails", "maintenanceHintEditor"):
            self.assertIn(f'id="{panel_id}"', html)

        self.assertNotIn("emergencyOpen", javascript)
        self.assertIn("function isGameMode()", javascript)
        self.assertIn("Number(state.game.phase || 0) >= 2", javascript)
        self.assertIn("for (const panel of [bookingDetails, allRiddles, diagnosticsDetails, hintEditor])", javascript)
        self.assertIn("if (panel) panel.open = false", javascript)
        self.assertNotIn('id="hintTemplateDialog"', html)
        self.assertNotIn("openHintTemplateDialog", javascript)
        self.assertIn('data-service-restart="game_master"', html)
        self.assertIn('data-service-restart="dashboard"', html)
        self.assertIn('class="database-viewer-button"', html)
        self.assertIn("href=\"{{ url_for('game_viewer') }}\"", html)
        restart_start = javascript.index("async function restartSystemService")
        restart_end = javascript.index("async function", restart_start + 20)
        self.assertIn("confirmAction", javascript[restart_start:restart_end])
        self.assertIn("item.id !== 'stop_timer'", javascript)

        self.assertNotIn("/* Fixed operator console:", css)
        control_room_css = css[css.index("/* Control-room dashboard */"):]
        self.assertIn("grid-template-columns: repeat(8, minmax(0, 1fr));", control_room_css)
        self.assertIn(".dashboard-game-mode #bookingDetails:not([open])", control_room_css)
        self.assertIn("@media (min-width: 1600px) and (max-height: 980px)", control_room_css)
        self.assertIn("@media (max-width: 760px)", control_room_css)

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
