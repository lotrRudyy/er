#!/usr/bin/env python3
"""
hb_pretty.py — Human-friendly ER1 heartbeat viewer.

- Subscribes to: +/hb, +/ota, and time/state
- Prints a heartbeat dashboard exactly on each HB interval boundary (default 20s).
- Ctrl+C exits immediately.
- Dashboard cadence aligns to HB_INTERVAL_SEC (not minute-only).

Flags:
  BOOT: node recently rebooted and is not time-synced yet (time_valid=false)
  OTA : BOOT + reboot likely caused by a recent OTA success event

Env:
  HB_INTERVAL_SEC (default 20)
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
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


HB_INTERVAL_SEC = int(os.environ.get("HB_INTERVAL_SEC", "20"))
TIME_STALE_SEC = 90
RESTART_DELTA = 5  # seconds of slack when detecting uptime drops

# If an OTA "success-ish" status is seen within this window before a reboot is detected,
# treat the reboot as OTA-caused.
OTA_REBOOT_WINDOW_SEC = 180

SEPARATOR = "-" * 68


def now_ts() -> float:
    return time.time()


def interval_floor(ts: Optional[float] = None, interval: int = HB_INTERVAL_SEC) -> float:
    t = ts if ts is not None else now_ts()
    if interval <= 0:
        return float(int(t))
    return float(int(t // interval) * interval)


def next_interval_boundary(from_ts: Optional[float] = None, interval: int = HB_INTERVAL_SEC) -> float:
    t = from_ts if from_ts is not None else now_ts()
    if interval <= 0:
        return float(int(t) + 1)
    base = int(t // interval) * interval
    return float(base + interval)


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


def is_ota_success_status(st: str) -> bool:
    """
    Heuristic: mark OTA as "success-ish" when status contains typical success tokens.
    Your project may use different exact strings; this is designed to be resilient.
    """
    s = (st or "").strip().upper()
    if not s:
        return False
    # Explicit failures should not qualify
    if "FAIL" in s or "ERROR" in s:
        return False
    # Common success-ish tokens
    return any(tok in s for tok in ("OK", "DONE", "SUCCESS", "END", "COMPLETE", "APPLY"))


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

    # Restart detection (uptime drop)
    restart_seen_at: Optional[float] = None

    # Last OTA status seen for this node (from +/ota)
    ota_last_status: Optional[str] = None
    ota_last_seen_at: Optional[float] = None
    ota_last_success_at: Optional[float] = None


QueueItem = Tuple[str, str, float]


class HeartbeatViewer:
    def __init__(self, broker: str, port: int) -> None:
        self.broker = broker
        self.port = port
        self.queue: "queue.Queue[QueueItem]" = queue.Queue()
        self.hb_cache: Dict[str, Heartbeat] = {}

        self.time_state: Dict[str, Any] = {}
        self.time_state_seen_at: Optional[float] = None

        self.start_ts = now_ts()
        self.running = True

        # Dashboard de-dupe per interval bucket
        self.last_dashboard_bucket: Optional[int] = None

        # Allow rapid dashboards during the first minute after start (useful when attaching)
        self.burst_active = True
        self.burst_end_ts: Optional[float] = None

        # Special sentinel to wake the queue on stop
        self._STOP_TOPIC = "__STOP__"

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
        client.subscribe("+/hb")
        client.subscribe("+/ota")
        client.subscribe("time/state")

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

        next_tick = next_interval_boundary(interval=HB_INTERVAL_SEC)
        self.burst_active = True
        self.burst_end_ts = now_ts() + 60

        try:
            while self.running:
                timeout = min(0.25, max(0.0, next_tick - now_ts()))
                try:
                    topic, payload, recv_ts = self.queue.get(timeout=timeout)
                    if topic == self._STOP_TOPIC:
                        break
                    self.handle_message(topic, payload, recv_ts)
                except queue.Empty:
                    pass

                current_ts = now_ts()

                if self.burst_active and self.burst_end_ts and current_ts >= self.burst_end_ts:
                    self.burst_active = False

                while current_ts >= next_tick:
                    self.print_dashboard(next_tick)
                    next_tick += HB_INTERVAL_SEC
        finally:
            client.loop_stop()
            client.disconnect()

    def handle_message(self, topic: str, payload: str, recv_ts: float) -> None:
        if topic.endswith("/hb"):
            self.handle_hb(topic, payload, recv_ts)
        elif topic.endswith("/ota"):
            self.handle_ota(topic, payload, recv_ts)
        elif topic == "time/state":
            self.handle_time_state(payload, recv_ts)
        else:
            # Shouldn't happen due to subscriptions, but keep quiet if it does
            pass

    def handle_time_state(self, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return
        self.time_state = data
        self.time_state_seen_at = recv_ts

    def handle_ota(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return
        node = topic.split("/")[0].strip() or "?"
        rec = self.hb_cache.get(node, Heartbeat(node=node))

        st = str(data.get("st") or data.get("state") or data.get("status") or "").strip()
        if st:
            rec.ota_last_status = st
            rec.ota_last_seen_at = recv_ts
            if is_ota_success_status(st):
                rec.ota_last_success_at = recv_ts

        self.hb_cache[node] = rec

        # During burst, refresh quickly
        if self.burst_active and self.burst_end_ts and recv_ts <= self.burst_end_ts:
            self.print_dashboard(interval_floor(recv_ts, HB_INTERVAL_SEC), force=False)

    def handle_hb(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return

        node = str(data.get("node") or topic.split("/")[0])
        rec = self.hb_cache.get(node, Heartbeat(node=node))

        prev_up = rec.up if rec.up else rec.prev_up
        new_up = int_or_none(data.get("up"), rec.up)

        # Detect reboot via uptime drop
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

        if self.burst_active and self.burst_end_ts and recv_ts <= self.burst_end_ts:
            self.print_dashboard(interval_floor(recv_ts, HB_INTERVAL_SEC), force=False)

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

    def _reboot_is_ota(self, hb: Heartbeat, now: float) -> bool:
        """
        Consider reboot "OTA-caused" if:
          - we detected a reboot (restart_seen_at), and
          - we saw an OTA success-ish status recently before that reboot.
        """
        if hb.restart_seen_at is None:
            return False
        if hb.ota_last_success_at is None:
            return False
        # Compare against the reboot detection timestamp (more stable than 'now')
        return 0.0 <= (hb.restart_seen_at - hb.ota_last_success_at) <= OTA_REBOOT_WINDOW_SEC

    def print_dashboard(self, boundary_ts: float, force: bool = False) -> None:
        bucket = int(boundary_ts // max(1, HB_INTERVAL_SEC))
        if not force and self.last_dashboard_bucket is not None and bucket == self.last_dashboard_bucket:
            return
        self.last_dashboard_bucket = bucket

        header_ts = datetime.fromtimestamp(boundary_ts).strftime("%H:%M:%S.%f")[:-3]
        date_str = datetime.fromtimestamp(boundary_ts).strftime("%Y.%m.%d")

        if self.time_state:
            ts_field = str(self.time_state.get("ts") or date_str)
            try:
                if " - " in ts_field:
                    time_part, date_part = ts_field.split(" - ", 1)
                    if time_part.strip():
                        header_ts = time_part.strip()
                    if date_part.strip():
                        date_str = date_part.strip()
                else:
                    if ts_field.strip().count(".") >= 2:
                        date_str = ts_field.strip()
            except Exception:
                pass

        self._safe_print(SEPARATOR)
        self._safe_print(f"HB | {header_ts} - {date_str}  (every {HB_INTERVAL_SEC}s)")
        self._safe_print(SEPARATOR)

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

            # BOOT: show only while time is not valid yet (no more fixed 180s window)
            if hb.restart_seen_at and (not hb.time_valid):
                flags.append("BOOT")

                # OTA: if reboot likely caused by OTA (recent success status)
                if self._reboot_is_ota(hb, now):
                    flags.append("OTA")

            if stale:
                flags.append("STL")

            flag_str = f" ({', '.join(flags)})" if flags else ""
            up_field = f"{format_uptime(hb.up)}{flag_str}"

            row = [
                f"{hb.node:13}",
                f"{up_field:<14}",
                f"{(hb.fw or '?'):<6}",
                f"{self.format_build(hb.build):<7}",
                f"{self.format_heap(hb):<11}",
                f"{self.format_err(hb)}",
            ]
            rows.append(" ".join(row))

        if not rows:
            self._safe_print("(no heartbeats yet)")
        else:
            header = [
                f"{'node':13}",
                f"{'up':<14}",
                f"{'fw':<6}",
                f"{'build':<7}",
                f"{'heap':<11}",
                f"{'err'}",
            ]
            header_line = " ".join(header)
            self._safe_print(header_line)
            self._safe_print("-" * (len(header_line) + 9))
            for r in rows:
                self._safe_print(r)

        for warn in dict.fromkeys(warnings):
            self._safe_print(f"! {warn}")

        self._safe_flush()

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
    viewer = HeartbeatViewer(broker, port)
    viewer.run()


if __name__ == "__main__":
    main()
