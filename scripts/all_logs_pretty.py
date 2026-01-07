#!/usr/bin/env python3
"""
logs_all_pretty.py — Pretty ER1 log + OTA streamer (all nodes).

- Subscribes to: +/log and +/ota
- Prints every log from every node in a consistent, readable format.
- Handles multiline messages without escaping newlines.
- Prints OTA progress as consolidated blocks per node/session.
- Ctrl+C exits immediately.

Env:
  LOCAL_BROKER (default 127.0.0.1)
  LOCAL_BROKER_PORT (default 1883)
"""
import json
import os
import queue
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


SEPARATOR = "-" * 80
QueueItem = Tuple[str, str, float]


def now_ts() -> float:
    return time.time()


def int_or_none(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


@dataclass
class OtaSession:
    node: str
    id: str
    version: Optional[str] = None
    target: Optional[str] = None
    url: Optional[str] = None
    status: str = ""
    pct: Optional[int] = None
    bytes: Optional[int] = None
    sha256: Optional[str] = None
    last_details: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=now_ts)
    last_update: float = field(default_factory=now_ts)


class LogsAllPretty:
    def __init__(self, broker: str, port: int) -> None:
        self.broker = broker
        self.port = port
        self.queue: "queue.Queue[QueueItem]" = queue.Queue()
        self.running = True
        self._STOP_TOPIC = "__STOP__"

        # node -> ota_id -> session
        self.ota_sessions: Dict[str, Dict[str, OtaSession]] = {}

    def stop(self, *_: Any) -> None:
        self.running = False
        try:
            self.queue.put_nowait((self._STOP_TOPIC, "", now_ts()))
        except Exception:
            pass

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, rc: int) -> None:
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}", file=sys.stderr)
            self.running = False
            return
        client.subscribe("+/log")
        client.subscribe("+/ota")

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")
        try:
            self.queue.put_nowait((msg.topic, payload, now_ts()))
        except Exception:
            pass

    def safe_json(self, payload: str) -> Any:
        try:
            return json.loads(payload)
        except Exception:
            return None

    def run(self) -> None:
        client = mqtt.Client()
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect(self.broker, self.port, 30)
        client.loop_start()

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            while self.running:
                try:
                    topic, payload, recv_ts = self.queue.get(timeout=0.25)
                    if topic == self._STOP_TOPIC:
                        break

                    if topic.endswith("/log"):
                        self.handle_log(topic, payload)
                    elif topic.endswith("/ota"):
                        self.handle_ota(topic, payload, recv_ts)
                    else:
                        self._safe_print(f"[unparsed] {topic}: {payload}")
                        self._safe_flush()

                except queue.Empty:
                    pass
        finally:
            client.loop_stop()
            client.disconnect()

    # ------------------------
    # LOG HANDLING
    # ------------------------
    def handle_log(self, topic: str, payload: str) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self._safe_print(f"[unparsed] {topic}: {payload}")
            self._safe_flush()
            return

        node = topic.split("/")[0]
        level = data.get("lv") or data.get("level") or "?"
        ts = data.get("ts") or ""
        msg = data.get("msg") or ""
        data_field = data.get("d")

        # Special pretty card for piano DSP compatibility logs.
        if node == "piano" and isinstance(data_field, dict):
            t = str(data_field.get("t") or "")
            if not t:
                if "REJ_COMPAT" in str(msg):
                    t = "REJ"
                elif "ACC_COMPAT" in str(msg):
                    t = "ACC"
            if t in {"REJ", "ACC"}:
                pred = str(data_field.get("pred") or "?")
                s1 = data_field.get("s1")
                s2 = data_field.get("s2")
                margin = data_field.get("margin")
                hps = data_field.get("hps")
                harm = data_field.get("harm")

                def fnum(v: Any, fmt: str) -> str:
                    try:
                        return format(float(v), fmt)
                    except Exception:
                        return "?"

                line1 = (
                    f"pred={pred}  "
                    f"margin={fnum(margin, '.4f')}  "
                    f"s1={fnum(s1, '.4f')} s2={fnum(s2, '.4f')}  "
                    f"harm={harm if harm is not None else '?'}  "
                    f"hps={fnum(hps, '.1f')}"
                )

                top = data_field.get("top")
                top3_parts = []
                if isinstance(top, list):
                    for item in top[:3]:
                        if isinstance(item, dict):
                            p = item.get("p")
                            s = item.get("s")
                            if p is not None and s is not None:
                                top3_parts.append(f"{p} {fnum(s, '.4f')}")
                line2 = "top3: " + (" | ".join(top3_parts) if top3_parts else "n/a")

                self._safe_print(SEPARATOR)
                self._safe_print(f"piano | {t} | {ts}".rstrip())
                self._safe_print(SEPARATOR)
                self._safe_print(line1)
                self._safe_print(line2)
                self._safe_print(SEPARATOR)
                self._safe_flush()
                return

        header = f"{node}/log {level}"
        if ts:
            header += f" {ts}"

        if "\n" in str(msg):
            self._safe_print(header)
            for line in str(msg).splitlines():
                self._safe_print(line)
        else:
            self._safe_print(f"{header} {msg}")

        self._safe_flush()

    # ------------------------
    # OTA HANDLING
    # ------------------------
    def handle_ota(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self._safe_print(f"[unparsed] {topic}: {payload}")
            self._safe_flush()
            return

        node = topic.split("/")[0]
        status = str(data.get("st") or "")

        details_raw = data.get("d", {})
        details: Any = details_raw
        if isinstance(details_raw, str):
            try:
                details = json.loads(details_raw)
            except Exception:
                details = {}
        if not isinstance(details, dict):
            details = {}

        ota_id = str(details.get("id") or "?")
        session = self.ota_sessions.get(node, {}).get(ota_id) or OtaSession(node=node, id=ota_id)

        session.status = status
        session.last_update = recv_ts
        session.last_details = details
        session.version = str(details.get("version") or session.version or "")
        session.target = str(details.get("target") or session.target or "")
        session.url = str(details.get("url") or session.url or "")
        session.pct = int_or_none(details.get("pct"), session.pct)
        session.bytes = int_or_none(details.get("bytes"), session.bytes)
        session.sha256 = str(details.get("sha256") or session.sha256 or "")

        self.ota_sessions.setdefault(node, {})[ota_id] = session
        self.print_ota_group(node)

    def format_ota_line(self, sess: OtaSession) -> str:
        parts = [f"id={sess.id}", f"st={sess.status}"]
        if sess.pct is not None:
            parts.append(f"{sess.pct}%")
        if sess.version:
            parts.append(f"ver={sess.version}")
        if sess.target:
            parts.append(f"tgt={sess.target}")
        if sess.bytes is not None:
            parts.append(f"bytes={sess.bytes}")
        if sess.sha256:
            parts.append(f"sha256={sess.sha256}")
        if sess.url:
            parts.append(f"url={sess.url}")
        reason = sess.last_details.get("reason")
        if reason:
            parts.append(f"reason={reason}")
        return " ".join(parts)

    def print_ota_group(self, node: str) -> None:
        sessions = self.ota_sessions.get(node, {})
        if not sessions:
            return
        lines = [
            SEPARATOR,
            f"OTA | {node} ({len(sessions)} session{'s' if len(sessions) != 1 else ''})",
            SEPARATOR,
        ]
        for sess in sorted(sessions.values(), key=lambda s: (s.last_update, s.id)):
            lines.append(self.format_ota_line(sess))
        self._safe_print("\n".join(lines))
        self._safe_flush()

    # ------------------------
    # IO SAFETY
    # ------------------------
    def _safe_print(self, s: str) -> None:
        try:
            print(s)
        except BrokenPipeError:
            self.stop()

    def _safe_flush(self) -> None:
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            self.stop()


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    LogsAllPretty(broker, port).run()


if __name__ == "__main__":
    main()
