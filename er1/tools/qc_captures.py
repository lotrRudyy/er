import os
import re
import json
import math
import shutil
import argparse
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

# ------------------------------
# Label classification
# ------------------------------
KEY_RE = re.compile(r"^[a-g](#?)[0-7]$")          # a0..a7, c#4 etc (lowercase)
TALKKEY_RE = re.compile(r"^talkkey_.+$")         # talkkey_c4, talkkey_a0, etc

REP_DIR_RE = re.compile(r"^rep\d+_\d{8}-\d{6}$")  # rep001_YYYYMMDD-HHMMSS


def norm_label(s: str) -> str:
    return (s or "").strip().lower()


def is_key_label(lbl: str) -> bool:
    return KEY_RE.match(lbl) is not None


def is_talkkey_label(lbl: str) -> bool:
    return TALKKEY_RE.match(lbl) is not None


def should_qc_label(lbl: str, include_talkkey: bool) -> bool:
    if is_key_label(lbl):
        return True
    if include_talkkey and is_talkkey_label(lbl):
        return True
    return False


# ------------------------------
# QC thresholds
# ------------------------------
@dataclass
class QcCfg:
    # for normal key labels
    min_peak: float = 0.03
    min_snr_db: float = 20.0
    max_rms_pre: float = 0.005

    # onset acceptance vs enter
    delete_onset_before_lead: bool = True
    delete_onset_after_post: bool = True

    # for talkkey_* labels (relaxed noise constraints)
    talkkey_min_peak: float = 0.03
    talkkey_min_snr_db: float = 16.0
    talkkey_max_rms_pre: float = 0.012


# ------------------------------
# Utilities
# ------------------------------
def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


def safe_log10(x: float) -> float:
    return math.log10(max(1e-12, x))


def read_capture(rep_folder: str) -> Tuple[np.ndarray, Dict]:
    meta_path = os.path.join(rep_folder, "meta.json")
    raw_path = os.path.join(rep_folder, "audio.raw")
    meta = json.load(open(meta_path, "r", encoding="utf-8"))
    pcm = np.fromfile(raw_path, dtype=np.int16)
    x = pcm.astype(np.float32) / 32768.0
    return x, meta


# ------------------------------
# Improved onset detector:
# spectral flux (80..3500 Hz), consecutive hits, search around Enter
# ------------------------------
def spectral_flux_onset(
    x: np.ndarray,
    fs: int,
    enter_sample: int,
    search_pre_ms: int,
    search_post_ms: int,
    frame_ms: float = 10.0,
    hop_ms: float = 5.0,
    f_lo: float = 80.0,
    f_hi: float = 3500.0,
    consec: int = 2,
    k_sigma: float = 6.0,
) -> Optional[int]:
    n = x.size
    if n < 64:
        return None

    hop = max(1, int(round(fs * hop_ms / 1000.0)))
    win = max(16, int(round(fs * frame_ms / 1000.0)))
    nfft = 1
    while nfft < win:
        nfft <<= 1

    # Search bounds around ENTER
    s0 = max(0, enter_sample - int(round(fs * search_pre_ms / 1000.0)))
    s1 = min(n, enter_sample + int(round(fs * search_post_ms / 1000.0)))
    if s1 - s0 < win:
        return None

    # Baseline tail (~60ms ending at enter)
    b1 = max(0, min(n, enter_sample))
    b0 = max(0, b1 - int(round(fs * 60 / 1000.0)))

    start = min(b0, s0)
    stop = max(b1, s1)

    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    k0 = int(np.searchsorted(freqs, f_lo, side="left"))
    k1 = int(np.searchsorted(freqs, f_hi, side="right"))
    k0 = max(1, k0)
    k1 = min(freqs.size - 1, k1)

    w = np.hanning(win).astype(np.float32)

    mags_prev = None
    flux_vals = []
    frame_starts = []

    for st in range(start, stop - win + 1, hop):
        seg = x[st:st + win] * w
        spec = np.fft.rfft(seg, n=nfft)
        mags = np.abs(spec).astype(np.float32)[k0:k1]

        if mags_prev is None:
            flux = 0.0
        else:
            diff = mags - mags_prev
            diff[diff < 0] = 0
            flux = float(np.sum(diff))
        mags_prev = mags

        flux_vals.append(flux)
        frame_starts.append(st)

    flux_vals = np.asarray(flux_vals, dtype=np.float32)
    frame_starts = np.asarray(frame_starts, dtype=np.int32)

    base_mask = (frame_starts >= b0) & (frame_starts < b1)
    base = flux_vals[base_mask]
    if base.size < 8:
        mu = float(np.mean(flux_vals))
        sd = float(np.std(flux_vals))
    else:
        mu = float(np.mean(base))
        sd = float(np.std(base))

    thr = mu + k_sigma * sd if sd > 1e-9 else (mu * 10.0 + 1e-9)

    search_mask = (frame_starts >= s0) & (frame_starts < s1)
    idxs = np.where(search_mask)[0]
    if idxs.size == 0:
        return None

    hit = 0
    for i in idxs:
        if flux_vals[i] > thr:
            hit += 1
            if hit >= consec:
                onset_i = i - (consec - 1)
                return int(frame_starts[onset_i])
        else:
            hit = 0

    return None


def save_aligned_npz(rep_folder: str, x: np.ndarray, fs: int, onset_sample: Optional[int]) -> None:
    # Save deterministic aligned slice: 20ms pre + 120ms post
    if onset_sample is None:
        return
    pre = int(round(fs * 0.020))
    post = int(round(fs * 0.120))
    a0 = max(0, onset_sample - pre)
    a1 = min(x.size, onset_sample + post)
    aligned = x[a0:a1].astype(np.float32)
    np.savez_compressed(
        os.path.join(rep_folder, "aligned.npz"),
        aligned=aligned,
        fs=fs,
        onset_sample=int(onset_sample),
        onset_ms=float(1000.0 * onset_sample / fs),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_dir", default="captures")
    ap.add_argument("--out_csv", default="qc_report.csv")
    ap.add_argument("--redo_txt", default="redo.txt")
    ap.add_argument("--delete_bad", action="store_true")
    ap.add_argument("--write_aligned", action="store_true")

    # label inclusion
    ap.add_argument("--include_talkkey", action="store_true", help="QC talkkey_* (default: on)")
    ap.add_argument("--no_talkkey", action="store_true", help="Disable QC for talkkey_* labels")

    # key thresholds
    ap.add_argument("--min_peak", type=float, default=0.03)
    ap.add_argument("--min_snr_db", type=float, default=20.0)
    ap.add_argument("--max_rms_pre", type=float, default=0.005)

    # talkkey thresholds (relaxed by default)
    ap.add_argument("--talkkey_min_peak", type=float, default=0.03)
    ap.add_argument("--talkkey_min_snr_db", type=float, default=16.0)
    ap.add_argument("--talkkey_max_rms_pre", type=float, default=0.012)

    args = ap.parse_args()

    include_talkkey = True
    if args.no_talkkey:
        include_talkkey = False
    if args.include_talkkey:
        include_talkkey = True

    qc = QcCfg(
        min_peak=args.min_peak,
        min_snr_db=args.min_snr_db,
        max_rms_pre=args.max_rms_pre,
        talkkey_min_peak=args.talkkey_min_peak,
        talkkey_min_snr_db=args.talkkey_min_snr_db,
        talkkey_max_rms_pre=args.talkkey_max_rms_pre,
    )

    rows: List[Dict] = []
    redo_cmds: List[str] = []
    deleted: List[str] = []
    skipped: List[str] = []

    if not os.path.isdir(args.captures_dir):
        print(f"No captures_dir: {args.captures_dir}")
        return

    # Layout: captures/<label>/<repdir>/audio.raw+meta.json
    for label_dir in sorted(os.listdir(args.captures_dir)):
        full_label_dir = os.path.join(args.captures_dir, label_dir)
        if not os.path.isdir(full_label_dir):
            continue

        for repdir in sorted(os.listdir(full_label_dir)):
            full_repdir = os.path.join(full_label_dir, repdir)
            if not os.path.isdir(full_repdir):
                continue

            files = set(os.listdir(full_repdir))
            if "audio.raw" not in files or "meta.json" not in files:
                continue

            x, meta = read_capture(full_repdir)

            lbl = norm_label(meta.get("label", label_dir))

            # Only QC keys (and optionally talkkey_*). Skip silence/talk/etc.
            if not should_qc_label(lbl, include_talkkey=include_talkkey):
                skipped.append(f"{label_dir}/{repdir}")
                continue

            fs = int(meta.get("fs", 48000))
            pre_ms = int(meta.get("pre_ms", 60))
            lead_ms = int(meta.get("lead_ms", 140))
            post_ms = int(meta.get("post_ms", 250))

            enter_ms = pre_ms + lead_ms
            enter_sample = int(round(fs * enter_ms / 1000.0))

            pcm_i16 = (x * 32768.0).astype(np.int16, copy=False)
            peak = float(np.max(np.abs(x))) if x.size else 0.0
            clip = int(np.sum((pcm_i16 == 32767) | (pcm_i16 == -32768)))

            pre_n = int(round(fs * pre_ms / 1000.0))
            prelead_n = int(round(fs * (pre_ms + lead_ms) / 1000.0))
            x_pre = x[:pre_n]
            x_post = x[prelead_n:] if prelead_n < x.size else np.array([], dtype=np.float32)

            rms_pre = rms(x_pre)
            rms_post = rms(x_post)
            snr_proxy_db = 20.0 * safe_log10((rms_post + 1e-9) / (rms_pre + 1e-9))

            onset_sample = spectral_flux_onset(
                x=x,
                fs=fs,
                enter_sample=enter_sample,
                search_pre_ms=lead_ms + 80,
                search_post_ms=150,
                frame_ms=10.0,
                hop_ms=5.0,
                f_lo=80.0,
                f_hi=3500.0,
                consec=2,
                k_sigma=6.0,
            )
            onset_ms = (1000.0 * onset_sample / fs) if onset_sample is not None else None
            onset_minus_enter = (onset_ms - enter_ms) if onset_ms is not None else None

            # Choose thresholds by label type
            if is_talkkey_label(lbl):
                min_peak = qc.talkkey_min_peak
                min_snr_db = qc.talkkey_min_snr_db
                max_rms_pre = qc.talkkey_max_rms_pre
            else:
                min_peak = qc.min_peak
                min_snr_db = qc.min_snr_db
                max_rms_pre = qc.max_rms_pre

            flags: List[str] = []
            if clip > 0:
                flags.append("clipped")
            if peak < min_peak:
                flags.append("too_quiet")
            if rms_pre > max_rms_pre:
                flags.append("noisy_pre")
            if snr_proxy_db < min_snr_db:
                flags.append("low_snr")
            if onset_sample is None:
                flags.append("no_onset_found")
            else:
                if qc.delete_onset_before_lead and onset_minus_enter < -lead_ms:
                    flags.append("onset_before_lead")
                if qc.delete_onset_after_post and onset_minus_enter > post_ms:
                    flags.append("onset_after_post")

            row = {
                "label": lbl,
                "folder": f"{label_dir}/{repdir}",
                "fs": fs,
                "pre_ms": pre_ms,
                "lead_ms": lead_ms,
                "post_ms": post_ms,
                "peak": peak,
                "clip": clip,
                "rms_pre": rms_pre,
                "rms_post": rms_post,
                "snr_proxy_db": snr_proxy_db,
                "onset_sample": onset_sample,
                "onset_ms_est": onset_ms,
                "onset_minus_enter_ms": onset_minus_enter,
                "flags": ",".join(flags),
            }

            is_bad = (len(flags) > 0)

            if is_bad:
                if args.delete_bad:
                    try:
                        shutil.rmtree(full_repdir)  # delete ONLY this rep folder
                        deleted.append(f"{label_dir}/{repdir}")
                    except Exception as e:
                        row["delete_error"] = str(e)

                # redo: one command per deleted rep (lowercase)
                redo_cmds.append(f"start {lbl} 1")
            else:
                if args.write_aligned:
                    try:
                        save_aligned_npz(full_repdir, x, fs, onset_sample)
                    except Exception:
                        pass

            rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["label", "folder"])
    df.to_csv(args.out_csv, index=False)

    with open(args.redo_txt, "w", encoding="utf-8") as f:
        for cmd in redo_cmds:
            f.write(cmd + "\n")

    if not df.empty:
        cols = ["label", "peak", "clip", "snr_proxy_db", "onset_minus_enter_ms", "flags", "folder"]
        print(df[cols].to_string(index=False))

    print(f"\nWrote {args.out_csv} ({len(df)} qc-checked captures)")
    if args.delete_bad:
        print(f"Deleted {len(deleted)} bad rep folders")
    print(f"Wrote {args.redo_txt} ({len(redo_cmds)} commands)")
    print(f"Skipped {len(skipped)} non-key captures (silence/talk/etc.)")

if __name__ == "__main__":
    main()
