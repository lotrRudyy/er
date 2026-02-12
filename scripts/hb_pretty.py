#!/usr/bin/env python3
"""
hb_pretty.py — Human-friendly ER1 heartbeat viewer.

- Subscribes: +/hb, +/ota, +/log, time/state
- Prints a heartbeat table on each HB interval boundary (default 20s)
- ALSO prints an extra table immediately when a node restarts (uptime drops)
- Ctrl+C exits immediately

UI rules:
- Always show a FULL table immediately after start (short warmup then print once)
- Keep "heap" as the heap column title (values are just [..])
- Header separators: always exactly:
  ---------------------------------------------
- Do NOT print "(every 20s)" every time:
  - print it only once (first periodic dashboard)
  - when reboot/update prints, show reason instead

Error visibility:
- HB only contains err_cnt/err_code; it often doesn't contain the message.
- Therefore we also listen to +/log and cache last ERR/WRN line per node.
- When err_cnt increases, show Δ+N + last ERR/WRN message.
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

# Detect reboot if uptime dropped (with slack)
RESTART_DELTA = 5

# Treat reboot as OTA-caused if we saw a success-ish OTA status shortly before reboot
OTA_REBOOT_WINDOW_SEC = 180

# Print one "startup snapshot" after a short warmup so the first table is usually complete
STARTUP_WARMUP_SEC = float(os.environ.get("HB_STARTUP_WARMUP_SEC", "1.2"))

# If an err_cnt increase happened recently, keep showing the “+N msg” hint for this long.
ERR_BURST_WINDOW_SEC = 90.0

SEPARATOR = "---------------------------------------------"

# Column widths (compact; FW pulled left)
NODE_W = 13
UP_W = 10
FW_W = 3
HEAP_W = 7  # "[93/91]" is 7 chars


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
    s = (st or "").strip().upper()
    if not s:
        return False
    if "FAIL" in s or "ERROR" in s:
        return False
    return any(tok in s for tok in ("OK", "DONE", "SUCCESS", "END", "COMPLETE", "APPLY"))


def make_mqtt_client() -> mqtt.Client:
    # Use Callback API v2 to avoid deprecation warning, and match signatures below.
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        return mqtt.Client()


def _shorten_msg(s: str, max_len: int = 52) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


@dataclass
class Heartbeat:
    node: str
    fw: str = ""
    up: int = 0
    prev_up: Optional[int] = None
    ts: str = ""
    time_valid: bool = True
    heap_free: Optional[int] = None
    heap_min: Optional[int] = None
    heap_size: Optional[int] = None

    err_cnt: Optional[int] = None
    err_code: Optional[int] = None

    last_seen: float = field(default_factory=now_ts)

    # Reboot detection (uptime drop)
    restart_seen_at: Optional[float] = None

    # OTA status tracking
    ota_last_status: Optional[str] = None
    ota_last_seen_at: Optional[float] = None
    ota_last_success_at: Optional[float] = None

    # Last ERR/WRN log line seen
    last_log_lv: Optional[str] = None
    last_log_msg: Optional[str] = None
    last_log_seen_at: Optional[float] = None

    # Track err_cnt deltas to show “+N msg”
    last_err_bump_at: Optional[float] = None
    last_err_bump_delta: Optional[int] = None


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

        # Periodic table de-dupe per interval bucket (unless force=True)
        self.last_dashboard_bucket: Optional[int] = None

        # Print "(every 20s)" only once, on the first periodic dashboard
        self.did_print_interval_hint = False

        # Startup snapshot gate
        self.startup_snapshot_printed = False
        self.startup_snapshot_at = self.start_ts + STARTUP_WARMUP_SEC

        self._STOP_TOPIC = "__STOP__"

    def stop(self, *_: Any) -> None:
        self.running = False
        try:
            self.queue.put_nowait((self._STOP_TOPIC, "", now_ts()))
        except Exception:
            pass

    # ---- paho-mqtt Callback API v2 signatures ----
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        try:
            ok = int(reason_code) == 0
        except Exception:
            ok = (reason_code == 0)

        if not ok:
            print(f"[mqtt] connect failed reason_code={reason_code}", file=sys.stderr)
            self.running = False
            return

        client.subscribe("+/hb")
        client.subscribe("+/ota")
        client.subscribe("+/log")
        client.subscribe("time/state")

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, reason_code: Any, properties: Any = None) -> None:
        return

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")
        try:
            self.queue.put_nowait((msg.topic, payload, now_ts()))
        except Exception:
            pass

    @staticmethod
    def safe_json(payload: str) -> Any:
        try:
            return json.loads(payload)
        except Exception:
            return None

    def run(self) -> None:
        client = make_mqtt_client()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect(self.broker, self.port, 30)
        client.loop_start()

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        next_tick = next_interval_boundary(interval=HB_INTERVAL_SEC)

        try:
            while self.running:
                now = now_ts()

                if (not self.startup_snapshot_printed) and (now >= self.startup_snapshot_at):
                    self.print_dashboard(
                        boundary_ts=interval_floor(now, HB_INTERVAL_SEC),
                        force=True,
                        reason="startup",
                    )
                    self.startup_snapshot_printed = True

                timeout = min(0.25, max(0.0, next_tick - now_ts()))
                try:
                    topic, payload, recv_ts = self.queue.get(timeout=timeout)
                    if topic == self._STOP_TOPIC:
                        break
                    self.handle_message(topic, payload, recv_ts)
                except queue.Empty:
                    pass

                current_ts = now_ts()
                while current_ts >= next_tick:
                    self.print_dashboard(next_tick, force=False, reason=None)
                    next_tick += HB_INTERVAL_SEC
        finally:
            client.loop_stop()
            client.disconnect()

    def handle_message(self, topic: str, payload: str, recv_ts: float) -> None:
        if topic.endswith("/hb"):
            self.handle_hb(topic, payload, recv_ts)
        elif topic.endswith("/ota"):
            self.handle_ota(topic, payload, recv_ts)
        elif topic.endswith("/log"):
            self.handle_log(topic, payload, recv_ts)
        elif topic == "time/state":
            self.handle_time_state(payload, recv_ts)

    def handle_time_state(self, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return
        self.time_state = data
        self.time_state_seen_at = recv_ts

    def handle_log(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return

        node = topic.split("/")[0].strip() or "?"
        lv = str(data.get("lv") or "").strip().upper()
        msg = str(data.get("msg") or "").strip()

        # Only cache WARN/ERR (that’s what you actually care about)
        if lv not in ("WRN", "WARN", "ERR", "ERROR"):
            return

        rec = self.hb_cache.get(node, Heartbeat(node=node))
        rec.last_log_lv = "WRN" if lv in ("WRN", "WARN") else "ERR"
        rec.last_log_msg = _shorten_msg(msg, 64)
        rec.last_log_seen_at = recv_ts
        self.hb_cache[node] = rec

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

    def handle_hb(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            return

        node = str(data.get("node") or topic.split("/")[0])
        rec = self.hb_cache.get(node, Heartbeat(node=node))

        prev_up = rec.up if rec.up else rec.prev_up
        new_up = int_or_none(data.get("up"), rec.up)

        reboot_detected = False
        if prev_up is not None and new_up is not None and new_up >= 0 and (new_up + RESTART_DELTA) < prev_up:
            rec.restart_seen_at = recv_ts
            reboot_detected = True

        # Track err count deltas (this is how we decide when to display last ERR/WRN msg)
        prev_err_cnt = rec.err_cnt if rec.err_cnt is not None else 0
        new_err_cnt = int_or_none(data.get("err_cnt"), rec.err_cnt)
        if new_err_cnt is None:
            new_err_cnt = prev_err_cnt

        if new_err_cnt > prev_err_cnt:
            rec.last_err_bump_at = recv_ts
            rec.last_err_bump_delta = int(new_err_cnt - prev_err_cnt)
        # If it resets (reboot), just clear bump info
        if new_err_cnt < prev_err_cnt:
            rec.last_err_bump_at = None
            rec.last_err_bump_delta = None

        rec.prev_up = prev_up
        rec.up = new_up or 0
        rec.fw = str(data.get("fw") or rec.fw or "")
        rec.ts = str(data.get("ts") or rec.ts or "")
        rec.time_valid = bool(data.get("time_valid", True))

        rec.heap_free = int_or_none(data.get("heap_free"), rec.heap_free)
        rec.heap_min = int_or_none(data.get("heap_min"), rec.heap_min)
        rec.heap_size = int_or_none(data.get("heap_size"), rec.heap_size)

        rec.err_cnt = new_err_cnt
        rec.err_code = int_or_none(data.get("err_code"), rec.err_code)

        rec.last_seen = recv_ts
        self.hb_cache[node] = rec

        if reboot_detected:
            reason = f"restart {node}"
            if self._reboot_is_ota(rec):
                reason = f"ota {node}"
            self.print_dashboard(interval_floor(recv_ts, HB_INTERVAL_SEC), force=True, reason=reason)

        # Pull startup snapshot earlier once we have at least 2 nodes
        if (not self.startup_snapshot_printed) and (len(self.hb_cache) >= 2):
            self.startup_snapshot_at = min(self.startup_snapshot_at, now_ts() + 0.1)

    def _reboot_is_ota(self, hb: Heartbeat) -> bool:
        if hb.restart_seen_at is None or hb.ota_last_success_at is None:
            return False
        return 0.0 <= (hb.restart_seen_at - hb.ota_last_success_at) <= OTA_REBOOT_WINDOW_SEC

    def format_fw(self, fw: str) -> str:
        s = (fw or "").strip()
        if not s:
            return "?"
        return s if len(s) <= 3 else "??"

    def format_heap(self, hb: Heartbeat) -> str:
        size = hb.heap_size or 0
        if size <= 0:
            return "[n/a]"
        free = hb.heap_free
        hmin = hb.heap_min
        free_pct = int(free * 100 / size) if (free is not None) else None
        min_pct = int(hmin * 100 / size) if (hmin is not None) else None
        if free_pct is not None and min_pct is not None:
            free_pct = max(0, min(100, free_pct))
            min_pct = max(0, min(100, min_pct))
            return f"[{free_pct:02d}/{min_pct:02d}]"
        if free_pct is not None:
            free_pct = max(0, min(100, free_pct))
            return f"[{free_pct:02d}%]"
        if min_pct is not None:
            min_pct = max(0, min(100, min_pct))
            return f"[min{min_pct:02d}]"
        return "[n/a]"

    def format_err(self, hb: Heartbeat, now: float) -> str:
        """
        Always show err_cnt.
        If currently wrong (err_code != 0): append code + last ERR/WRN message.
        If err_cnt recently increased: append +N + last ERR/WRN message.
        """
        cnt = hb.err_cnt if hb.err_cnt is not None else 0

        # Prefer "currently wrong"
        if hb.err_code:
            extra = f" code={hb.err_code}"
            if hb.last_log_msg:
                extra += f" {hb.last_log_msg}"
            return f"{cnt}{extra}"

        # Otherwise: show recent bump hint
        if hb.last_err_bump_at and hb.last_err_bump_delta and (now - hb.last_err_bump_at) <= ERR_BURST_WINDOW_SEC:
            extra = f" +{hb.last_err_bump_delta}"
            if hb.last_log_msg:
                extra += f" {hb.last_log_msg}"
            return f"{cnt}{extra}"

        return f"{cnt}"

    def print_dashboard(self, boundary_ts: float, force: bool, reason: Optional[str]) -> None:
        bucket = int(boundary_ts // max(1, HB_INTERVAL_SEC))
        if (not force) and (self.last_dashboard_bucket is not None) and (bucket == self.last_dashboard_bucket):
            return
        if not force:
            self.last_dashboard_bucket = bucket

        header_ts = datetime.fromtimestamp(boundary_ts).strftime("%H:%M:%S")
        date_str = datetime.fromtimestamp(boundary_ts).strftime("%Y.%m.%d")

        if self.time_state:
            ts_field = str(self.time_state.get("ts") or "")
            if " - " in ts_field:
                try:
                    time_part, date_part = ts_field.split(" - ", 1)
                    if time_part.strip():
                        header_ts = time_part.strip().split(".")[0]
                    if date_part.strip():
                        date_str = date_part.strip().split()[0]
                except Exception:
                    pass

        paren = ""
        if force and reason:
            paren = f" ({reason})"
        elif (not self.did_print_interval_hint):
            paren = f" (every {HB_INTERVAL_SEC}s)"
            self.did_print_interval_hint = True

        print(SEPARATOR)
        print(f"HB | {header_ts} - {date_str}{paren}")
        print(SEPARATOR)

        header = (
            f"{'node':{NODE_W}} "
            f"{'up':<{UP_W}} "
            f"{'fw':<{FW_W}} "
            f"{'heap':<{HEAP_W}} "
            f"err"
        )
        print(header)
        print("-" * len(header))

        now = now_ts()
        rows = []
        for hb in sorted(self.hb_cache.values(), key=lambda r: (-r.up, r.node)):
            up_str = format_uptime(hb.up)
            row = (
                f"{hb.node:{NODE_W}} "
                f"{up_str:<{UP_W}} "
                f"{self.format_fw(hb.fw):<{FW_W}} "
                f"{self.format_heap(hb):<{HEAP_W}} "
                f"{self.format_err(hb, now)}"
            )
            rows.append(row)

        if rows:
            for r in rows:
                print(r)
        else:
            print("(no heartbeats yet)")

        warnings = []
        if self.time_state_seen_at and (now - self.time_state_seen_at) > TIME_STALE_SEC:
            warnings.append("time/state stale (>90s)")
        if (not self.time_state_seen_at) and ((now - self.start_ts) > TIME_STALE_SEC):
            warnings.append("time/state missing (>90s)")
        for w in dict.fromkeys(warnings):
            print(f"! {w}")

        try:
            sys.stdout.flush()
        except BrokenPipeError:
            self.stop()


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    HeartbeatViewer(broker, port).run()


if __name__ == "__main__":
    main()
