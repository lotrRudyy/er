import os
import re
import time
import json
import zlib
import struct
import threading
import serial
from cobs import cobs

# ==============================
# CONFIG
# ==============================
PORT = "COM3"
BAUD = 921600
OUT_DIR = "captures"
os.makedirs(OUT_DIR, exist_ok=True)

REP_RE = re.compile(r"^rep(\d+)_\d{8}-\d{6}$")

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF

def norm_label(s: str) -> str:
    return (s or "").strip().lower()

def timestamp_tag() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())

def next_rep_index(label_dir: str) -> int:
    mx = 0
    try:
        for name in os.listdir(label_dir):
            m = REP_RE.match(name)
            if m:
                mx = max(mx, int(m.group(1)))
    except FileNotFoundError:
        return 1
    return mx + 1

# ==============================
# SERIAL OPEN
# ==============================
ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2.0)
ser.reset_input_buffer()
ser.reset_output_buffer()
print(f"[OK] Connected to {PORT} @ {BAUD}")
print("[OK] Type commands here (e.g. start c4 10, start silence 30). Empty line triggers capture when prompted.")

# ==============================
# COMMAND THREAD (input())
# ==============================
def command_thread():
    while True:
        try:
            cmd = input()
            ser.write((cmd.rstrip("\r\n") + "\n").encode("ascii", errors="ignore"))
            ser.flush()
        except EOFError:
            break
        except Exception as e:
            print("[CMD ERROR]", e)
            break

threading.Thread(target=command_thread, daemon=True).start()

# ==============================
# RECEIVE STATE
# ==============================
buf = bytearray()
current = None
proto_ver = None

def new_capture(label_s, fw_rep, fw_reps, fs, pre_ms, lead_ms, post_ms, total_samples, clip_count, max_abs):
    return {
        "label": label_s,
        "fw_rep": fw_rep,
        "fw_reps_per_label": fw_reps,
        "fs": fs,
        "pre_ms": pre_ms,
        "lead_ms": lead_ms,
        "post_ms": post_ms,
        "total_samples": total_samples,
        "clip_count": clip_count,
        "max_abs": max_abs,
        "samples": bytearray(total_samples * 2),
        "filled": bytearray(total_samples),
        "received_samples": 0,
    }

while True:
    b = ser.read(1)
    if not b:
        continue

    if b == b"\x00":
        if not buf:
            continue

        try:
            pkt = cobs.decode(bytes(buf))
        except Exception:
            buf.clear()
            continue
        buf.clear()

        # ---------- TXT ----------
        if len(pkt) >= 3 and pkt[:3] == b"TXT":
            if len(pkt) < 3 + 1 + 2 + 4:
                continue
            ver = pkt[3]
            proto_ver = ver
            text_len = struct.unpack("<H", pkt[4:6])[0]
            if len(pkt) < 3 + 1 + 2 + text_len + 4:
                continue

            frame_wo_crc = pkt[:-4]
            rx_crc = struct.unpack("<I", pkt[-4:])[0]
            if crc32(frame_wo_crc) != rx_crc:
                continue

            msg = pkt[6:6 + text_len].decode("utf-8", errors="replace")
            print(msg)
            continue

        # ---------- CAP ----------
        # "CAP"(3) + ver(1) + label(24)
        # + rep(i32) + reps(i32) + fs(u32)
        # + pre(u16) + lead(u16) + post(u16) + flags(u16)
        # + total(u32) + clip(u32) + max_abs(u16) + rsv(u16) + crc(u32)
        if len(pkt) >= 3 and pkt[:3] == b"CAP":
            min_len = 3 + 1 + 24 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 4 + 4 + 2 + 2 + 4
            if len(pkt) < min_len:
                continue

            frame_wo_crc = pkt[:-4]
            rx_crc = struct.unpack("<I", pkt[-4:])[0]
            if crc32(frame_wo_crc) != rx_crc:
                print("[WARN] CAP CRC mismatch")
                continue

            ver = pkt[3]
            proto_ver = ver

            lbl_raw = pkt[4:28].decode("ascii", errors="ignore").strip("\0")
            lbl = norm_label(lbl_raw)

            fw_rep = struct.unpack("<i", pkt[28:32])[0]
            fw_reps = struct.unpack("<i", pkt[32:36])[0]
            fs = struct.unpack("<I", pkt[36:40])[0]
            pre_ms = struct.unpack("<H", pkt[40:42])[0]
            lead_ms = struct.unpack("<H", pkt[42:44])[0]
            post_ms = struct.unpack("<H", pkt[44:46])[0]
            total_samples = struct.unpack("<I", pkt[48:52])[0]
            clip_count = struct.unpack("<I", pkt[52:56])[0]
            max_abs = struct.unpack("<H", pkt[56:58])[0]

            current = new_capture(lbl, fw_rep, fw_reps, fs, pre_ms, lead_ms, post_ms, total_samples, clip_count, max_abs)
            continue

        # ---------- DA ----------
        # "DA"(2) + offset(u32) + nsamp(u16) + pcm + crc(u32)
        if len(pkt) >= 2 and pkt[:2] == b"DA":
            if current is None:
                continue
            if len(pkt) < 2 + 4 + 2 + 4:
                continue

            frame_wo_crc = pkt[:-4]
            rx_crc = struct.unpack("<I", pkt[-4:])[0]
            if crc32(frame_wo_crc) != rx_crc:
                print("[WARN] DA CRC mismatch")
                continue

            offset = struct.unpack("<I", pkt[2:6])[0]
            nsamp = struct.unpack("<H", pkt[6:8])[0]
            pcm = pkt[8:-4]

            if len(pcm) != nsamp * 2:
                continue
            if offset + nsamp > current["total_samples"]:
                continue

            base = offset * 2
            current["samples"][base:base + nsamp * 2] = pcm

            filled = current["filled"]
            new_count = 0
            for i in range(offset, offset + nsamp):
                if filled[i] == 0:
                    filled[i] = 1
                    new_count += 1
            current["received_samples"] += new_count

            if current["received_samples"] >= current["total_samples"]:
                lbl = current["label"]
                lbl_dir = os.path.join(OUT_DIR, lbl)
                os.makedirs(lbl_dir, exist_ok=True)

                repn = next_rep_index(lbl_dir)
                tag = timestamp_tag()
                folder = f"rep{repn:03d}_{tag}"
                path = os.path.join(lbl_dir, folder)
                os.makedirs(path, exist_ok=True)

                with open(os.path.join(path, "audio.raw"), "wb") as f:
                    f.write(current["samples"])

                meta = {
                    "proto_ver": proto_ver,
                    "label": lbl,
                    "fw_rep": current["fw_rep"],
                    "fw_reps_per_label": current["fw_reps_per_label"],
                    "fs": current["fs"],
                    "format": "int16",
                    "channels": 1,
                    "pre_ms": current["pre_ms"],
                    "lead_ms": current["lead_ms"],
                    "post_ms": current["post_ms"],
                    "total_samples": current["total_samples"],
                    "clip_count": current["clip_count"],
                    "max_abs": current["max_abs"],
                    "saved_rep_index": repn,
                    "saved_timestamp": tag,
                }
                with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)

                print(f"[SAVED] {lbl}/{folder}")
                current = None

            continue

        # Unknown packet: ignore
        continue

    else:
        buf.append(b[0])
