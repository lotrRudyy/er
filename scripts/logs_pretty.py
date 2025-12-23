#!/usr/bin/env python3
"""
Human-friendly ER1 log viewer.

- Subscribes to hb/log/ota/time/state.
- Prints a heartbeat dashboard exactly on each minute boundary.
- Shows OTA progress as consolidated blocks.
- Handles multiline log messages without escaping newlines.
"""
import json
import os
import queue
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency check
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


HB_INTERVAL_SEC = int(os.environ.get("HB_INTERVAL_SEC", "20"))
TIME_STALE_SEC = 90
RESTART_DELTA = 5  # seconds of slack when detecting uptime drops
SEPARATOR = "-" * 75


def now_ts() -> float:
    return time.time()


def next_minute_boundary(from_ts: Optional[float] = None) -> float:
    base = datetime.fromtimestamp(from_ts or now_ts())
    boundary = (base.replace(second=0, microsecond=0) + timedelta(minutes=1)).timestamp()
    return boundary


def format_uptime(secs: int) -> str:
    if secs < 0:
        secs = 0
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02}:{minutes:02}:{seconds:02}"
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def int_or_none(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


@dataclass
class Heartbeat:
    node: str
    fw: str = ""
    build: str = ""
    up: int = 0
    prev_up: Optional[int] = None
    ts: str = ""
    time_valid: bool = True
    heap_free: Optional[int] = None
    heap_min: Optional[int] = None
    heap_size: Optional[int] = None
    heap_largest: Optional[int] = None
    err_cnt: Optional[int] = None
    err_code: Optional[int] = None
    err_since_up: Optional[int] = None
    err_msg: Optional[str] = None
    last_seen: float = field(default_factory=now_ts)
    restart_seen_at: Optional[float] = None


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
    reboot_seen: bool = False


class PrettyLogger:
    def __init__(self, broker: str, port: int) -> None:
        self.broker = broker
        self.port = port
        self.queue: queue.Queue = queue.Queue()
        self.hb_cache: Dict[str, Heartbeat] = {}
        self.ota_sessions: Dict[str, Dict[str, OtaSession]] = {}
        self.time_state: Dict[str, Any] = {}
        self.time_state_seen_at: Optional[float] = None
        self.start_ts = now_ts()
        self.running = True

    def stop(self, *_: Any) -> None:
        self.running = False

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, rc: int) -> None:
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}", file=sys.stderr)
            self.running = False
            return
        client.subscribe("+/hb")
        client.subscribe("+/log")
        client.subscribe("+/ota")
        client.subscribe("time/state")

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")
        self.queue.put((msg.topic, payload, now_ts()))

    def run(self) -> None:
        client = mqtt.Client()
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect(self.broker, self.port, 30)
        client.loop_start()

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        next_tick = next_minute_boundary()
        while self.running:
            timeout = max(0.0, next_tick - now_ts())
            try:
                topic, payload, recv_ts = self.queue.get(timeout=timeout)
                self.handle_message(topic, payload, recv_ts)
            except queue.Empty:
                self.print_dashboard(next_tick)
                next_tick = next_minute_boundary(next_tick + 0.1)
                continue

            if now_ts() >= next_tick:
                self.print_dashboard(next_tick)
                next_tick = next_minute_boundary()

        client.loop_stop()
        client.disconnect()

    def handle_message(self, topic: str, payload: str, recv_ts: float) -> None:
        if topic.endswith("/hb"):
            self.handle_hb(topic, payload, recv_ts)
        elif topic.endswith("/ota"):
            self.handle_ota(topic, payload, recv_ts)
        elif topic.endswith("/log"):
            self.handle_log(topic, payload)
        elif topic == "time/state":
            self.handle_time_state(payload, recv_ts)
        else:
            self.print_fallback(topic, payload)

    def safe_json(self, payload: str) -> Any:
        try:
            return json.loads(payload)
        except Exception:
            return None

    def handle_hb(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self.print_fallback(topic, payload)
            return

        node = str(data.get("node") or topic.split("/")[0])
        rec = self.hb_cache.get(node, Heartbeat(node=node))

        prev_up = rec.up if rec.up else rec.prev_up
        new_up = int_or_none(data.get("up"), rec.up)
        if prev_up is not None and new_up is not None and (new_up + RESTART_DELTA) < prev_up:
            rec.restart_seen_at = recv_ts

        rec.prev_up = prev_up
        rec.up = new_up or 0
        rec.fw = str(data.get("fw") or rec.fw or "")
        rec.build = str(data.get("build") or rec.build or "")
        rec.ts = str(data.get("ts") or rec.ts or "")
        rec.time_valid = bool(data.get("time_valid", True))
        rec.heap_free = int_or_none(data.get("heap_free"), rec.heap_free)
        rec.heap_min = int_or_none(data.get("heap_min"), rec.heap_min)
        rec.heap_size = int_or_none(data.get("heap_size"), rec.heap_size)
        rec.heap_largest = int_or_none(data.get("heap_largest"), rec.heap_largest)
        rec.err_cnt = int_or_none(data.get("err_cnt"), rec.err_cnt)
        rec.err_code = int_or_none(data.get("err_code"), rec.err_code)
        rec.err_since_up = int_or_none(data.get("err_since_up"), rec.err_since_up)
        err_msg = data.get("err_msg")
        if rec.err_code:
            rec.err_msg = str(err_msg) if err_msg else rec.err_msg
        else:
            rec.err_msg = None
        rec.last_seen = recv_ts
        self.hb_cache[node] = rec
        self.mark_ota_reboot(node, rec)

    def handle_time_state(self, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return
        self.time_state = data
        self.time_state_seen_at = recv_ts

    def handle_log(self, topic: str, payload: str) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self.print_fallback(topic, payload)
            return
        node = topic.split("/")[0]
        level = data.get("lv") or data.get("level") or "?"
        ts = data.get("ts") or ""
        msg = data.get("msg") or ""
        data_field = data.get("d")

        header = f"{node}/log {level}"
        if ts:
            header += f" {ts}"

        if "\n" in msg:
            print(header)
            for line in msg.splitlines():
                print(line)
        else:
            print(f"{header} {msg}")

        if isinstance(data_field, dict) and data_field:
            try:
                print(f"  data: {json.dumps(data_field, separators=(',', ':'))}")
            except Exception:
                pass

    def handle_ota(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self.print_fallback(topic, payload)
            return
        node = topic.split("/")[0]
        status = str(data.get("st") or "")
        details_raw = data.get("d", {})
        details = details_raw
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
        self.print_ota_session(session)

    def mark_ota_reboot(self, node: str, hb: Heartbeat) -> None:
        if not hb.restart_seen_at:
            return
        sessions = self.ota_sessions.get(node, {})
        updated = False
        for sess in sessions.values():
            if sess.status in {"OTA_OK", "OTA_FAIL"}:
                continue
            if not sess.reboot_seen:
                sess.reboot_seen = True
                sess.last_update = hb.restart_seen_at
                updated = True
        if updated:
            for sess in sessions.values():
                if sess.reboot_seen and sess.status not in {"OTA_OK", "OTA_FAIL"}:
                    self.print_ota_session(sess, note="reboot detected")

    def print_fallback(self, topic: str, payload: str) -> None:
        print(f"[unparsed] {topic}: {payload}")

    def format_heap(self, hb: Heartbeat) -> str:
        parts = []
        if hb.heap_free is not None:
            parts.append(f"free={hb.heap_free//1024}k")
        if hb.heap_min is not None:
            parts.append(f"min={hb.heap_min//1024}k")
        if hb.heap_largest is not None:
            parts.append(f"largest={hb.heap_largest//1024}k")
        if hb.heap_size is not None:
            parts.append(f"size={hb.heap_size//1024}k")
        return " ".join(parts) if parts else "heap=?"

    def format_err(self, hb: Heartbeat) -> str:
        if hb.err_code:
            msg = f"code={hb.err_code}"
            if hb.err_msg:
                msg += f" msg={hb.err_msg}"
            if hb.err_since_up is not None:
                msg += f" at_up={hb.err_since_up}"
            if hb.err_cnt is not None:
                msg += f" cnt={hb.err_cnt}"
            return msg
        cnt_part = f"cnt={hb.err_cnt}" if hb.err_cnt is not None else "cnt=?"
        return f"ok {cnt_part}"

    def print_dashboard(self, boundary_ts: float) -> None:
        dt = datetime.fromtimestamp(boundary_ts)
        ts_str = dt.strftime("%H:%M:%S.%f")[:-3]
        date_str = dt.strftime("%Y.%m.%d")

        header_extra = ""
        if self.time_state:
            ts_field = str(self.time_state.get("ts") or date_str)
            try:
                time_part = ts_field.split(" ")[1] if " " in ts_field else None
                date_part = ts_field.split(" ")[0] if " " in ts_field else ts_field
                if time_part:
                    ts_str = time_part
                if date_part:
                    date_str = date_part
            except Exception:
                pass
            epoch = self.time_state.get("epoch")
            seq = self.time_state.get("seq")
            extras = []
            if epoch is not None:
                extras.append(f"epoch={epoch}")
            if seq is not None:
                extras.append(f"seq={seq}")
            if extras:
                header_extra = f" ({' '.join(extras)})"

        print(SEPARATOR)
        print(f"HB | {ts_str} - {date_str}{header_extra}")
        print(SEPARATOR)

        now = now_ts()
        warnings = []
        if self.time_state_seen_at:
            if (now - self.time_state_seen_at) > TIME_STALE_SEC:
                warnings.append("time/state stale (>90s)")
        elif (now - self.start_ts) > TIME_STALE_SEC:
            warnings.append("time/state missing (>90s)")

        rows = []
        for hb in sorted(self.hb_cache.values(), key=lambda r: (-r.up, r.node)):
            age = now - hb.last_seen
            stale = age > (HB_INTERVAL_SEC * 2)
            if not hb.time_valid:
                warnings.append(f"time invalid: {hb.node}")
            flags = []
            if hb.restart_seen_at and (now - hb.restart_seen_at) < 180:
                flags.append("RESTARTED")
            if stale:
                flags.append("STALE")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            row = (
                f"{hb.node:12} "
                f"up={format_uptime(hb.up):>12} "
                f"age={int(age):>3}s{flag_str} "
                f"fw={hb.fw or '?'} build={hb.build or '?'} "
                f"{self.format_heap(hb)} "
                f"err[{self.format_err(hb)}]"
            )
            rows.append(row)

        if not rows:
            print("(no heartbeats yet)")
        else:
            for r in rows:
                print(r)

        for warn in dict.fromkeys(warnings):
            print(f"! {warn}")

        print(SEPARATOR)
        sys.stdout.flush()

    def print_ota_session(self, sess: OtaSession, note: Optional[str] = None) -> None:
        lines = [SEPARATOR]
        header = f"OTA {sess.node} id={sess.id} status={sess.status}"
        if sess.version:
            header += f" ver={sess.version}"
        if sess.target:
            header += f" target={sess.target}"
        lines.append(header)
        if sess.pct is not None:
            lines.append(f"  progress={sess.pct}%")
        if sess.bytes is not None:
            lines.append(f"  bytes={sess.bytes}")
        if sess.sha256:
            lines.append(f"  sha256={sess.sha256}")
        if sess.url:
            lines.append(f"  url={sess.url}")
        if sess.reboot_seen:
            lines.append("  reboot: detected via hb reset")
        reason = sess.last_details.get("reason")
        if reason:
            lines.append(f"  reason={reason}")
        if note:
            lines.append(f"  note={note}")
        lines.append(SEPARATOR)
        print("\n".join(lines))
        sys.stdout.flush()


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    logger = PrettyLogger(broker, port)
    logger.run()


if __name__ == "__main__":
    main()
