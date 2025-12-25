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
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency check
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


HB_INTERVAL_SEC = int(os.environ.get("HB_INTERVAL_SEC", "20"))
TIME_STALE_SEC = 90
RESTART_DELTA = 5  # seconds of slack when detecting uptime drops
SEPARATOR = "-" * 64
LOG_HISTORY_LINES = int(os.environ.get("LOG_HISTORY_LINES", "200"))
LOG_DIR = Path(os.environ.get("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))


def now_ts() -> float:
    return time.time()


def next_minute_boundary(from_ts: Optional[float] = None) -> float:
    base = datetime.fromtimestamp(from_ts or now_ts())
    boundary = (base.replace(second=0, microsecond=0) + timedelta(minutes=1)).timestamp()
    return boundary

def minute_floor(ts: Optional[float] = None) -> float:
    base = datetime.fromtimestamp(ts or now_ts())
    return base.replace(second=0, microsecond=0).timestamp()

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
        self.last_dashboard_ts: Optional[int] = None
        self.history_lines = LOG_HISTORY_LINES
        self.log_dir = LOG_DIR
        self.burst_active = False
        self.burst_end_ts: Optional[float] = None

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

        self.print_startup_history()

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        next_tick = next_minute_boundary()
        self.burst_active = True
        self.burst_end_ts = now_ts() + 60  # allow rapid prints during first minute after start
        while self.running:
            timeout = max(0.0, next_tick - now_ts())
            try:
                topic, payload, recv_ts = self.queue.get(timeout=timeout)
                self.handle_message(topic, payload, recv_ts)
            except queue.Empty:
                current_ts = now_ts()
                if self.burst_active and self.burst_end_ts and current_ts >= self.burst_end_ts:
                    self.burst_active = False
                while current_ts >= next_tick:
                    self.print_dashboard(next_tick)
                    next_tick += 60
                continue

            current_ts = now_ts()
            if self.burst_active and self.burst_end_ts and current_ts >= self.burst_end_ts:
                self.burst_active = False
            while current_ts >= next_tick:
                self.print_dashboard(next_tick)
                next_tick += 60

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
        if prev_up is not None and new_up is not None and new_up >= 0 and (new_up + RESTART_DELTA) < prev_up:
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
        if self.burst_active and self.burst_end_ts and recv_ts <= self.burst_end_ts:
            self.print_dashboard(minute_floor(recv_ts), force=True)

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

        # Special pretty card for piano DSP compatibility logs.
        # The firmware emits a compact msg like "REJ_COMPAT ..." and also includes
        # a structured dict in `d` (e.g. {"t":"REJ", "pred":..., "s1":..., ...}).
        if node == "piano" and isinstance(data_field, dict):
            t = str(data_field.get("t") or "")
            if not t:
                # fall back to msg prefix (REJ_COMPAT/ACC_COMPAT)
                if "REJ_COMPAT" in msg:
                    t = "REJ"
                elif "ACC_COMPAT" in msg:
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

                piano_sep = "-" * 75
                print(piano_sep)
                # Example: piano | REJ | 03:04:00.578 - 2025.12.25
                print(f"piano | {t} | {ts}".rstrip())
                print(piano_sep)
                print(line1)
                print(line2)
                print(piano_sep)
                return

        header = f"{node}/log {level}"
        if ts:
            header += f" {ts}"

        if "\n" in msg:
            print(header)
            for line in msg.splitlines():
                print(line)
        else:
            print(f"{header} {msg}")

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
        self.print_ota_group(node)

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
            self.print_ota_group(node, note="reboot detected via hb reset")

    def print_fallback(self, topic: str, payload: str) -> None:
        print(f"[unparsed] {topic}: {payload}")

    def print_startup_history(self) -> None:
        if self.history_lines <= 0:
            return
        today = datetime.fromtimestamp(now_ts()).strftime("%d.%m.%Y")
        log_file = self.log_dir / f"er1-{today}.log"
        try:
            with log_file.open("r", encoding="utf-8") as fh:
                last_lines = deque(fh, maxlen=self.history_lines)
        except FileNotFoundError:
            return
        except Exception as exc:  # pragma: no cover - best-effort history
            print(f"[history] failed to read {log_file}: {exc}", file=sys.stderr)
            return

        lines = list(last_lines)
        if not lines:
            return
        print(f"[history] showing last {len(lines)} lines from {log_file}")
        for line in lines:
            print(line.rstrip("\n"))
        print(SEPARATOR)

    def format_heap(self, hb: Heartbeat) -> str:
        size = hb.heap_size or 0
        free_pct = None
        min_pct = None
        if size > 0:
            if hb.heap_free is not None:
                free_pct = max(0, min(100, int(hb.heap_free * 100 / size)))
            if hb.heap_min is not None:
                min_pct = max(0, min(100, int(hb.heap_min * 100 / size)))

        if free_pct is not None and min_pct is not None:
            return f"heap[{free_pct:02d}/{min_pct:02d}]"
        if free_pct is not None:
            return f"heap[{free_pct:02d}%]"
        if min_pct is not None:
            return f"heap[min{min_pct:02d}]"
        return "heap[n/a]"

    def format_build(self, build: str, max_len: int = 3) -> str:
        build_clean = (build or "").strip()
        if not build_clean:
            return "?"
        return build_clean if len(build_clean) <= max_len else f"{build_clean[:max_len]}..."

    def format_err(self, hb: Heartbeat) -> str:
        err_cnt = hb.err_cnt if hb.err_cnt is not None else 0
        if hb.err_code:
            msg = f"code={hb.err_code}"
            if hb.err_msg:
                msg += f" msg={hb.err_msg}"
            if hb.err_since_up is not None:
                msg += f" at_up={hb.err_since_up}"
            msg += f" cnt={err_cnt}"
            return msg
        return f"ok cnt={err_cnt}"

    def print_dashboard(self, boundary_ts: float, force: bool = False) -> None:
        boundary_min = int(boundary_ts // 60)
        if not force and self.last_dashboard_ts is not None and boundary_min == self.last_dashboard_ts:
            return
        self.last_dashboard_ts = boundary_min
        dt = datetime.fromtimestamp(boundary_min * 60)
        header_ts = datetime.fromtimestamp(minute_floor(boundary_ts)).strftime("%H:%M:%S.%f")[:-3]
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

        print(SEPARATOR)
        print(f"HB | {header_ts} - {date_str}{header_extra}")
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
                flags.append("RES")
            if stale:
                flags.append("STL")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            up_field = f"{format_uptime(hb.up)}{flag_str}"
            row = [
                f"{hb.node:9}",
                f"{up_field:<14}",
                f"{(hb.fw or '?'):<6}",
                f"{self.format_build(hb.build):<7}",
                f"{self.format_heap(hb):<11}",
                f"{self.format_err(hb)}",
            ]
            rows.append(" ".join(row))

        if not rows:
            print("(no heartbeats yet)")
        else:
            header = [
                f"{'node':9}",
                f"{'up':<14}",
                f"{'fw':<6}",
                f"{'build':<7}",
                f"{'heap':<11}",
                f"{'err'}",
            ]
            print(" ".join(header))
            print("-" * (len(" ".join(header)) + 9))
            for r in rows:
                print(r)

        for warn in dict.fromkeys(warnings):
            print(f"! {warn}")

        sys.stdout.flush()

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
        if sess.reboot_seen:
            parts.append("reboot")
        reason = sess.last_details.get("reason")
        if reason:
            parts.append(f"reason={reason}")
        return " ".join(parts)

    def print_ota_group(self, node: str, note: Optional[str] = None) -> None:
        sessions = self.ota_sessions.get(node, {})
        if not sessions:
            return
        lines = [SEPARATOR, f"OTA | {node} ({len(sessions)} session{'s' if len(sessions) != 1 else ''})", SEPARATOR]
        for sess in sorted(sessions.values(), key=lambda s: (s.last_update, s.id)):
            line = self.format_ota_line(sess)
            if note:
                line = f"{line} note={note}"
            lines.append(line)
        print("\n".join(lines))
        sys.stdout.flush()


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    logger = PrettyLogger(broker, port)
    logger.run()


if __name__ == "__main__":
    main()
