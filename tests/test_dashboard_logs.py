from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import threading
import time
import types
import unittest
import uuid
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "web" / "er1_dashboard" / "app.py"
JS_PATH = ROOT / "web" / "er1_dashboard" / "static" / "app.js"
HTML_PATH = ROOT / "web" / "er1_dashboard" / "templates" / "index.html"

PRODUCTION_CONSTANTS = {
    "TOPIC_LIGHTING_CMD",
    "TOPIC_MAGLOCK_CMD",
    "TOPIC_STAR_SKY_CMD",
    "LOCKS",
    "LIGHT_GROUPS",
    "LOG_NODE_LABELS",
    "LOG_NODE_IDS",
    "LOG_NODE_ID_SET",
    "LOG_LEVELS",
    "LOG_LEVEL_SET",
    "LOG_STREAM_ID",
    "MAX_LOG_PAYLOAD_BYTES",
    "MAX_LOG_MESSAGE_CHARS",
    "MAX_LOG_DETAIL_STRING_CHARS",
    "MAX_LOG_DETAIL_KEY_CHARS",
    "MAX_LOG_DETAIL_ITEMS",
    "MAX_LOG_DETAIL_DEPTH",
    "MAX_LOG_DETAIL_NODES",
    "LOG_BUFFER_CAPACITY",
    "LOG_API_DEFAULT_LIMIT",
    "LOG_API_MAX_LIMIT",
}


def extracted_dashboard(*names: str) -> dict[str, Any]:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    wanted = set(names)
    namespace: dict[str, Any] = {
        "Any": Any,
        "datetime": datetime,
        "deque": deque,
        "json": json,
        "math": math,
        "re": re,
        "threading": threading,
        "time": time,
        "timezone": timezone,
        "uuid": uuid,
    }
    found: set[str] = set()
    for original in tree.body:
        node: ast.stmt | None = None
        if isinstance(original, ast.Assign):
            targets = {target.id for target in original.targets if isinstance(target, ast.Name)}
            if targets & PRODUCTION_CONSTANTS:
                node = original
        elif isinstance(original, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and original.name in wanted:
            node = original
            found.add(original.name)
        if node is None:
            continue
        node = ast.fix_missing_locations(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP_PATH), "exec"), namespace)
    missing = wanted - found
    if missing:
        raise AssertionError(f"dashboard definitions missing: {sorted(missing)}")
    return namespace


def extracted_dashboard_method(class_name: str, method_name: str) -> tuple[Any, dict[str, Any]]:
    namespace = extracted_dashboard()
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_PATH))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(
        node for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    method.decorator_list = []
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(APP_PATH), "exec"),
        namespace,
    )
    return namespace[method_name], namespace


class QueryArgs:
    def __init__(self, **values: str | list[str]) -> None:
        self.values = {key: value if isinstance(value, list) else [value] for key, value in values.items()}

    def keys(self):
        return self.values.keys()

    def getlist(self, key: str) -> list[str]:
        return list(self.values.get(key, []))


class IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.diagnostics_attrs: dict[str, str | None] | None = None
        self.log_attrs: dict[str, str | None] | None = None
        self.current_log_node: str | None = None
        self.log_controls: dict[str, list[tuple[str, dict[str, str | None]]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "details" and values.get("id") == "diagnosticsDetails":
            self.diagnostics_attrs = values
        if tag == "div" and "diagnostics-level-row" in str(values.get("class") or "").split():
            self.current_log_node = str(values.get("data-log-level-node") or "")
            self.log_controls[self.current_log_node] = []
        elif self.current_log_node and tag in {"select", "button"}:
            self.log_controls[self.current_log_node].append((tag, values))
        if tag == "div" and values.get("id") == "diagnosticsLogList":
            self.log_attrs = values

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.current_log_node:
            self.current_log_node = None


class DashboardLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helpers = extracted_dashboard(
            "_bounded_log_text",
            "_sanitize_log_detail",
            "_sanitize_log_scalar",
            "_reject_log_json_constant",
            "parse_node_log",
            "NodeLogBuffer",
            "_query_arg_values",
            "_parse_log_filter",
            "parse_logs_query",
            "validate_log_level_request",
        )

    def payload(self, **updates: Any) -> bytes:
        data = {
            "t": "log",
            "ts": "2026-09-05T12:34:56Z",
            "time_valid": True,
            "lv": "INF",
            "msg": "ready",
            "d": {"pin": 4},
        }
        data.update(updates)
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def test_log_parser_rejects_unknown_malformed_and_oversized_payloads(self) -> None:
        parse_log = self.helpers["parse_node_log"]
        self.assertIsNone(parse_log("unknown/log", self.payload()))
        self.assertIsNone(parse_log("lighting/state", self.payload()))
        self.assertIsNone(parse_log("lighting/log", b"{broken"))
        self.assertIsNone(parse_log("lighting/log", b"\xff"))
        self.assertIsNone(parse_log("lighting/log", b'{"lv":"INF","msg":"x","d":{"n":NaN}}'))
        self.assertIsNone(parse_log("lighting/log", self.payload(lv="TRACE")))
        self.assertIsNone(parse_log("lighting/log", b"x" * (self.helpers["MAX_LOG_PAYLOAD_BYTES"] + 1)))

        parsed = parse_log("lighting/log", self.payload())
        self.assertEqual(parsed["node"], "lighting")
        self.assertEqual(
            {key: parsed[key] for key in ("t", "ts", "time_valid", "lv", "msg", "d")},
            {
                "t": "log",
                "ts": "2026-09-05T12:34:56Z",
                "time_valid": True,
                "lv": "INF",
                "msg": "ready",
                "d": {"pin": 4},
            },
        )

        warning = parse_log(
            "lighting/log",
            self.payload(lv="WRN", msg="payload parse warning", d="unexpected token at byte 7", d_type="string"),
        )
        self.assertEqual(warning["lv"], "WRN")
        self.assertEqual(warning["msg"], "payload parse warning")
        self.assertEqual(warning["d"], "unexpected token at byte 7")
        self.assertEqual(warning["d_type"], "string")
        self.assertEqual(parse_log("lighting/log", self.payload(d=["a", {"nested": True}]))["d"], ["a", {"nested": True}])
        bounded_scalar = parse_log(
            "lighting/log",
            self.payload(d="x" * (self.helpers["MAX_LOG_DETAIL_STRING_CHARS"] + 100), d_type="string"),
        )["d"]
        self.assertEqual(len(bounded_scalar), self.helpers["MAX_LOG_DETAIL_STRING_CHARS"])

    def test_recursive_sanitizer_bounds_data_and_preserves_xss_as_text(self) -> None:
        xss = '<img src=x onerror="alert(1)"><script>bad()</script>'
        nested: dict[str, Any] = {"text": xss, "items": list(range(100))}
        cursor = nested
        for _index in range(12):
            cursor["child"] = {}
            cursor = cursor["child"]
        parsed = self.helpers["parse_node_log"]("chess/log", self.payload(msg=xss, d=nested))
        self.assertEqual(parsed["msg"], xss)
        self.assertEqual(parsed["d"]["text"], xss)
        self.assertLessEqual(len(parsed["d"]["items"]), self.helpers["MAX_LOG_DETAIL_ITEMS"])
        self.assertIn("[max depth]", json.dumps(parsed["d"]))

        long_message = "m" * (self.helpers["MAX_LOG_MESSAGE_CHARS"] + 200)
        parsed = self.helpers["parse_node_log"]("chess/log", self.payload(msg=long_message))
        self.assertEqual(len(parsed["msg"]), self.helpers["MAX_LOG_MESSAGE_CHARS"])
        self.assertTrue(parsed["msg"].endswith("..."))

    def test_ring_rollover_gap_cursor_filters_and_limit(self) -> None:
        buffer = self.helpers["NodeLogBuffer"](3)
        base = {"t": "log", "ts": None, "time_valid": None, "msg": "event", "d": {}}
        for index, (node, level) in enumerate([
            ("lighting", "INF"),
            ("chess", "ERR"),
            ("lighting", "ERR"),
            ("chess", "DBG"),
        ]):
            buffer.append({**base, "node": node, "lv": level}, received_at=1_700_000_000 + index)

        first = buffer.query(after=0, limit=2)
        self.assertTrue(first["reset"])
        self.assertEqual(first["gap_reason"], "buffer_rollover")
        self.assertEqual(first["dropped"], 1)
        self.assertEqual([entry["seq"] for entry in first["entries"]], [2, 3])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_after"], 3)

        second = buffer.query(after=first["next_after"], limit=2)
        self.assertFalse(second["gap"])
        self.assertEqual([entry["seq"] for entry in second["entries"]], [4])
        self.assertEqual(second["next_after"], 4)

        filtered = buffer.query(after=1, nodes={"lighting"}, levels={"ERR"}, limit=10)
        self.assertEqual([entry["seq"] for entry in filtered["entries"]], [3])
        self.assertEqual(filtered["next_after"], 4)
        self.assertEqual(buffer.query(after=99, limit=10)["gap_reason"], "cursor_ahead")

    def test_new_process_stream_disambiguates_the_same_sequence_cursor(self) -> None:
        other = extracted_dashboard(
            "_bounded_log_text",
            "_sanitize_log_detail",
            "_sanitize_log_scalar",
            "_reject_log_json_constant",
            "parse_node_log",
            "NodeLogBuffer",
        )
        entry = {"node": "lighting", "t": "log", "ts": None, "time_valid": None, "lv": "INF", "msg": "x", "d": {}}
        old_buffer = self.helpers["NodeLogBuffer"](3)
        new_buffer = other["NodeLogBuffer"](3)
        old_buffer.append(entry, received_at=1_700_000_000)
        new_buffer.append(entry, received_at=1_700_000_001)

        old_response = old_buffer.query(after=0, limit=3)
        stale_cursor_response = new_buffer.query(after=old_response["next_after"], limit=3)
        self.assertEqual(old_response["next_after"], stale_cursor_response["newest_seq"])
        self.assertEqual(stale_cursor_response["entries"], [])
        self.assertNotEqual(old_response["stream_id"], stale_cursor_response["stream_id"])
        self.assertEqual([row["seq"] for row in new_buffer.query(after=0, limit=3)["entries"]], [1])

    def test_query_and_log_level_validation_are_strict(self) -> None:
        parse_query = self.helpers["parse_logs_query"]
        parsed = parse_query(QueryArgs(
            after="12",
            limit="25",
            nodes=["lighting,chess", "stop_timer"],
            levels=["ERR", "WRN,INF"],
        ))
        self.assertEqual(parsed["after"], 12)
        self.assertEqual(parsed["limit"], 25)
        self.assertEqual(parsed["nodes"], {"lighting", "chess", "stop_timer"})
        self.assertEqual(parsed["levels"], {"ERR", "WRN", "INF"})

        for args in (
            QueryArgs(after="-1"),
            QueryArgs(after="01"),
            QueryArgs(limit="0"),
            QueryArgs(limit="501"),
            QueryArgs(nodes="lighting,unknown"),
            QueryArgs(levels="INFO"),
            QueryArgs(extra="1"),
        ):
            with self.subTest(values=args.values), self.assertRaises(ValueError):
                parse_query(args)

        validate = self.helpers["validate_log_level_request"]
        self.assertEqual(validate({"node": "images_piano", "level": "DBG"}), ("images_piano", "DBG"))
        for body in (
            None,
            {"node": "images", "level": "DBG"},
            {"node": "lighting", "level": "debug"},
            {"node": "lighting", "level": "ERR", "extra": True},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate(body)


class DashboardLightSummaryTests(unittest.TestCase):
    def test_multi_light_summary_distinguishes_mixed_and_partial_state(self) -> None:
        summarize, _namespace = extracted_dashboard_method("DashboardStore", "_build_light_summary")
        mixed = summarize(None, {
            "r1_stuen": {"on": True, "pct": 100},
            "r1_bild": {"on": False, "pct": 0},
        }, {})
        r1 = next(row for row in mixed if row["id"] == "r1")
        self.assertEqual(r1["state"], "mixed")
        self.assertEqual(r1["state_label"], "gemischt")
        self.assertTrue(r1["mixed"])
        self.assertFalse(r1["on"])
        self.assertTrue(r1["any_on"])
        self.assertEqual(r1["known_count"], 2)

        partial = summarize(None, {"r1_stuen": {"on": True, "pct": 100}}, {})
        r1 = next(row for row in partial if row["id"] == "r1")
        self.assertEqual(r1["state"], "partial")
        self.assertTrue(r1["partial"])
        self.assertEqual(r1["known_count"], 1)
        self.assertEqual(r1["component_count"], 2)
        javascript = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("light.state_label", javascript)
        self.assertIn("mixed: 'is-mixed'", javascript)


class DashboardMqttRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = extracted_dashboard(
            "_query_arg_values",
            "_parse_log_filter",
            "parse_logs_query",
            "validate_log_level_request",
            "_publish_requested_log_level",
            "mqtt_is_connected",
            "mqtt_publish",
            "mqtt_publish_batch",
            "api_logs",
            "api_log_level",
            "api_lock",
            "api_light",
        )

        class JsonResponse(dict):
            def __init__(self, payload: dict[str, Any]) -> None:
                super().__init__(payload)
                self.headers: dict[str, str] = {}

        self.namespace["jsonify"] = JsonResponse

    def set_request(self, body: Any) -> None:
        self.namespace["request"] = types.SimpleNamespace(get_json=lambda **_kwargs: body)

    def test_mqtt_publish_checks_connection_and_queue_result(self) -> None:
        publish = self.namespace["mqtt_publish"]
        calls: list[tuple[Any, ...]] = []

        class Client:
            connected = False
            rc = 0

            def is_connected(self) -> bool:
                return self.connected

            def publish(self, *args: Any, **kwargs: Any):
                calls.append((*args, kwargs))
                return types.SimpleNamespace(rc=self.rc)

        client = Client()
        self.namespace.update({"mqtt_client": client, "mqtt": types.SimpleNamespace(MQTT_ERR_SUCCESS=0)})
        self.assertFalse(publish("lighting/log/level", "ERR"))
        self.assertEqual(calls, [])
        client.connected = True
        client.rc = 4
        self.assertFalse(publish("lighting/log/level", "ERR"))
        client.rc = 0
        self.assertTrue(publish("lighting/log/level", "ERR"))
        self.assertEqual(calls[-1][0:2], ("lighting/log/level", "ERR"))
        self.assertEqual(calls[-1][-1], {"qos": 0, "retain": False})

    def test_log_level_endpoint_validates_and_returns_requested_not_applied(self) -> None:
        requests: list[tuple[str, str]] = []
        self.namespace["_publish_requested_log_level"] = lambda node, level: requests.append((node, level)) or None
        self.set_request({"node": "images_piano", "level": "DBG"})
        response, status = self.namespace["api_log_level"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertFalse(response["mqtt_queued"])
        self.assertFalse(response["applied"])

        self.set_request({"node": "images", "level": "DBG"})
        response, status = self.namespace["api_log_level"]()
        self.assertEqual(status, 400)
        self.assertFalse(response["ok"])

        self.namespace["_publish_requested_log_level"] = lambda node, level: requests.append((node, level)) or {
            "level": level,
            "requested_at": "2026-09-05T12:00:00Z",
        }
        self.set_request({"node": "images_piano", "level": "ERR"})
        response = self.namespace["api_log_level"]()
        self.assertEqual(requests[-1], ("images_piano", "ERR"))
        self.assertEqual(response["requested"], "ERR")
        self.assertFalse(response["applied"])
        self.assertFalse(response["retained"])

    def test_logs_endpoint_rejects_unknown_filters(self) -> None:
        self.namespace["request"] = types.SimpleNamespace(args=QueryArgs(nodes="lighting,not_physical"))
        response, status = self.namespace["api_logs"]()
        self.assertEqual(status, 400)
        self.assertFalse(response["ok"])

    def test_logs_endpoint_reports_stream_and_disconnected_mqtt(self) -> None:
        result = {
            "entries": [],
            "stream_id": "new-process-stream",
            "next_after": 7,
            "oldest_seq": 1,
            "newest_seq": 7,
            "reset": False,
            "gap": False,
            "gap_reason": None,
            "dropped": 0,
            "buffer_size": 7,
            "buffer_capacity": 1500,
            "has_more": False,
        }
        self.namespace.update({
            "request": types.SimpleNamespace(args=QueryArgs(after="7")),
            "node_log_buffer": types.SimpleNamespace(query=lambda **_query: dict(result)),
            "_requested_log_levels_snapshot": lambda: {},
            "mqtt_is_connected": lambda: False,
        })
        response = self.namespace["api_logs"]()
        self.assertEqual(response["stream_id"], "new-process-stream")
        self.assertFalse(response["mqtt_connected"])
        self.assertEqual(response.headers["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")

    def test_lock_and_multi_command_light_failures_return_503(self) -> None:
        calls: list[tuple[str, Any]] = []
        self.namespace["mqtt_publish"] = lambda topic, payload: calls.append((topic, payload)) or False
        self.set_request({"lock": "r2", "action": "open"})
        response, status = self.namespace["api_lock"]()
        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertEqual(response["command_count"], 1)
        self.assertFalse(response["applied"])

        outcomes = iter([True, False])
        calls.clear()
        self.namespace["mqtt_publish"] = lambda topic, payload: calls.append((topic, payload)) or next(outcomes)
        self.set_request({"group": "r1", "action": "on"})
        response, status = self.namespace["api_light"]()
        self.assertEqual(status, 503)
        self.assertEqual(len(calls), 2)
        self.assertEqual(response["queued_count"], 1)
        self.assertTrue(response["partial"])
        self.assertFalse(response["applied"])

        outcomes = iter([True, False, True])
        calls.clear()
        self.namespace["mqtt_publish"] = lambda topic, payload: calls.append((topic, payload)) or next(outcomes)
        self.set_request({"group": "star_sky", "action": "on"})
        response, status = self.namespace["api_light"]()
        self.assertEqual(status, 503)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1], ("star_sky/sys/cmd", "SOLVE"))
        self.assertTrue(response["partial"])


class DashboardLogLevelConcurrencyTests(unittest.TestCase):
    def test_same_node_publish_and_record_follow_queue_order_while_other_nodes_proceed(self) -> None:
        namespace = extracted_dashboard("_publish_requested_log_level")
        namespace["_log_level_publish_locks"] = {
            node: threading.Lock() for node in namespace["LOG_NODE_IDS"]
        }
        events: list[tuple[str, str, str]] = []
        first_publish = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        errors: list[BaseException] = []

        def publish(topic: str, level: str) -> bool:
            events.append(("publish", topic, level))
            if level == "DBG":
                first_publish.set()
                if not release_first.wait(2):
                    raise AssertionError("first publish release timed out")
            return True

        def remember(node: str, level: str) -> dict[str, str]:
            events.append(("record", node, level))
            return {"level": level, "requested_at": level}

        namespace.update({"mqtt_publish": publish, "_remember_requested_log_level": remember})

        def request(level: str, started: threading.Event | None = None) -> None:
            try:
                if started:
                    started.set()
                namespace["_publish_requested_log_level"]("lighting", level)
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=request, args=("DBG",))
        second = threading.Thread(target=request, args=("ERR", second_started))
        first.start()
        self.assertTrue(first_publish.wait(1))
        second.start()
        self.assertTrue(second_started.wait(1))
        try:
            time.sleep(0.05)
            self.assertEqual(events, [("publish", "lighting/log/level", "DBG")])
        finally:
            release_first.set()
            first.join(2)
            second.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(events, [
            ("publish", "lighting/log/level", "DBG"),
            ("record", "lighting", "DBG"),
            ("publish", "lighting/log/level", "ERR"),
            ("record", "lighting", "ERR"),
        ])

        lighting_entered = threading.Event()
        release_lighting = threading.Event()
        chess_done = threading.Event()
        namespace["_log_level_publish_locks"] = {
            node: threading.Lock() for node in namespace["LOG_NODE_IDS"]
        }

        def parallel_publish(topic: str, _level: str) -> bool:
            if topic == "lighting/log/level":
                lighting_entered.set()
                if not release_lighting.wait(2):
                    raise AssertionError("lighting release timed out")
            return True

        namespace.update({"mqtt_publish": parallel_publish, "_remember_requested_log_level": remember})
        blocked = threading.Thread(target=lambda: namespace["_publish_requested_log_level"]("lighting", "INF"))
        other = threading.Thread(target=lambda: (namespace["_publish_requested_log_level"]("chess", "WRN"), chess_done.set()))
        blocked.start()
        self.assertTrue(lighting_entered.wait(1))
        other.start()
        try:
            self.assertTrue(chess_done.wait(1), "a different node was blocked by the lighting request")
        finally:
            release_lighting.set()
            blocked.join(2)
            other.join(2)


class DashboardDiagnosticsSourceTests(unittest.TestCase):
    def test_log_subscription_precedes_generic_flattening_without_dbg_noise(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        connect_source = ast.get_source_segment(source, functions["on_connect"])
        handler_source = ast.get_source_segment(source, functions["_handle_mqtt_message"])
        self.assertIn('("+/log", 0)', connect_source)
        self.assertNotIn("+/dbg", connect_source)
        self.assertLess(handler_source.index("parse_node_log"), handler_source.index("parse_json_payload"))

    def test_diagnostics_poll_is_incremental_text_only_and_backlog_is_yielding(self) -> None:
        source = JS_PATH.read_text(encoding="utf-8")

        def function_source(name: str) -> str:
            start = source.index(f"function {name}(")
            next_start = source.find("\nfunction ", start + 1)
            return source[start:] if next_start < 0 else source[start:next_start]

        can_poll = function_source("diagnosticsCanPoll")
        poll = function_source("pollDiagnosticsLogs")
        create = function_source("createDiagnosticsLogEntry")
        append = function_source("appendDiagnosticsLogs")
        replace = function_source("replaceDiagnosticsLogs")
        delay = function_source("diagnosticsPollDelay")
        self.assertIn("details?.open", can_poll)
        self.assertIn("document.visibilityState === 'visible'", can_poll)
        self.assertLess(poll.index("diagnosticsCanPoll()"), poll.index("api(`/api/logs?"))
        self.assertNotIn("innerHTML", create + append + replace)
        self.assertIn("textContent", create)
        self.assertIn("appendChild", append)
        self.assertIn("firstElementChild?.remove()", append)
        self.assertNotIn("replaceChildren", append)
        self.assertIn("replaceChildren", replace)
        self.assertIn("else appendDiagnosticsLogs(incoming)", poll)
        self.assertIn("DIAGNOSTICS_MAX_ENTRIES = 500", source)
        self.assertIn("DIAGNOSTICS_BACKLOG_DELAY_MS = 75", source)
        self.assertIn("DIAGNOSTICS_BACKLOG_PAGE_BUDGET = 4", source)
        self.assertIn("return DIAGNOSTICS_BACKLOG_DELAY_MS", delay)
        self.assertIn("nextDelay = diagnosticsPollDelay(Boolean(data.has_more))", poll)

    def test_stream_change_resets_stale_cursor_and_disconnected_status_is_explicit(self) -> None:
        source = JS_PATH.read_text(encoding="utf-8")

        def function_source(name: str) -> str:
            start = source.index(f"function {name}(")
            next_start = source.find("\nfunction ", start + 1)
            return source[start:] if next_start < 0 else source[start:next_start]

        stream = function_source("updateDiagnosticsStream")
        poll = function_source("pollDiagnosticsLogs")
        status = function_source("diagnosticsBufferStatus")
        self.assertIn("diagnostics.streamId !== nextStreamId", stream)
        stream_branch = poll[poll.index("if (streamChanged)"):poll.index("let resetDisplay")]
        self.assertIn("diagnostics.after = 0", stream_branch)
        self.assertIn("diagnostics.entries = []", stream_branch)
        self.assertIn("replaceDiagnosticsLogs()", stream_branch)
        self.assertIn("return;", stream_branch)
        self.assertIn("MQTT getrennt; Pufferanzeige", status)
        self.assertNotIn("Empfang aktiv", source)

    def test_dirty_log_level_draft_survives_blur_and_hydration_until_success(self) -> None:
        source = JS_PATH.read_text(encoding="utf-8")

        def function_source(name: str) -> str:
            start = source.index(f"function {name}(")
            if source[start - 6:start] == "async ":
                start -= 6
            next_start = source.find("\nfunction ", start + 1)
            return source[start:] if next_start < 0 else source[start:next_start]

        apply_levels = function_source("applyLastRequestedLogLevels")
        request_level = function_source("requestDiagnosticLogLevel")
        wire = function_source("wireDiagnostics")
        self.assertIn("logLevelDrafts: new Map()", source)
        self.assertIn("!diagnostics.logLevelDrafts.has(node)", apply_levels)
        self.assertNotIn("logLevelDrafts.delete", apply_levels)
        self.assertIn("diagnostics.logLevelDrafts.set(node, select.value)", wire)
        self.assertIn("const level = submittedDraft ?? select?.value", request_level)

        script = f"""
const diagnostics = {{ logLevelDrafts: new Map() }};
const select = {{ value: 'ERR' }};
const status = {{ textContent: '' }};
const row = {{
  dataset: {{ logLevelNode: 'lighting' }},
  querySelector(selector) {{ return selector.includes('select') ? select : status; }},
}};
const document = {{
  activeElement: null,
  querySelectorAll() {{ return [row]; }},
}};
const DIAGNOSTICS_BACKLOG_DELAY_MS = 75;
const button = {{ disabled: false }};
let requestBody = null;
let shouldFail = false;
async function api(_url, options) {{
  requestBody = JSON.parse(options.body);
  if (shouldFail) throw new Error('queue failed');
  return {{ requested: requestBody.level, requested_at: '2026-09-05T12:00:00Z' }};
}}
function formatDiagnosticsTimestamp(value) {{ return value; }}
function setDiagnosticsStatus() {{}}
function scheduleDiagnosticsPoll() {{}}
{apply_levels}
{request_level}
(async () => {{
  diagnostics.logLevelDrafts.set('lighting', 'ERR');
  applyLastRequestedLogLevels({{ lighting: {{ level: 'INF', requested_at: '' }} }});
  if (select.value !== 'ERR') throw new Error('hydration overwrote the dirty draft after blur');
  await requestDiagnosticLogLevel(row, button);
  if (requestBody.level !== 'ERR') throw new Error('request did not read the dirty draft');
  if (diagnostics.logLevelDrafts.has('lighting')) throw new Error('successful request did not clear the draft');

  select.value = 'WRN';
  diagnostics.logLevelDrafts.set('lighting', 'WRN');
  shouldFail = true;
  await requestDiagnosticLogLevel(row, button);
  applyLastRequestedLogLevels({{ lighting: {{ level: 'INF', requested_at: '' }} }});
  if (diagnostics.logLevelDrafts.get('lighting') !== 'WRN' || select.value !== 'WRN') {{
    throw new Error('failed request or hydration cleared the dirty draft');
  }}
}})().catch((error) => {{ console.error(error.message); process.exitCode = 1; }});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_batch_feedback_retains_counts_and_no_ack_claim(self) -> None:
        source = JS_PATH.read_text(encoding="utf-8")
        self.assertIn("data.queued_count", source)
        self.assertIn("data.command_count", source)
        self.assertIn("keine Rücknahme und keine Gerätebestätigung", source)

    def test_native_panel_is_closed_by_default_and_html_ids_are_unique(self) -> None:
        parser = IdParser()
        parser.feed(HTML_PATH.read_text(encoding="utf-8"))
        self.assertIsNotNone(parser.diagnostics_attrs)
        self.assertNotIn("open", parser.diagnostics_attrs)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(parser.log_attrs.get("aria-relevant"), "additions")
        self.assertEqual(set(parser.log_controls), {
            "lighting", "maglock", "images_piano", "chess", "knocking",
            "candles", "star_slider", "star_sky", "stop_timer",
        })
        for node, controls in parser.log_controls.items():
            self.assertEqual([tag for tag, _attrs in controls], ["select", "button"], node)
            for _tag, attrs in controls:
                self.assertIn("Log-Stufe für", str(attrs.get("aria-label") or ""), node)
        javascript = JS_PATH.read_text(encoding="utf-8")
        referenced_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)", javascript))
        self.assertEqual(sorted(referenced_ids - set(parser.ids)), [])


if __name__ == "__main__":
    unittest.main()
