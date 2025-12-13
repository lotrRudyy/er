#!/usr/bin/env python3
from __future__ import annotations
"""
OTA verification watcher.

Subscribes to +/cmd and +/hb to track UPDATE commands.
Emits OTA_RESULT lines once success/failure is determined.

Dependencies:
  pip install paho-mqtt
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

BROKER = os.getenv("LOCAL_BROKER", "127.0.0.1")
PORT = int(os.getenv("LOCAL_BROKER_PORT", "1883"))
VERIFY_WINDOW = int(os.getenv("OTA_VERIFY_WINDOW", "90"))
LOG_DIR = Path(os.getenv("ER1_LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))
LOG_FILE = Path(os.getenv("OTA_VERIFY_LOG", str(LOG_DIR / "ota-verify.log")))


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
    __slots__ = ("node", "old_fw", "last_fw", "start", "deadline", "saw_offline", "saw_return")

    def __init__(self, node: str, old_fw: str) -> None:
        self.node = node
        self.old_fw = old_fw or "?"
        self.last_fw = self.old_fw
        self.start = time.time()
        self.deadline = self.start + VERIFY_WINDOW
        self.saw_offline = False
        self.saw_return = False


devices: dict[str, DeviceState] = {}
attempts: dict[str, Attempt] = {}
running = True
_last_malformed_log: dict[str, float] = {}
_suppress_malformed = False


def log_line(msg: str) -> None:
    timestamp = time.strftime("[%d.%m.%Y %H:%M:%S]", time.localtime())
    line = f"{timestamp} {msg}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    print(line, flush=True)


def key_for(node: str) -> str:
    return node


def ensure_state(node: str) -> DeviceState:
    key = key_for(node)
    state = devices.get(key)
    if state is None:
        state = DeviceState()
        devices[key] = state
    return state


def complete_attempt(key: str, success: bool, *, reason: str | None = None, new_fw: str | None = None) -> None:
    attempt = attempts.pop(key, None)
    if attempt is None:
        return
    if success:
        final_fw = new_fw or attempt.last_fw or attempt.old_fw
        log_line(
            f"OTA_RESULT dev={attempt.node} result=OK "
            f"old_fw={attempt.old_fw} new_fw={final_fw}"
        )
    else:
        why = reason or "timeout"
        last_fw = attempt.last_fw or attempt.old_fw
        log_line(
            f"OTA_RESULT dev={attempt.node} result=FAIL "
            f"reason={why} old_fw={attempt.old_fw} last_fw={last_fw}"
        )


def start_attempt(node: str) -> None:
    key = key_for(node)
    prior = attempts.get(key)
    if prior is not None:
        complete_attempt(key, False, reason="timeout")

    state = ensure_state(node)
    attempts[key] = Attempt(node, state.fw)


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
    key = key_for(node)
    state = ensure_state(node)
    state.online = False
    state.uptime = None
    state.last_seen = time.time()

    attempt = attempts.get(key)
    if attempt:
        attempt.saw_offline = True


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

    key = key_for(node)
    state = ensure_state(node)
    prev_uptime = state.uptime

    fw = data.get("fw") or data.get("firmware")
    if isinstance(fw, str):
        state.fw = fw
    uptime_val = parse_uptime(data)

    state.uptime = uptime_val
    state.online = True
    state.last_seen = time.time()

    attempt = attempts.get(key)
    if attempt:
        if not attempt.saw_offline and detect_offline_via_uptime(prev_uptime, uptime_val):
            attempt.saw_offline = True
            attempt.saw_return = True
        elif attempt.saw_offline:
            attempt.saw_return = True

        if state.fw:
            attempt.last_fw = state.fw

        if attempt.saw_offline and state.fw and state.fw != attempt.old_fw:
            complete_attempt(key, True, new_fw=state.fw)


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
    expired = []
    for key, attempt in attempts.items():
        if now >= attempt.deadline:
            if not attempt.saw_offline:
                reason = "no_offline"
            elif not attempt.saw_return:
                reason = "no_return"
            elif attempt.last_fw == attempt.old_fw:
                reason = "no_fw_change"
            else:
                reason = "timeout"
            expired.append((key, reason))
    for key, reason in expired:
        complete_attempt(key, False, reason=reason)


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

    client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    try:
        main()
    except KeyboardInterrupt:
        pass
