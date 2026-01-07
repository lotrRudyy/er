#!/usr/bin/env python3
"""
logs_all_pretty.py — Pretty ER1 log + OTA streamer (all nodes).

- Subscribes to: +/log and +/ota
- Pretty-prints ALL logs from ALL nodes (multiline preserved)
- Piano formatting:
    - Cache NOTE_COMPAT details (pred/margin/top3 + ACC/REJ verdict from detector)
    - Print ONLY when PIANO_SEQ arrives (no wait time)
      using the cached NOTE_COMPAT that arrived immediately before it
    - Explicit REJ_COMPAT prints immediately as "<note> rejected"
      with progress "?" and seq "(no seq)" (never shows previous seq/progress)
      and resets internal seq/progress caches
- OTA:
    - Prints compact OTA event lines (deduped), avoids repeating giant tables
- Ctrl+C exits immediately

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
from typing import Any, Dict, Optional, Tuple, List

try:
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError as exc:
    sys.stderr.write(f"paho-mqtt is required: {exc}\n")
    sys.exit(1)


SEPARATOR = "-" * 80
QueueItem = Tuple[str, str, float]


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


@dataclass
class OtaSession:
    node: str
    id: str
    status: str = ""
    pct: Optional[int] = None
    version: Optional[str] = None
    target: Optional[str] = None
    bytes: Optional[int] = None
    sha256: Optional[str] = None
    url: Optional[str] = None
    reason: Optional[str] = None
    last_details: Dict[str, Any] = field(default_factory=dict)
    last_update: float = field(default_factory=now_ts)


@dataclass
class PianoNoteCompat:
    ts: str
    pred: str
    margin: str
    top3: List[Tuple[str, Any]]
    verdict: Optional[str]  # "accepted" | "rejected" | None
    seen_at: float = field(default_factory=now_ts)


class LogsAllPretty:
    def __init__(self, broker: str, port: int) -> None:
        self.broker = broker
        self.port = port
        self.queue: "queue.Queue[QueueItem]" = queue.Queue()
        self.running = True
        self._STOP_TOPIC = "__STOP__"

        # OTA
        self.ota_sessions: Dict[str, Dict[str, OtaSession]] = {}
        self.last_ota_line: Dict[Tuple[str, str], str] = {}

        # Piano
        # node -> {progress:int|None, seq_list:list[str], ts:str}
        self.piano_seq_state: Dict[str, Dict[str, Any]] = {}
        # node -> last NOTE_COMPAT that arrived immediately before seq
        self.last_note_compat: Dict[str, PianoNoteCompat] = {}

    def stop(self, *_: Any) -> None:
        self.running = False
        try:
            self.queue.put_nowait((self._STOP_TOPIC, "", now_ts()))
        except Exception:
            pass

    def _on_connect(self, client: mqtt.Client, _u: Any, _f: Any, rc: int) -> None:
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}", file=sys.stderr)
            self.running = False
            return
        client.subscribe("+/log")
        client.subscribe("+/ota")

    def _on_message(self, _c: mqtt.Client, _u: Any, msg: mqtt.MQTTMessage) -> None:
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
                    topic, payload, ts_recv = self.queue.get(timeout=0.25)
                    if topic == self._STOP_TOPIC:
                        break
                    if topic.endswith("/log"):
                        self.handle_log(topic, payload)
                    elif topic.endswith("/ota"):
                        self.handle_ota(topic, payload, ts_recv)
                except queue.Empty:
                    pass
        finally:
            client.loop_stop()
            client.disconnect()

    # ------------------------------------------------------------------
    # Piano helpers
    # ------------------------------------------------------------------
    def print_piano_block(
        self,
        ts: str,
        pred: str,
        verdict: str,  # accepted/rejected/?
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

    def infer_detector_verdict(self, msg: str, d: Dict[str, Any]) -> Optional[str]:
        # Prefer explicit structured tag if present
        t_field = d.get("t")
        if isinstance(t_field, str):
            t = t_field.strip().upper()
            if t == "ACC":
                return "accepted"
            if t == "REJ":
                return "rejected"
        # Fallback to message content
        if "ACC_COMPAT" in msg:
            return "accepted"
        if "REJ_COMPAT" in msg:
            return "rejected"
        return None

    def reset_piano_state(self, ts: str) -> None:
        # Set both nodes to empty so we never accidentally reuse older seq/progress
        reset_state = {"progress": None, "seq_list": [], "ts": ts}
        self.piano_seq_state["piano"] = dict(reset_state)
        self.piano_seq_state["images_piano"] = dict(reset_state)
        self.last_note_compat.pop("piano", None)
        self.last_note_compat.pop("images_piano", None)

    # ------------------------------------------------------------------
    # LOG HANDLING
    # ------------------------------------------------------------------
    def handle_log(self, topic: str, payload: str) -> None:
        data = self.safe_json(payload)
        if not isinstance(data, dict):
            self._p(f"[unparsed] {topic}: {payload}")
            return

        node = topic.split("/")[0]
        level = data.get("lv") or data.get("level") or "?"
        ts = data.get("ts") or ""
        msg = str(data.get("msg") or "")
        d = data.get("d")

        # ============================
        # PIANO SPECIAL CASES
        # ============================
        if node in {"images_piano", "piano"}:
            # REJ_COMPAT: print immediately with empty seq and progress "0"
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
                    ts=ts,
                    pred=pred,
                    verdict="rejected",
                    progress=None,   # MUST be "?"
                    seq_list=[],     # MUST be "(no seq)"
                    margin=margin,
                    top3=top3,
                )
                self.reset_piano_state(ts)
                return

            # NOTE_COMPAT: cache details (do not print)
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

            # PIANO_SEQ: update state and print using cached NOTE_COMPAT
            if "PIANO_SEQ" in msg and isinstance(d, dict):
                seq_list = parse_seq_string(d.get("seq"))
                prog_raw = d.get("progress")
                try:
                    prog_i: Optional[int] = int(prog_raw) if prog_raw is not None else None
                except Exception:
                    prog_i = None

                self.piano_seq_state[node] = {
                    "progress": prog_i,
                    "seq_list": seq_list,
                    "ts": ts,
                }

                # Pick last note compat from the same node, else other
                note = self.last_note_compat.get(node) or self.last_note_compat.get(
                    "piano" if node == "images_piano" else "images_piano"
                )

                # If no NOTE_COMPAT cached, still print something minimal
                if note is None:
                    pred = seq_list[-1] if seq_list else "?"
                    self.print_piano_block(
                        ts=ts,
                        pred=pred,
                        verdict="accepted",  # reaching PIANO_SEQ implies accepted-by-detector stream
                        progress=prog_i,
                        seq_list=seq_list,
                        margin="?",
                        top3=[],
                    )
                    return

                # accepted/rejected ONLY from detector (if absent, default accepted because it reached seq stream)
                verdict = note.verdict or "accepted"

                self.print_piano_block(
                    ts=note.ts or ts,
                    pred=note.pred,
                    verdict=verdict,
                    progress=prog_i,
                    seq_list=seq_list,
                    margin=note.margin,
                    top3=note.top3,
                )
                return

        # ============================
        # GENERIC LOG
        # ============================
        header = f"{node}/log {level}"
        if ts:
            header += f" {ts}"

        msg_out = str(data.get("msg") or "")
        if "\n" in msg_out:
            self._p(header)
            for line in msg_out.splitlines():
                self._p(line)
        else:
            self._p(f"{header} {msg_out}")

    # ------------------------------------------------------------------
    # OTA HANDLING
    # ------------------------------------------------------------------
    def handle_ota(self, topic: str, payload: str, recv_ts: float) -> None:
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
        sess = self.ota_sessions.get(node, {}).get(ota_id) or OtaSession(node=node, id=ota_id)

        sess.status = status
        sess.last_update = recv_ts
        sess.last_details = details
        sess.version = str(details.get("version") or sess.version or "") or sess.version
        sess.target = str(details.get("target") or sess.target or "") or sess.target
        sess.url = str(details.get("url") or sess.url or "") or sess.url
        sess.pct = int_or_none(details.get("pct"), sess.pct)
        sess.bytes = int_or_none(details.get("bytes"), sess.bytes)
        sess.sha256 = str(details.get("sha256") or sess.sha256 or "") or sess.sha256
        sess.reason = str(details.get("reason") or "") or sess.reason

        self.ota_sessions.setdefault(node, {})[ota_id] = sess
        self.print_ota_event(sess)

    def print_ota_event(self, sess: OtaSession) -> None:
        parts = [
            "OTA",
            f"node={sess.node}",
            f"id={sess.id}",
            f"st={sess.status}",
        ]
        if sess.pct is not None:
            parts.append(f"pct={sess.pct}%")
        if sess.version:
            parts.append(f"ver={sess.version}")
        if sess.target:
            parts.append(f"tgt={sess.target}")
        if sess.reason:
            parts.append(f"reason={sess.reason}")

        line = " ".join(parts)
        key = (sess.node, sess.id)
        if self.last_ota_line.get(key) == line:
            return
        self.last_ota_line[key] = line

        ts_local = time.strftime("%H:%M:%S", time.localtime(sess.last_update))
        self._p(f"{ts_local} | {line}")

    # ------------------------------------------------------------------
    def _p(self, s: str) -> None:
        try:
            print(s)
            sys.stdout.flush()
        except BrokenPipeError:
            self.stop()


def main() -> None:
    broker = os.environ.get("LOCAL_BROKER", "127.0.0.1")
    port = int(os.environ.get("LOCAL_BROKER_PORT", "1883"))
    LogsAllPretty(broker, port).run()


if __name__ == "__main__":
    main()
