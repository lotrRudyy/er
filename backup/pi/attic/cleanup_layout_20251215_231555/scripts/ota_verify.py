#!/usr/bin/env python3
from __future__ import annotations
"""
OTA verification watcher.

Subscribes to +/cmd and +/hb to track UPDATE commands.
Emits OTA_RESULT lines once success/failure is determined.

Also publishes authoritative Pi time to time/state topic for node clock sync.

Dependencies:
  pip install paho-mqtt
"""
import json
import os
import signal
import sys
import time
from pathlib import Path
from datetime import datetime

BROKER = os.getenv("LOCAL_BROKER", "127.0.0.1")
PORT = int(os.getenv("LOCAL_BROKER_PORT", "1883"))
VERIFY_WINDOW = int(os.getenv("OTA_VERIFY_WINDOW", "90"))
LOG_DIR = Path(os.getenv("ER1_LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))
LOG_FILE = Path(os.getenv("OTA_VERIFY_LOG", str(LOG_DIR / "ota-verify.log")))
# Canonical deployment map (must mirror PC tooling)
DEPLOYMENTS: dict[str, dict[str, object]] = {
    "maglock": {
        "env": "maglock",
        "dev": "maglock",
        "cmd_node": "maglock",
        "firmware": "maglock.bin",
        "legacy": ("maglock_ctrl.bin",),
        "verify_nodes": ("maglock",),
    },
    "images_piano": {
        "env": "images_piano",
        "dev": "images_piano",
        "cmd_node": "images",
        "firmware": "images_piano.bin",
        "legacy": (),
        "verify_nodes": ("images", "piano"),
    },
    "chess": {
        "env": "chess",
        "dev": "chess",
        "cmd_node": "chess",
        "firmware": "chess.bin",
        "legacy": (),
        "verify_nodes": ("chess",),
    },
    "knocking": {
        "env": "knocking",
        "dev": "knocking",
        "cmd_node": "knocking",
        "firmware": "knocking.bin",
        "legacy": (),
        "verify_nodes": ("knocking",),
    },
    "candles": {
        "env": "candles",
        "dev": "candles",
        "cmd_node": "candles",
        "firmware": "candles.bin",
        "legacy": (),
        "verify_nodes": ("candles",),
    },
    "star_sky": {
        "env": "star_sky",
        "dev": "star_sky",
        "cmd_node": "star_sky",
        "firmware": "star_sky.bin",
        "legacy": (),
        "verify_nodes": ("star_sky",),
    },
    "star_slider": {
        "env": "star_slider",
        "dev": "star_slider",
        "cmd_node": "star_slider",
        "firmware": "star_slider.bin",
        "legacy": (),
        "verify_nodes": ("star_slider",),
    },
    "stop_timer": {
        "env": "stop_timer",
        "dev": "stop_timer",
        "cmd_node": "stop_timer",
        "firmware": "stop_timer.bin",
        "legacy": (),
        "verify_nodes": ("stop_timer",),
    },
}

# Time sync configuration
TIME_TOPIC = "time/state"
TIME_PUBLISH_INTERVAL = 30  # seconds
TIME_WARN_INTERVAL = 60  # max warn once per 60s if system time invalid
TIME_EPOCH_MIN = 1672531200  # 2023-01-01 00:00:00 UTC


class DeviceState:
    __slots__ = ("fw", "uptime", "online", "last_seen")

    fw: str
    uptime: int | None
    online: bool
    last_seen: float

    def __init__(self) -> None:
        self.fw = "?"
        self.uptime = None
        self.online = False
        self.last_seen = 0.0


class Attempt:
    __slots__ = ("deployment", "nodes", "old_fw", "start", "deadline", "seen_offline", "seen_return", "fw_by_node")

    def __init__(self, deployment: str, nodes: tuple[str, ...], old_fw: str) -> None:
        self.deployment = deployment
        self.nodes = nodes
        self.old_fw = old_fw or "?"
        self.start = time.time()
        self.deadline = self.start + VERIFY_WINDOW
        self.seen_offline: set[str] = set()
        self.seen_return: set[str] = set()
        self.fw_by_node: dict[str, str] = {}


devices: dict[str, DeviceState] = {}
attempts: dict[str, Attempt] = {}
running = True
_last_malformed_log: dict[str, float] = {}
_suppress_malformed = False
_time_seq = 0
_last_time_publish = 0.0
_last_time_invalid_warn = 0.0


def log_line(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]
    line = f"{timestamp} {msg}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    print(line, flush=True)


def format_ts_iso_like(dt: datetime) -> str:
    """Format datetime using canonical timestamp: YYYY.MM.DD HH:MM:SS.mmm"""
    return dt.strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]


def publish_time_if_due(client) -> None:
    """Publish Pi's wall-clock time to time/state topic (retained)."""
    global _time_seq, _last_time_publish, _last_time_invalid_warn
    now = time.time()

    # Check if it's time to publish (every 30s)
    if now - _last_time_publish < TIME_PUBLISH_INTERVAL:
        return

    # Check if system time is valid
    if now < TIME_EPOCH_MIN:
        # System time is invalid; warn at most once per 60s
        if now - _last_time_invalid_warn >= TIME_WARN_INTERVAL:
            _last_time_invalid_warn = now
            log_line(f"[warn] system time invalid epoch={int(now)} (< {TIME_EPOCH_MIN})")
        return

    # System time is valid; publish it
    _last_time_publish = now
    _time_seq += 1

    dt = datetime.fromtimestamp(now)
    ts_str = format_ts_iso_like(dt)

    payload = {
        "epoch": int(now),
        "ts": ts_str,
        "src": "er1-pi",
        "seq": _time_seq,
    }

    try:
        payload_json = json.dumps(payload, separators=(',', ':'))
        client.publish(TIME_TOPIC, payload_json, retain=True)
    except Exception as e:
        log_line(f"[err] time publish failed: {e}")


def ensure_state(node: str) -> DeviceState:
    state = devices.get(node)
    if state is None:
        state = DeviceState()
        devices[node] = state
    return state


def deployment_for(node: str) -> str:
    for deployment, cfg in DEPLOYMENTS.items():
        verify_nodes = cfg.get("verify_nodes") or ()
        cmd_node = cfg.get("cmd_node") or deployment
        if node == deployment or node == cmd_node or node in verify_nodes:
            return deployment
    return node


def nodes_for(deployment: str) -> tuple[str, ...]:
    cfg = DEPLOYMENTS.get(deployment)
    if not cfg:
        return (deployment,)
    verify_nodes = cfg.get("verify_nodes")
    if verify_nodes:
        return tuple(verify_nodes)
    cmd_node = cfg.get("cmd_node") or deployment
    return (cmd_node,)


def complete_attempt(attempt: Attempt, success: bool, *, reason: str | None = None) -> None:
    attempts.pop(attempt.deployment, None)
    if success:
        fw_values = [fw for fw in attempt.fw_by_node.values() if fw]
        final_fw = ",".join(sorted(set(fw_values))) if fw_values else attempt.old_fw
        log_line(
            f"OTA_RESULT dev={attempt.deployment} nodes={','.join(attempt.nodes)} result=OK "
            f"old_fw={attempt.old_fw} new_fw={final_fw}"
        )
    else:
        why = reason or "timeout"
        last_fw = ",".join(sorted(set(attempt.fw_by_node.values()))) if attempt.fw_by_node else attempt.old_fw
        log_line(
            f"OTA_RESULT dev={attempt.deployment} nodes={','.join(attempt.nodes)} result=FAIL "
            f"reason={why} old_fw={attempt.old_fw} last_fw={last_fw}"
        )


def start_attempt(node: str) -> None:
    deployment = deployment_for(node)
    nodes = nodes_for(deployment)
    prior = attempts.get(deployment)
    if prior is not None:
        complete_attempt(prior, False, reason="timeout")

    baseline_fw = "?"
    for n in nodes:
        if n in devices and devices[n].fw and devices[n].fw != "?":
            baseline_fw = devices[n].fw
            break
    attempts[deployment] = Attempt(deployment, nodes, baseline_fw)


def parse_topic(topic: str) -> tuple[str, str] | None:
    parts = topic.split("/")
    if len(parts) != 2:
        log_malformed("topic_parts", topic)
        return None
    node, suffix = parts
    if suffix not in ("cmd", "hb"):
        log_malformed("topic_suffix", topic)
        return None
    return node, suffix


def handle_cmd(node: str, payload: str) -> None:
    cmd = payload.strip()
    if not cmd:
        return
    if cmd.upper().startswith("UPDATE"):
        start_attempt(node)


def detect_offline_via_uptime(prev: int | None, current: int | None) -> bool:
    if prev is None or current is None:
        return False
    # require clear drop (at least 5s) to avoid noise
    return current + 5 < prev


def handle_offline(node: str) -> None:
    state = ensure_state(node)
    state.online = False
    state.uptime = None
    state.last_seen = time.time()

    attempt = attempts.get(deployment_for(node))
    if attempt:
        attempt.seen_offline.add(node)


def parse_uptime(data: dict) -> int | None:
    for field in ("up", "uptime"):
        if field in data:
            try:
                return int(data[field])
            except (ValueError, TypeError):
                continue
    return None


def handle_hb(node: str, payload: str) -> None:
    text = payload.strip()
    if not text:
        return
    if text.lower() == "offline":
        handle_offline(node)
        return

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log_malformed("hb_json", f"{node}/hb", text)
        return

    state = ensure_state(node)
    prev_uptime = state.uptime

    fw = data.get("fw") or data.get("firmware")
    if isinstance(fw, str):
        state.fw = fw
    uptime_val = parse_uptime(data)

    state.uptime = uptime_val
    state.online = True
    state.last_seen = time.time()

    deployment = deployment_for(node)
    attempt = attempts.get(deployment)
    if attempt:
        if detect_offline_via_uptime(prev_uptime, uptime_val):
            attempt.seen_offline.add(node)
            attempt.seen_return.add(node)
        elif node in attempt.seen_offline:
            attempt.seen_return.add(node)

        if state.fw:
            attempt.fw_by_node[node] = state.fw

        if attempt_ready(attempt):
            complete_attempt(attempt, True)


def attempt_ready(attempt: Attempt) -> bool:
    nodes = attempt.nodes
    if not nodes:
        return False
    for node in nodes:
        if node not in attempt.seen_offline or node not in attempt.seen_return:
            return False
        state = devices.get(node)
        if state is None or not state.fw:
            return False
        if attempt.old_fw and attempt.old_fw != "?" and state.fw == attempt.old_fw:
            return False
    return True


def on_connect(client, _userdata, _flags, rc: int) -> None:
    if rc != 0:
        print(f"[ota-verify] MQTT connect failed rc={rc}", file=sys.stderr, flush=True)
        return
    client.subscribe("+/cmd")
    client.subscribe("+/hb")


def on_message(_client, _userdata, msg) -> None:
    parsed = parse_topic(msg.topic)
    if not parsed:
        return
    node, suffix = parsed
    payload = msg.payload.decode("utf-8", errors="ignore")
    if suffix == "cmd":
        handle_cmd(node, payload)
    elif suffix == "hb":
        handle_hb(node, payload)


def check_timeouts() -> None:
    now = time.time()
    expired: list[tuple[Attempt, str]] = []
    for attempt in attempts.values():
        if now >= attempt.deadline:
            if len(attempt.seen_offline) < len(attempt.nodes):
                reason = "no_offline"
            elif len(attempt.seen_return) < len(attempt.nodes):
                reason = "no_return"
            elif not attempt.fw_by_node:
                reason = "no_fw_change"
            elif all(fw == attempt.old_fw for fw in attempt.fw_by_node.values()):
                reason = "no_fw_change"
            else:
                reason = "timeout"
            expired.append((attempt, reason))
    for attempt, reason in expired:
        complete_attempt(attempt, False, reason=reason)


def stop_loop(_signum, _frame) -> None:
    global running
    running = False


def log_malformed(reason: str, topic: str, payload: str | None = None) -> None:
    if _suppress_malformed:
        return
    now = time.time()
    key = f"{reason}:{topic}"
    last = _last_malformed_log.get(key, 0.0)
    if now - last < 5.0:
        return
    _last_malformed_log[key] = now
    suffix = ""
    if payload:
        sample = payload.replace("\n", " ").replace("\r", " ")
        if len(sample) > 80:
            sample = sample[:80] + "..."
        suffix = f" payload={sample}"
    log_line(f"[warn] malformed {reason} topic={topic}{suffix}")


def self_test() -> int:
    global _suppress_malformed
    _suppress_malformed = True
    cases = [
        ("stop_timer/cmd", ("stop_timer", "cmd")),
        ("stop_timer/hb", ("stop_timer", "hb")),
        ("maglock/hb", ("maglock", "hb")),
        ("stop_timer/ota", None),
        ("stop_timer", None),
    ]
    failures = 0
    for topic, expected in cases:
        result = parse_topic(topic)
        ok = result == expected
        print(f"{topic} -> {result} (expected {expected})")
        if not ok:
            failures += 1

    # Test time/state payload
    print("\n--- Time Sync Payload Test ---")
    now = time.time()
    if now < TIME_EPOCH_MIN:
        print(f"[skip] system time invalid, using placeholder")
        now = TIME_EPOCH_MIN + 100

    dt = datetime.fromtimestamp(now)
    ts_str = format_ts_iso_like(dt)

    sample_payload = {
        "epoch": int(now),
        "ts": ts_str,
        "src": "er1-pi",
        "seq": 1,
    }

    try:
        payload_json = json.dumps(sample_payload, separators=(',', ':'))
        print(f"topic: {TIME_TOPIC}")
        print(f"payload: {payload_json}")

        # Verify it parses
        parsed = json.loads(payload_json)
        assert "epoch" in parsed and isinstance(parsed["epoch"], int)
        assert "ts" in parsed and isinstance(parsed["ts"], str)
        assert "src" in parsed and isinstance(parsed["src"], str)
        assert "seq" in parsed and isinstance(parsed["seq"], int)
        print("✓ time/state payload valid")
    except Exception as e:
        print(f"✗ time/state payload invalid: {e}")
        failures += 1

    return failures


def main() -> None:
    import paho.mqtt.client as mqtt # type: ignore
    signal.signal(signal.SIGINT, stop_loop)
    signal.signal(signal.SIGTERM, stop_loop)
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 30)

    while running:
        client.loop(timeout=1.0)
        check_timeouts()
        publish_time_if_due(client)

    client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    try:
        main()
    except KeyboardInterrupt:
        pass
