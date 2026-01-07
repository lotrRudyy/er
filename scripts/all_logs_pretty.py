#!/usr/bin/env python3
"""
all_logs_pretty.py — Pretty ER1 log viewer (all nodes) with piano + OTA pretty blocks.

Current behavior:
- OTA:
  - Prints nice OTA timeline for live sessions.
  - Retained OTA messages at startup are allowed,
    BUT retained OTA_PROGRESS / OTA_FLASHED are suppressed to avoid the startup spam lines.

- time_resync:
  - Prints whenever it happens (normal periodic resyncs).
  - If it happens within the OTA reboot window, it is attached to the OTA block (and ends the block).

- paho-mqtt:
  - Compatible with callback signature including 'properties' (paho v2).
  - Uses classic mqtt.Client() (no callback_api_version) to avoid API mismatch crashes.
"""

import json
import os
import queue
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List, Set

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


SEPARATOR = "-" * 80
OTA_SEP = "-" * 97

QueueItem = Tuple[str, str, float, bool]  # topic, payload, recv_ts, retain


def now_ts() -> float:
    return time.time()


def int_or_none(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return default


def fnum(v: Any, fmt: str) -> Optional[str]:
    try:
        return format(float(v), fmt)
    except Exception:
        return None


def pad_score_line(label: str, score: Any, width: int) -> str:
    s = fnum(score, ".6f")
    if s is None:
        s = "?"
    return f"{(label + ':'):<{width}} {s}"


def parse_seq_string(seq: Any) -> List[str]:
    if isinstance(seq, str):
        return [p.strip() for p in seq.split(",") if p.strip()]
    if isinstance(seq, list):
        out: List[str] = []
        for x in seq:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    return []


def fmt_local_ts_ms(ts: float) -> str:
    lt = time.localtime(ts)
    ms = int((ts - int(ts)) * 1000)
    return time.strftime("%H:%M:%S", lt) + f".{ms:03d}" + " - " + time.strftime("%Y.%m.%d", lt)


def fmt_local_ts(ts: float) -> str:
    lt = time.localtime(ts)
    return time.strftime("%H:%M:%S", lt) + " - " + time.strftime("%Y.%m.%d", lt)


def strip_ms_from_device_ts(ts: str) -> str:
    """Normalize device timestamp strings by removing milliseconds.

    Accepts:
      - "HH:MM:SS.mmm - YYYY.MM.DD"
      - "HH:MM:SS - YYYY.MM.DD"
    Returns:
      - "HH:MM:SS - YYYY.MM.DD" (or original if not matching)
    """
    if not isinstance(ts, str):
        return ""
    if " - " not in ts:
        return ts
    time_part, date_part = ts.split(" - ", 1)
    if "." in time_part:
        time_part = time_part.split(".", 1)[0]
    return f"{time_part} - {date_part}"


@dataclass
class PianoNoteCompat:
    ts: str
    pred: str
    margin: str
    top3: List[Tuple[str, Any]]
    verdict: Optional[str]  # "accepted" | "rejected" | None


@dataclass
class OtaPrettySession:
    node: str
    ota_id: str
    version: Optional[str] = None
    created_at: float = field(default_factory=now_ts)
    header_printed: bool = False
    printed_lines: Set[str] = field(default_factory=set)
    last_pct: Optional[int] = None
    ended: bool = False


class LogsAllPretty:
    def __init__(self, broker: str, port: int) -> None:
        self.broker = broker
        self.port = port
        self.queue: "queue.Queue[QueueItem]" = queue.Queue()
        self.running = True
        self._STOP_TOPIC = "__STOP__"

        # Piano state
        self.piano_seq_state: Dict[str, Dict[str, Any]] = {}
        self.last_note_compat: Dict[str, PianoNoteCompat] = {}

        # OTA sessions and "recent flash" window to attach reboot logs
        self.ota_sessions: Dict[str, Dict[str, OtaPrettySession]] = {}
        self.ota_recent_flash: Dict[str, Tuple[str, float]] = {}  # node -> (id, flashed_ts)

        # Pending id=? invalid_sha256 (held briefly so we can drop if real START follows)
        self.pending_ota_fail: Dict[str, Tuple[float, str]] = {}  # node -> (ts, reason)
        self.PENDING_FAIL_TTL = 5.0

    def stop(self, *_: Any) -> None:
        self.running = False
        try:
            self.queue.put_nowait((self._STOP_TOPIC, "", now_ts(), False))
        except Exception:
            pass

    # paho v2 calls: on_connect(client, userdata, flags, rc, properties)
    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        rc: int,
        _properties: Any = None,
    ) -> None:
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}", file=sys.stderr)
            self.running = False
            return
        client.subscribe("+/log")
        client.subscribe("+/ota")

    def _on_message(self, _c: mqtt.Client, _u: Any, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")
        retain = bool(getattr(msg, "retain", False))
        try:
            self.queue.put_nowait((msg.topic, payload, now_ts(), retain))
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
                    topic, payload, ts_recv, retain = self.queue.get(timeout=0.25)
                    if topic == self._STOP_TOPIC:
                        break
                    if topic.endswith("/log"):
                        self.handle_log(topic, payload, ts_recv)
                    elif topic.endswith("/ota"):
                        self.handle_ota(topic, payload, ts_recv, retain)
                except queue.Empty:
                    pass

                self.flush_pending_ota_fails()
        finally:
            client.loop_stop()
            client.disconnect()

    def _p(self, s: str) -> None:
        try:
            print(s)
            sys.stdout.flush()
        except BrokenPipeError:
            self.stop()

    # ----------------- piano helpers -----------------
    def infer_detector_verdict(self, msg: str, d: Dict[str, Any]) -> Optional[str]:
        t_field = d.get("t")
        if isinstance(t_field, str):
            t = t_field.strip().upper()
            if t == "ACC":
                return "accepted"
            if t == "REJ":
                return "rejected"
        if "ACC_COMPAT" in msg:
            return "accepted"
        if "REJ_COMPAT" in msg:
            return "rejected"
        return None

    def reset_piano_state(self) -> None:
        reset_state = {"progress": None, "seq_list": [], "ts": ""}
        self.piano_seq_state["piano"] = dict(reset_state)
        self.piano_seq_state["images_piano"] = dict(reset_state)
        self.last_note_compat.pop("piano", None)
        self.last_note_compat.pop("images_piano", None)

    def print_piano_block(
        self,
        ts: str,
        pred: str,
        verdict: str,
        progress: Optional[int],
        seq_list: List[str],
        margin: str,
        top3: List[Tuple[str, Any]],
    ) -> None:
        prog_str = "?" if progress is None else str(progress)
        header = f"{ts} | piano | {pred} {verdict} | progress: {prog_str}"
        seq_line = " - ".join(seq_list) if seq_list else "(no seq)"

        label_width = 6
        if top3:
            label_width = max(label_width, max(len(p) + 1 for p, _ in top3))

        self._p(SEPARATOR)
        self._p(header)
        self._p(SEPARATOR)
        self._p(f"seq: {seq_line}")
        self._p(f"margin={margin}")
        for p, s in top3:
            self._p(pad_score_line(p, s, label_width))

    # ----------------- OTA helpers -----------------
    def get_ota_session(self, node: str, ota_id: str, created_at: float) -> OtaPrettySession:
        sess = self.ota_sessions.get(node, {}).get(ota_id)
        if sess is None:
            sess = OtaPrettySession(node=node, ota_id=ota_id, created_at=created_at)
            self.ota_sessions.setdefault(node, {})[ota_id] = sess
        return sess

    def print_ota_header_if_needed(self, sess: OtaPrettySession) -> None:
        if sess.header_printed:
            return
        ver = sess.version or "?"
        self._p(OTA_SEP)
        self._p(f"{fmt_local_ts_ms(sess.created_at)} | OTA | {sess.node} | v={ver} | id={sess.ota_id}")
        self._p(OTA_SEP)
        sess.header_printed = True

    def ota_line(self, sess: OtaPrettySession, when_ts: float, text: str) -> None:
        line = f"{fmt_local_ts(when_ts)} | {text}"
        if line in sess.printed_lines:
            return
        sess.printed_lines.add(line)
        self._p(line)

    def ota_flashing_line(self, sess: OtaPrettySession, when_ts: float, pct: int) -> None:
        line = f"{fmt_local_ts(when_ts)} | FLASHING - {pct:3d}%"
        if line in sess.printed_lines:
            return
        sess.printed_lines.add(line)
        self._p(line)

    def ota_end_if_needed(self, sess: OtaPrettySession, when_ts: float) -> None:
        if sess.ended:
            return
        self.ota_line(sess, when_ts, "END")
        sess.ended = True

    def flush_pending_ota_fails(self) -> None:
        now = now_ts()
        for node, (ts_fail, reason) in list(self.pending_ota_fail.items()):
            if now - ts_fail >= self.PENDING_FAIL_TTL:
                sess = self.get_ota_session(node, "?", ts_fail)
                self.print_ota_header_if_needed(sess)
                self.ota_line(sess, ts_fail, f"FAIL {reason}")
                self.ota_end_if_needed(sess, ts_fail)
                del self.pending_ota_fail[node]

    def attach_log_to_recent_ota(self, node: str, ts_recv: float, msg: str, ts_str: str) -> bool:
        recent = self.ota_recent_flash.get(node)
        if not recent:
            return False
        ota_id, flashed_at = recent
        if ts_recv - flashed_at > 60:
            return False

        sess = self.get_ota_session(node, ota_id, flashed_at)
        self.print_ota_header_if_needed(sess)

        if "OTA FLASHED, rebooting" in msg:
            self.ota_line(sess, ts_recv, "REBOOT")
            return True
        if "MQTT connected" in msg:
            self.ota_line(sess, ts_recv, "MQTT connected")
            return True
        if "time_resync delta_s=" in msg:
            delta = "?"
            try:
                delta = msg.split("delta_s=", 1)[1].strip()
            except Exception:
                pass
            # prefer device timestamp string if present (but remove milliseconds)
            when = strip_ms_from_device_ts(ts_str) if ts_str else fmt_local_ts(ts_recv)
            line = f"{when} | time_resync: {delta}"
            if line not in sess.printed_lines:
                sess.printed_lines.add(line)
                self._p(line)
            self.ota_end_if_needed(sess, ts_recv)
            return True

        return False

    # ----------------- LOG HANDLING -----------------
    def handle_log(self, topic: str, payload: str, recv_ts: float) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self._p(f"[unparsed] {topic}: {payload}")
            return

        node = topic.split("/")[0]
        level = str(data.get("lv") or data.get("level") or "?")
        ts = str(data.get("ts") or "")
        msg = str(data.get("msg") or "")
        d = data.get("d")

        # Attach reboot-tail logs to OTA block
        if self.attach_log_to_recent_ota(node, recv_ts, msg, ts):
            return

        # Print time_resync always (outside OTA it becomes a normal single-line event)
        if "time_resync delta_s=" in msg:
            delta = "?"
            try:
                delta = msg.split("delta_s=", 1)[1].strip()
            except Exception:
                pass
            when = strip_ms_from_device_ts(ts) if ts else fmt_local_ts(recv_ts)
            self._p(f"{when} | time_resync: {delta}")
            return

        # Normalize log level announcements into a clean single-line event
        # Example: "log_level set to INF" -> "HH:MM:SS - YYYY.MM.DD | log_level: INF"
        if msg.startswith("log_level set to"):
            lv_set = msg.split("log_level set to", 1)[1].strip()
            when = strip_ms_from_device_ts(ts) if ts else fmt_local_ts(recv_ts)
            self._p(f"{when} | log_level: {lv_set}")
            return

        # Suppress noisy OTA-related logs that duplicate OTA block
        if msg.startswith("CMD topic=") and "msg=UPDATE" in msg:
            return
        if msg.startswith("OTA_START id="):
            return
        if msg.startswith("CMD UPDATE -> HTTP OTA"):
            return
        if "OTA missing/invalid sha256" in msg:
            self.pending_ota_fail[node] = (recv_ts, "invalid_sha256")
            return

        # Piano
        if node in {"images_piano", "piano"}:
            if "REJ_COMPAT" in msg and isinstance(d, dict):
                pred = str(d.get("pred") or "?")
                margin = fnum(d.get("margin"), ".4f") or "?"

                top3: List[Tuple[str, Any]] = []
                top = d.get("top")
                if isinstance(top, list):
                    for item in top[:3]:
                        if isinstance(item, dict) and "p" in item and "s" in item:
                            top3.append((str(item.get("p")), item.get("s")))

                self.print_piano_block(
                    ts=ts or fmt_local_ts_ms(recv_ts),
                    pred=pred,
                    verdict="rejected",
                    progress=None,
                    seq_list=[],
                    margin=margin,
                    top3=top3,
                )
                self.reset_piano_state()
                return

            if "NOTE_COMPAT" in msg and isinstance(d, dict):
                pred = str(d.get("pred") or "?")
                margin = fnum(d.get("margin"), ".4f") or "?"

                top3: List[Tuple[str, Any]] = []
                top = d.get("top")
                if isinstance(top, list):
                    for item in top[:3]:
                        if isinstance(item, dict) and "p" in item and "s" in item:
                            top3.append((str(item.get("p")), item.get("s")))

                verdict = self.infer_detector_verdict(msg, d)
                self.last_note_compat[node] = PianoNoteCompat(
                    ts=ts,
                    pred=pred,
                    margin=margin,
                    top3=top3,
                    verdict=verdict,
                )
                return

            if "PIANO_SEQ" in msg and isinstance(d, dict):
                seq_list = parse_seq_string(d.get("seq"))
                prog_raw = d.get("progress")
                try:
                    prog_i: Optional[int] = int(prog_raw) if prog_raw is not None else None
                except Exception:
                    prog_i = None

                self.piano_seq_state[node] = {"progress": prog_i, "seq_list": seq_list, "ts": ts}

                note = self.last_note_compat.get(node) or self.last_note_compat.get(
                    "piano" if node == "images_piano" else "images_piano"
                )

                if note is None:
                    pred = seq_list[-1] if seq_list else "?"
                    self.print_piano_block(
                        ts=ts or fmt_local_ts_ms(recv_ts),
                        pred=pred,
                        verdict="accepted",
                        progress=prog_i,
                        seq_list=seq_list,
                        margin="?",
                        top3=[],
                    )
                    return

                verdict = note.verdict or "accepted"
                self.print_piano_block(
                    ts=note.ts or ts or fmt_local_ts_ms(recv_ts),
                    pred=note.pred,
                    verdict=verdict,
                    progress=prog_i,
                    seq_list=seq_list,
                    margin=note.margin,
                    top3=note.top3,
                )
                return

        # Generic log
        header = f"{node}/log {level}"
        if ts:
            header += f" {ts}"

        if "\n" in msg:
            self._p(header)
            for line in msg.splitlines():
                self._p(line)
        else:
            self._p(f"{header} {msg}")

    # ----------------- OTA HANDLING -----------------
    def handle_ota(self, topic: str, payload: str, recv_ts: float, retain: bool) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self._p(f"[unparsed] {topic}: {payload}")
            return

        node = topic.split("/")[0]
        status = str(data.get("st") or "")

        details_raw = data.get("d") or {}
        details: Any = details_raw
        if isinstance(details_raw, str):
            try:
                details = json.loads(details_raw)
            except Exception:
                details = {}
        if not isinstance(details, dict):
            details = {}

        ota_id = str(details.get("id") or "?")
        reason = str(details.get("reason") or "")
        ver = details.get("version")
        if ver is not None:
            ver = str(ver)

        # Keep retained OTA messages at start,
        # but suppress retained timeline spam lines:
        if retain and status in {"OTA_PROGRESS", "OTA_FLASHED"}:
            return

        # Hold id=? invalid_sha256 briefly so we can drop it if a real START follows
        if status == "OTA_FAIL" and ota_id == "?" and "invalid_sha256" in reason:
            self.pending_ota_fail[node] = (recv_ts, "invalid_sha256")
            return

        if status == "OTA_START":
            self.pending_ota_fail.pop(node, None)

        sess = self.get_ota_session(node, ota_id, recv_ts)
        if ver:
            sess.version = ver

        self.print_ota_header_if_needed(sess)

        if status == "OTA_START":
            self.ota_line(sess, recv_ts, "START")

        elif status == "OTA_PROGRESS":
            pct = int_or_none(details.get("pct"))
            if pct is not None:
                sess.last_pct = pct
                self.ota_flashing_line(sess, recv_ts, pct)

        elif status == "OTA_FLASHED":
            if sess.last_pct is None or sess.last_pct < 100:
                self.ota_flashing_line(sess, recv_ts, 100)
                sess.last_pct = 100
            self.ota_line(sess, recv_ts, "FLASHED")
            self.ota_recent_flash[node] = (ota_id, recv_ts)

        elif status == "OTA_OK":
            self.ota_line(sess, recv_ts, "OK")
            self.ota_end_if_needed(sess, recv_ts)

        elif status == "OTA_FAIL":
            if reason:
                self.ota_line(sess, recv_ts, f"FAIL {reason}")
            else:
                self.ota_line(sess, recv_ts, "FAIL")
            self.ota_end_if_needed(sess, recv_ts)

        else:
            self.ota_line(sess, recv_ts, status or "OTA")


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    LogsAllPretty(broker, port).run()


if __name__ == "__main__":
    main()
