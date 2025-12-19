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
import os
import queue
import re
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial


# -------------------------- CRC32 --------------------------

def crc32_ieee(data: bytes) -> int:
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


# -------------------------- COBS --------------------------

def cobs_decode(frame: bytes) -> bytes:
    """
    COBS decode. `frame` must NOT include the delimiter 0x00.
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
        if end > n and code != 1:
            raise ValueError("COBS: overrun")
        out += frame[i:end]
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


# -------------------------- CAP session --------------------------

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
        # Enter is at end of (pre + lead)
        return int(round(self.fs * (self.pre_ms + self.lead_ms) / 1000.0))

    @property
    def nsamples(self) -> int:
        return int(self.total_samples)


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

    def _next_rep_dir(self, label: str) -> tuple[Path, int, str]:
        label_dir = self.out_root / label
        label_dir.mkdir(parents=True, exist_ok=True)

        # Find max existing repNNN_YYYYMMDD-HHMMSS
        max_rep = 0
        pat = re.compile(r"^rep(\d{3})_\d{8}-\d{6}$")
        for p in label_dir.iterdir():
            if not p.is_dir():
                continue
            m = pat.match(p.name)
            if m:
                max_rep = max(max_rep, int(m.group(1)))

        rep_num = max_rep + 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rep_name = f"rep{rep_num:03d}_{ts}"
        return label_dir / rep_name, rep_num, ts

    def begin(self, hdr: CapHeader):
        # If a capture is already active, abandon it safely
        if self.active is not None:
            print("WARN: New CAP while previous capture active; abandoning previous.", file=sys.stderr)
            self.reset()

        self.active = hdr
        self.buf = bytearray(hdr.total_samples * 2)
        self.filled = bytearray(hdr.total_samples)  # 0/1 per sample
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

    def maybe_finalize(self) -> bool:
        if self.active is None or self.buf is None or self.filled is None or self.out_dir is None:
            return False

        missing = self.filled.count(0)
        if missing != 0:
            return False

        raw_path = self.out_dir / "raw_audio_i16.raw"
        raw_path.write_bytes(self.buf)

        meta_path = self.out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["received_seconds"] = float(time.time() - (self.started_at or time.time()))
        meta["written_files"] = ["raw_audio_i16.raw", "meta.json"]
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        print(f"WROTE: {self.out_dir}")
        self.reset()
        return True


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


def parse_cap(body: bytes) -> CapHeader:
    if len(body) < 3 + 1 + 24 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 4 + 4 + 2 + 2:
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


def parse_da(body: bytes) -> tuple[int, bytes]:
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


# -------------------------- Stdin sender --------------------------

def stdin_sender_thread(ser: serial.Serial, txq: "queue.Queue[str]", stop_evt: threading.Event):
    """
    Reads stdin and pushes lines to txq.
    Robust for multi-line paste: Python already yields line-by-line.
    """
    while not stop_evt.is_set():
        try:
            line = sys.stdin.readline()
            if line == "":
                time.sleep(0.05)
                continue
            line = line.rstrip("\r\n")
            txq.put(line, timeout=0.5)
        except Exception:
            time.sleep(0.05)


def tx_thread(ser: serial.Serial, txq: "queue.Queue[str]", stop_evt: threading.Event):
    """
    Sends queued lines to serial with '\n'.
    """
    while not stop_evt.is_set():
        try:
            line = txq.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            ser.write((line + "\n").encode("utf-8", errors="ignore"))
            ser.flush()
        except Exception as e:
            print(f"WARN: TX error: {e}", file=sys.stderr)
            time.sleep(0.2)


# -------------------------- Main receive loop --------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="Serial port (e.g. COM5 or /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--out_root", default="captures", help="Root output dir (default: captures)")
    ap.add_argument("--read_chunk", type=int, default=4096, help="Serial read chunk size")
    args = ap.parse_args()

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ser = serial.Serial(args.port, args.baud, timeout=0.02)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    assembler = CaptureAssembler(out_root=out_root)

    txq: "queue.Queue[str]" = queue.Queue(maxsize=4096)
    stop_evt = threading.Event()

    t_in = threading.Thread(target=stdin_sender_thread, args=(ser, txq, stop_evt), daemon=True)
    t_tx = threading.Thread(target=tx_thread, args=(ser, txq, stop_evt), daemon=True)
    t_in.start()
    t_tx.start()

    print(f"Listening on {args.port} @ {args.baud} -> {out_root}")

    buf = bytearray()

    try:
        while True:
            chunk = ser.read(args.read_chunk)
            if chunk:
                buf.extend(chunk)

                # split frames by 0x00 delimiter
                while True:
                    try:
                        z = buf.index(0)
                    except ValueError:
                        break
                    frame = bytes(buf[:z])
                    del buf[:z + 1]

                    if not frame:
                        continue

                    try:
                        decoded = cobs_decode(frame)
                        body = verify_crc(decoded)
                    except Exception as e:
                        # resync strategy: just drop this frame and continue
                        print(f"WARN: frame decode/crc error: {e}", file=sys.stderr)
                        continue

                    # dispatch by header
                    if body.startswith(b"TXT"):
                        try:
                            print(parse_txt(body))
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
                            assembler.maybe_finalize()
                        except Exception as e:
                            print(f"WARN: DA parse error: {e}", file=sys.stderr)

                    else:
                        print(f"WARN: unknown packet type: {body[:8]!r}", file=sys.stderr)

            else:
                # idle
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
