#!/usr/bin/env python3
"""
piano_capture_receiver.py

Host receiver for ESP32 Piano Collector (COBS framing + CRC32).

- Reads framed binary packets delimited by 0x00
- COBS decodes each frame, verifies CRC32
- Handles TXT/CAP/DA message types
- Writes captures/<label>/repNNN_YYYYMMDD-HHMMSS/ with:
    raw_audio_i16.raw
    meta.json
- Rep numbering is monotonic per label across sessions
- Stdin thread forwards pasted multi-line commands to ESP32 robustly

Optional QC integration:
- After each rep is written, can run qc_captures.py on that rep folder.
- If QC marks bad and --auto_redo enabled, auto-sends redo command to ESP32.

Protocol (must match firmware):

TXT:
  b"TXT" + ver(u8) + msg_len(u16le) + msg(bytes) + crc32(u32le)

CAP:
  b"CAP" + ver(u8) + label(24 bytes, null padded) +
  rep(i32le) + reps(i32le) + fs(u32le) +
  pre_ms(u16le) + lead_ms(u16le) + post_ms(u16le) + flags(u16le) +
  total_samples(u32le) + clip_count(u32le) + max_abs(u16le) + rsv(u16le) +
  crc32(u32le)

DA:
  b"DA" + offset(u32le) + nsamp(u16le) + pcm(int16le * nsamp) + crc32(u32le)
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import serial


# -------------------------- CRC32 --------------------------

def crc32_ieee(data: bytes) -> int:
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


# -------------------------- COBS --------------------------

def cobs_decode(frame: bytes) -> bytes:
    """
    Strict COBS decode. `frame` must NOT include the delimiter 0x00.
    Raises ValueError on malformed input.
    """
    out = bytearray()
    i = 0
    n = len(frame)
    while i < n:
        code = frame[i]
        if code == 0:
            raise ValueError("COBS: code=0")
        i += 1
        end = i + code - 1
        if end > n:
            raise ValueError("COBS: overrun")
        out.extend(frame[i:end])
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


# -------------------------- Packet parsing --------------------------

def verify_crc(payload: bytes) -> bytes:
    """
    Returns payload_without_crc if CRC matches; raises ValueError otherwise.
    CRC is last 4 bytes u32le over everything before it.
    """
    if len(payload) < 4:
        raise ValueError("too short for crc")
    body = payload[:-4]
    crc_recv = struct.unpack_from("<I", payload, len(payload) - 4)[0]
    crc_calc = crc32_ieee(body)
    if crc_recv != crc_calc:
        raise ValueError(f"crc mismatch recv=0x{crc_recv:08x} calc=0x{crc_calc:08x}")
    return body


def parse_txt(body: bytes) -> str:
    if len(body) < 3 + 1 + 2:
        raise ValueError("TXT too short")
    if body[0:3] != b"TXT":
        raise ValueError("not TXT")
    ver = body[3]
    msg_len = struct.unpack_from("<H", body, 4)[0]
    msg_start = 6
    msg_end = msg_start + msg_len
    if msg_end > len(body):
        raise ValueError("TXT len out of range")
    msg = body[msg_start:msg_end].decode("utf-8", errors="replace")
    return f"[TXT v{ver}] {msg}"


def parse_txt_raw(body: bytes) -> Tuple[int, str]:
    """Return (ver, msg) for TXT packets."""
    if len(body) < 3 + 1 + 2:
        raise ValueError("TXT too short")
    if body[0:3] != b"TXT":
        raise ValueError("not TXT")
    ver = body[3]
    msg_len = struct.unpack_from("<H", body, 4)[0]
    msg_start = 6
    msg_end = msg_start + msg_len
    if msg_end > len(body):
        raise ValueError("TXT len out of range")
    msg = body[msg_start:msg_end].decode("utf-8", errors="replace")
    return int(ver), msg


@dataclass
class CapHeader:
    ver: int
    label: str
    rep_from_device: int
    reps_per_label: int
    fs: int
    pre_ms: int
    lead_ms: int
    post_ms: int
    flags: int
    total_samples: int
    clip_count: int
    max_abs: int

    @property
    def enter_sample(self) -> int:
        return int(round(self.fs * (self.pre_ms + self.lead_ms) / 1000.0))

    @property
    def nsamples(self) -> int:
        return int(self.total_samples)


def parse_cap(body: bytes) -> CapHeader:
    min_len = 3 + 1 + 24 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 4 + 4 + 2 + 2
    if len(body) < min_len:
        raise ValueError("CAP too short")
    if body[0:3] != b"CAP":
        raise ValueError("not CAP")
    ver = body[3]
    lbl_raw = body[4:28]
    label = lbl_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip().lower()

    off = 28
    rep_from_device, reps_per_label, fs = struct.unpack_from("<iiI", body, off)
    off += 12
    pre_ms, lead_ms, post_ms, flags = struct.unpack_from("<HHHH", body, off)
    off += 8
    total_samples, clip_count = struct.unpack_from("<II", body, off)
    off += 8
    max_abs, _rsv = struct.unpack_from("<HH", body, off)
    off += 4

    return CapHeader(
        ver=ver,
        label=label,
        rep_from_device=rep_from_device,
        reps_per_label=reps_per_label,
        fs=fs,
        pre_ms=pre_ms,
        lead_ms=lead_ms,
        post_ms=post_ms,
        flags=flags,
        total_samples=total_samples,
        clip_count=clip_count,
        max_abs=max_abs,
    )


def parse_da(body: bytes) -> Tuple[int, bytes]:
    if len(body) < 2 + 4 + 2:
        raise ValueError("DA too short")
    if body[0:2] != b"DA":
        raise ValueError("not DA")
    offset = struct.unpack_from("<I", body, 2)[0]
    nsamp = struct.unpack_from("<H", body, 6)[0]
    pcm_start = 8
    pcm_end = pcm_start + nsamp * 2
    if pcm_end > len(body):
        raise ValueError("DA pcm out of range")
    pcm = body[pcm_start:pcm_end]
    return int(offset), pcm


# -------------------------- Capture assembler --------------------------

REP_DIR_RE = re.compile(r"^rep(\d{3})_\d{8}-\d{6}$", re.IGNORECASE)


class CaptureAssembler:
    def __init__(self, out_root: Path):
        self.out_root = out_root
        self.active: Optional[CapHeader] = None
        self.buf: Optional[bytearray] = None
        self.filled: Optional[bytearray] = None
        self.started_at: Optional[float] = None
        self.out_dir: Optional[Path] = None
        self.rep_num: Optional[int] = None
        self.timestamp: Optional[str] = None

    def reset(self):
        self.active = None
        self.buf = None
        self.filled = None
        self.started_at = None
        self.out_dir = None
        self.rep_num = None
        self.timestamp = None

    def _next_rep_dir(self, label: str) -> Tuple[Path, int, str]:
        label_dir = self.out_root / label
        label_dir.mkdir(parents=True, exist_ok=True)

        max_rep = 0
        for p in label_dir.iterdir():
            if not p.is_dir():
                continue
            m = REP_DIR_RE.match(p.name)
            if m:
                max_rep = max(max_rep, int(m.group(1)))

        rep_num = max_rep + 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rep_name = f"rep{rep_num:03d}_{ts}"
        return label_dir / rep_name, rep_num, ts

    def begin(self, hdr: CapHeader):
        if self.active is not None:
            print("WARN: New CAP while previous capture active; abandoning previous.", file=sys.stderr)
            self.reset()

        self.active = hdr
        self.buf = bytearray(hdr.total_samples * 2)
        self.filled = bytearray(hdr.total_samples)
        self.started_at = time.time()

        out_dir, rep_num, ts = self._next_rep_dir(hdr.label)
        out_dir.mkdir(parents=True, exist_ok=False)
        self.out_dir = out_dir
        self.rep_num = rep_num
        self.timestamp = ts

        meta = {
            "label": hdr.label,
            "rep": rep_num,
            "timestamp": ts,
            "fs": hdr.fs,
            "format": "int16_le_mono",
            "pre_ms": hdr.pre_ms,
            "lead_ms": hdr.lead_ms,
            "post_ms": hdr.post_ms,
            "enter_sample": hdr.enter_sample,
            "nsamples": hdr.nsamples,
            "total_samples": hdr.total_samples,
            "device": {
                "proto_ver": hdr.ver,
                "flags": hdr.flags,
                "clip_count": hdr.clip_count,
                "max_abs": hdr.max_abs,
                "reps_per_label": hdr.reps_per_label,
                "rep_index_from_device": hdr.rep_from_device,
            },
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        print(
            f"CAP: label={hdr.label} rep={rep_num:03d} total={hdr.total_samples} fs={hdr.fs} "
            f"pre/lead/post={hdr.pre_ms}/{hdr.lead_ms}/{hdr.post_ms} enter_sample={hdr.enter_sample}"
        )

    def add_da(self, offset: int, pcm: bytes):
        if self.active is None or self.buf is None or self.filled is None:
            return
        hdr = self.active
        nsamp = len(pcm) // 2
        if offset < 0 or offset + nsamp > hdr.total_samples:
            print(f"WARN: DA out of range offset={offset} nsamp={nsamp}", file=sys.stderr)
            return

        self.buf[offset * 2:(offset + nsamp) * 2] = pcm
        self.filled[offset:offset + nsamp] = b"\x01" * nsamp

    def maybe_finalize(self) -> Optional[Path]:
        if self.active is None or self.buf is None or self.filled is None or self.out_dir is None:
            return None

        if self.filled.count(0) != 0:
            return None

        raw_path = self.out_dir / "raw_audio_i16.raw"
        raw_path.write_bytes(self.buf)

        meta_path = self.out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["received_seconds"] = float(time.time() - (self.started_at or time.time()))
        meta["written_files"] = ["raw_audio_i16.raw", "meta.json"]
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        out = self.out_dir
        print(f"WROTE: {out}")
        self.reset()
        return out


# -------------------------- Command gating / stdin --------------------------

class RunGate:
    """Gate 'start ...' commands so a pasted multi-line plan executes sequentially.

    - 'start ...' lines are queued while a run is active.
    - When TXT includes 'done run', the next queued start is sent.
    - Non-start lines (e.g., stop) are forwarded immediately.
    - Empty line (ENTER token) is forwarded immediately (sends a bare '\\n').
    """

    def __init__(self, txq: "queue.Queue[str]"):
        self._txq = txq
        self._lock = threading.Lock()
        self._busy = False
        self._pending_starts: "deque[str]" = deque()

    def enqueue_start(self, line: str):
        line = line.strip()
        if not line:
            return
        with self._lock:
            if not self._busy:
                self._busy = True
                self._safe_put(line)
            else:
                self._pending_starts.append(line)

    def send_immediate(self, line: str):
        # IMPORTANT: allow empty line to pass through as ENTER token
        line = line.rstrip("\r\n")
        self._safe_put(line)

    def on_txt_msg(self, msg: str):
        msg_l = (msg or "").strip().lower()
        if not msg_l:
            return

        with self._lock:
            if msg_l.startswith("run start"):
                self._busy = True

            if "done run" in msg_l:
                self._busy = False
                if self._pending_starts:
                    nxt = self._pending_starts.popleft()
                    self._busy = True
                    self._safe_put(nxt)

    def _safe_put(self, line: str):
        try:
            # allow empty string to represent ENTER token
            self._txq.put(line, timeout=0.5)
        except Exception:
            print("WARN: could not enqueue command", file=sys.stderr)


def stdin_reader_thread(inq: "queue.Queue[str]", stop_evt: threading.Event):
    """Read lines from stdin and push into inq (one line per command).

    IMPORTANT: Empty line (just Enter) is a valid command (ENTER token).
    """
    while not stop_evt.is_set():
        try:
            line = sys.stdin.readline()
            if line == "":
                time.sleep(0.05)
                continue
            line = line.rstrip("\r\n")
            # DO NOT drop empty lines: they are ENTER tokens for the firmware.
            inq.put(line, timeout=0.5)
        except Exception:
            time.sleep(0.05)


def cmd_scheduler_thread(
    inq: "queue.Queue[str]",
    gate: RunGate,
    stop_evt: threading.Event,
):
    """Route user commands into the RunGate."""
    while not stop_evt.is_set():
        try:
            line = inq.get(timeout=0.1)
        except queue.Empty:
            continue

        # Empty line => ENTER token, forward immediately
        if line == "":
            gate.send_immediate("")
            continue

        s = line.strip()
        if not s:
            # if someone pasted whitespace-only line, treat it as ENTER too
            gate.send_immediate("")
            continue

        s_l = s.lower()
        if s_l.startswith("start "):
            gate.enqueue_start(s)
        else:
            gate.send_immediate(s)


def tx_thread(ser: serial.Serial, txq: "queue.Queue[str]", stop_evt: threading.Event):
    while not stop_evt.is_set():
        try:
            line = txq.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            # line may be "" (ENTER token) => sends just "\n"
            ser.write((line + "\n").encode("utf-8", errors="ignore"))
            ser.flush()
        except Exception as e:
            print(f"WARN: TX error: {e}", file=sys.stderr)
            time.sleep(0.2)


# -------------------------- QC integration --------------------------

def run_qc_on_rep(
    qc_script: Path,
    rep_dir: Path,
    move_bad: bool,
    bad_dir: Path,
    write_aligned: bool,
    include_talkkey: bool,
    pre_align_ms: int,
    post_align_ms: int,
) -> Dict[str, Any]:
    cmd: List[str] = [
        sys.executable,
        str(qc_script),
        "--rep_dir", str(rep_dir),
        "--captures_dir", "captures",
        "--bad_dir", str(bad_dir),
        "--pre_align_ms", str(pre_align_ms),
        "--post_align_ms", str(post_align_ms),
    ]
    if move_bad:
        cmd.append("--move_bad")
    if write_aligned:
        cmd.append("--write_aligned")
    if include_talkkey:
        cmd.append("--include_talkkey")
    else:
        cmd.append("--no_talkkey")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        return {"status": "error", "error": f"qc subprocess failed: {e}", "is_bad": True}

    out = (proc.stdout or "").strip()
    if not out:
        return {"status": "error", "error": f"qc returned empty stdout (rc={proc.returncode})", "is_bad": True}

    try:
        return json.loads(out)
    except Exception as e:
        return {
            "status": "error",
            "error": f"qc stdout not json: {e}",
            "stdout": out[:4000],
            "stderr": (proc.stderr or "")[:4000],
            "is_bad": True,
        }


# -------------------------- Main receive loop --------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3", help="Serial port (default: COM3)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--out_root", default="captures", help="Root output dir (default: captures)")
    ap.add_argument("--read_chunk", type=int, default=4096, help="Serial read chunk size")

    # Resync / robustness
    ap.add_argument("--max_frame", type=int, default=65535, help="Drop any single frame larger than this")
    ap.add_argument("--flush_after_errors", type=int, default=50, help="Flush serial if this many consecutive decode errors occur")

    # QC integration
    ap.add_argument("--no_qc", dest="qc", action="store_false", help="Disable QC after each rep")
    ap.set_defaults(qc=True)

    ap.add_argument("--qc_script", default="qc_captures.py", help="Path to qc_captures.py")
    ap.add_argument("--no_qc_move_bad", dest="qc_move_bad", action="store_false", help="Do not move bad reps to bad_captures/")
    ap.set_defaults(qc_move_bad=True)
    ap.add_argument("--qc_bad_dir", default="bad_captures", help="bad_captures dir")
    ap.add_argument("--no_qc_write_aligned", dest="qc_write_aligned", action="store_false", help="Do not write aligned exports")
    ap.set_defaults(qc_write_aligned=True)
    ap.add_argument("--qc_include_talkkey", action="store_true", help="QC talkkey_* (default: enabled)")
    ap.add_argument("--qc_no_talkkey", action="store_true", help="Disable QC talkkey_* labels")
    ap.add_argument("--qc_pre_align_ms", type=int, default=0)
    ap.add_argument("--qc_post_align_ms", type=int, default=300)

    # Auto redo
    ap.add_argument("--no_auto_redo", dest="auto_redo", action="store_false", help="Disable auto redo on QC failure")
    ap.set_defaults(auto_redo=True)
    ap.add_argument("--redo_fmt", default="start {label} 1", help="Format for redo line to send")

    args = ap.parse_args()

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ser = serial.Serial(args.port, args.baud, timeout=0.02)
    time.sleep(0.2)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    assembler = CaptureAssembler(out_root=out_root)

    txq: "queue.Queue[str]" = queue.Queue(maxsize=4096)
    inq: "queue.Queue[str]" = queue.Queue(maxsize=4096)
    stop_evt = threading.Event()

    gate = RunGate(txq=txq)

    t_in = threading.Thread(target=stdin_reader_thread, args=(inq, stop_evt), daemon=True)
    t_sched = threading.Thread(target=cmd_scheduler_thread, args=(inq, gate, stop_evt), daemon=True)
    t_tx = threading.Thread(target=tx_thread, args=(ser, txq, stop_evt), daemon=True)
    t_in.start()
    t_sched.start()
    t_tx.start()

    print(f"Listening on {args.port} @ {args.baud} -> {out_root}")

    qc_enabled = bool(args.qc)
    qc_script = Path(args.qc_script)
    qc_bad_dir = Path(args.qc_bad_dir)

    include_talkkey = True
    if args.qc_no_talkkey:
        include_talkkey = False
    if args.qc_include_talkkey:
        include_talkkey = True

    if qc_enabled:
        print(
            f"QC: enabled move_bad={bool(args.qc_move_bad)} bad_dir={qc_bad_dir} "
            f"write_aligned={bool(args.qc_write_aligned)} include_talkkey={include_talkkey} "
            f"auto_redo={bool(args.auto_redo)} redo_fmt={args.redo_fmt!r}"
        )

    buf = bytearray()
    consecutive_decode_errors = 0

    def handle_written_rep(rep_dir: Path):
        if not qc_enabled:
            return

        res = run_qc_on_rep(
            qc_script=qc_script,
            rep_dir=rep_dir,
            move_bad=bool(args.qc_move_bad),
            bad_dir=qc_bad_dir,
            write_aligned=bool(args.qc_write_aligned),
            include_talkkey=include_talkkey,
            pre_align_ms=int(args.qc_pre_align_ms),
            post_align_ms=int(args.qc_post_align_ms),
        )

        status = res.get("status")
        is_bad = bool(res.get("is_bad", False))
        flags = res.get("flags", [])
        label = res.get("label", "")

        if status == "error":
            print(f"QC ERROR: {res.get('error','(unknown)')}", file=sys.stderr)
            if res.get("stderr"):
                print(f"QC STDERR:\n{res['stderr']}", file=sys.stderr)
            return

        if is_bad:
            print(f"QC: BAD label={label} flags={flags}")
            if args.auto_redo and label:
                line = args.redo_fmt.format(label=str(label).lower())
                print(f"AUTO-REDO -> {line}")
                gate.enqueue_start(line)
        else:
            print(f"QC: OK label={label}")

    try:
        while True:
            chunk = ser.read(args.read_chunk)
            if chunk:
                buf.extend(chunk)

                while True:
                    try:
                        z = buf.index(0)
                    except ValueError:
                        break

                    frame = bytes(buf[:z])
                    del buf[:z + 1]

                    if not frame:
                        continue

                    if len(frame) > int(args.max_frame):
                        print(f"WARN: dropping oversized frame len={len(frame)}", file=sys.stderr)
                        consecutive_decode_errors += 1
                        continue

                    try:
                        decoded = cobs_decode(frame)
                        body = verify_crc(decoded)
                        consecutive_decode_errors = 0
                    except Exception as e:
                        consecutive_decode_errors += 1
                        print(f"WARN: frame decode/crc error: {e}", file=sys.stderr)

                        if consecutive_decode_errors >= int(args.flush_after_errors):
                            print("WARN: too many decode errors -> flushing serial input for resync", file=sys.stderr)
                            try:
                                ser.reset_input_buffer()
                            except Exception:
                                pass
                            buf.clear()
                            assembler.reset()
                            consecutive_decode_errors = 0
                        continue

                    if body.startswith(b"TXT"):
                        try:
                            ver, msg = parse_txt_raw(body)
                            print(f"[TXT v{ver}] {msg}")
                            gate.on_txt_msg(msg)
                        except Exception as e:
                            print(f"WARN: TXT parse error: {e}", file=sys.stderr)

                    elif body.startswith(b"CAP"):
                        try:
                            hdr = parse_cap(body)
                            assembler.begin(hdr)
                        except Exception as e:
                            print(f"WARN: CAP parse error: {e}", file=sys.stderr)
                            assembler.reset()

                    elif body.startswith(b"DA"):
                        try:
                            offset, pcm = parse_da(body)
                            assembler.add_da(offset, pcm)
                            rep_written = assembler.maybe_finalize()
                            if rep_written is not None:
                                handle_written_rep(rep_written)
                        except Exception as e:
                            print(f"WARN: DA parse error: {e}", file=sys.stderr)

                    else:
                        print(f"WARN: unknown packet type: {body[:8]!r}", file=sys.stderr)

            else:
                time.sleep(0.002)

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        stop_evt.set()
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
