from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ------------------------------
# Label classification
# ------------------------------
KEY_RE = re.compile(r"^[a-g](#?)[0-7]$")          # a0..a7, c#4 etc (lowercase)
TALKKEY_RE = re.compile(r"^talkkey_.+$")         # talkkey_c4, talkkey_a0, etc
REP_DIR_RE = re.compile(r"^rep\d{3}_\d{8}-\d{6}$", re.IGNORECASE)


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


def infer_mode(rep_folder: Path, meta: Dict[str, Any], lbl: str) -> str:
    """
    Returns 'clean' or 'stress'.

    Priority:
      1) meta['mode'] if present
      2) folder path contains captures_stress (common in your workflow)
      3) talkkey_* label treated as stress-ish by default
      4) default clean
    """
    m = str(meta.get("mode", "")).strip().lower()
    if m in ("stress", "clean"):
        return m

    p = str(rep_folder).lower()
    if "captures_stress" in p or "stress" in p:
        # keep it simple: if the path clearly indicates stress dataset
        return "stress"

    if is_talkkey_label(lbl):
        return "stress"

    return "clean"


# ------------------------------
# QC thresholds
# ------------------------------
@dataclass
class QcCfg:
    # strict (clean templates; key labels)
    min_peak: float = 0.03
    min_snr_db: float = 20.0
    max_rms_pre: float = 0.005

    # relaxed (stress recordings; ringing expected)
    stress_min_peak: float = 0.03
    stress_min_snr_db: float = 10.0
    stress_max_rms_pre: float = 0.020
    stress_ignore_noisy_pre: bool = True  # ringing makes pre "noisy" by design

    # relaxed for talkkey_* (kept for backward compatibility)
    talkkey_min_peak: float = 0.03
    talkkey_min_snr_db: float = 16.0
    talkkey_max_rms_pre: float = 0.012

    # onset acceptance vs enter
    reject_onset_before_lead: bool = True
    reject_onset_after_post: bool = True

    # onset detector parameters
    onset_f_lo: float = 80.0
    onset_f_hi: float = 3500.0
    onset_frame_ms: float = 10.0
    onset_hop_ms: float = 5.0
    onset_consec: int = 2

    # onset thresholding (clean vs stress)
    onset_k_sigma_clean: float = 6.0   # old behavior
    onset_k_sigma_stress: float = 4.0  # easier trigger in stress

    # onset search windows around Enter
    onset_search_pre_ms: int = 120      # includes some lead + safety
    onset_search_post_ms_clean: int = 160
    onset_search_post_ms_stress: int = 260  # wider window helps low keys / messy reps

    # aligned export window (ms) relative to detected onset
    pre_align_ms: int = 0
    post_align_ms: int = 300


# ------------------------------
# Utilities
# ------------------------------

def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


def safe_log10(x: float) -> float:
    return math.log10(max(1e-12, x))


def read_capture(rep_folder: Path) -> Tuple[np.ndarray, Dict[str, Any]]:
    meta_path = rep_folder / "meta.json"
    raw_path = rep_folder / "raw_audio_i16.raw"
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing meta.json: {meta_path}")
    if not raw_path.exists():
        raise FileNotFoundError(f"Missing raw_audio_i16.raw: {raw_path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pcm = np.fromfile(str(raw_path), dtype=np.int16)
    x = pcm.astype(np.float32) / 32768.0
    return x, meta


def move_rep_to_bad(rep_folder: Path, bad_dir: Path) -> Path:
    """Move captures/<label>/<rep...>/ -> bad_captures/<label>/<rep...>/"""
    rep_folder = rep_folder.resolve()
    label = rep_folder.parent.name.lower()
    rep_name = rep_folder.name

    dest = bad_dir.resolve() / label / rep_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        suffix = datetime.now().strftime("%H%M%S")
        dest = bad_dir.resolve() / label / f"{rep_name}_dup{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(str(rep_folder), str(dest))
    return dest


def robust_center_scale(x: np.ndarray) -> Tuple[float, float]:
    """
    Robust baseline stats for flux:
      center = median
      scale  = 1.4826 * MAD
    """
    if x.size == 0:
        return 0.0, 0.0
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    scale = 1.4826 * mad
    return med, scale


# ------------------------------
# Onset detector (spectral flux)
# ------------------------------

def spectral_flux_onset(
    x: np.ndarray,
    fs: int,
    enter_sample: int,
    search_pre_ms: int,
    search_post_ms: int,
    frame_ms: float,
    hop_ms: float,
    f_lo: float,
    f_hi: float,
    consec: int,
    k_sigma: float,
) -> Optional[int]:
    """
    Spectral-flux onset around Enter.

    Uses robust median+MAD baseline for thresholding (less sensitive to ringing),
    and falls back to mean+std if MAD is ~0.
    """
    n = x.size
    if n < 128:
        return None

    hop = max(1, int(round(fs * hop_ms / 1000.0)))
    win = max(64, int(round(fs * frame_ms / 1000.0)))
    nfft = 1
    while nfft < win:
        nfft <<= 1

    s0 = max(0, enter_sample - int(round(fs * search_pre_ms / 1000.0)))
    s1 = min(n, enter_sample + int(round(fs * search_post_ms / 1000.0)))
    if s1 - s0 < win:
        return None

    # baseline window: last 60ms before enter (can include ringing; robust stats handle it)
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
    flux_vals: List[float] = []
    frame_starts: List[int] = []

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

    flux_vals_np = np.asarray(flux_vals, dtype=np.float32)
    frame_starts_np = np.asarray(frame_starts, dtype=np.int32)

    base_mask = (frame_starts_np >= b0) & (frame_starts_np < b1)
    base = flux_vals_np[base_mask]

    if base.size >= 8:
        mu, sd = robust_center_scale(base)
        if sd < 1e-9:
            mu = float(np.mean(base))
            sd = float(np.std(base))
    else:
        mu = float(np.mean(flux_vals_np))
        sd = float(np.std(flux_vals_np))

    thr = mu + k_sigma * sd if sd > 1e-9 else (mu * 10.0 + 1e-9)

    search_mask = (frame_starts_np >= s0) & (frame_starts_np < s1)
    idxs = np.where(search_mask)[0]
    if idxs.size == 0:
        return None

    hit = 0
    for i in idxs:
        if flux_vals_np[i] > thr:
            hit += 1
            if hit >= consec:
                onset_i = i - (consec - 1)
                return int(frame_starts_np[onset_i])
        else:
            hit = 0

    return None


def write_aligned_exports(rep_folder: Path, x: np.ndarray, fs: int, onset_sample: int, cfg: QcCfg, qc_result: Dict[str, Any]) -> None:
    pre = int(round(fs * (cfg.pre_align_ms / 1000.0)))
    post = int(round(fs * (cfg.post_align_ms / 1000.0)))
    a0 = max(0, onset_sample - pre)
    a1 = min(x.size, onset_sample + post)

    aligned_i16 = np.clip(x[a0:a1] * 32768.0, -32768, 32767).astype(np.int16)

    out_raw = rep_folder / "aligned_i16.raw"
    out_meta = rep_folder / "aligned_meta.json"

    out_raw.write_bytes(aligned_i16.tobytes())

    meta_out = {
        "aligned": {
            "onset_sample": int(onset_sample),
            "onset_ms": float(1000.0 * onset_sample / fs),
            "pre_align_ms": int(cfg.pre_align_ms),
            "post_align_ms": int(cfg.post_align_ms),
            "slice_start_sample": int(a0),
            "slice_end_sample": int(a1),
            "nsamples": int(a1 - a0),
        },
        "qc": qc_result,
    }
    out_meta.write_text(json.dumps(meta_out, indent=2), encoding="utf-8")


# ------------------------------
# Core QC: one rep
# ------------------------------

def qc_one_rep(
    rep_folder: Path,
    cfg: QcCfg,
    include_talkkey: bool,
    move_bad: bool,
    bad_dir: Path,
    write_aligned: bool,
) -> Dict[str, Any]:
    rep_folder = rep_folder.resolve()

    try:
        x, meta = read_capture(rep_folder)
    except Exception as e:
        return {
            "rep_folder": str(rep_folder),
            "status": "error",
            "error": str(e),
            "is_bad": True,
            "flags": ["read_error"],
        }

    lbl = norm_label(meta.get("label", rep_folder.parent.name))
    fs = int(meta.get("fs", 48000))
    pre_ms = int(meta.get("pre_ms", 0))
    lead_ms = int(meta.get("lead_ms", 0))
    post_ms = int(meta.get("post_ms", 0))

    if not should_qc_label(lbl, include_talkkey=include_talkkey):
        return {
            "rep_folder": str(rep_folder),
            "status": "skipped",
            "label": lbl,
            "reason": "label_not_qc",
            "is_bad": False,
            "flags": [],
        }

    mode = infer_mode(rep_folder, meta, lbl)

    enter_ms = pre_ms + lead_ms
    enter_sample = int(round(fs * enter_ms / 1000.0))

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    pcm_i16 = np.clip(x * 32768.0, -32768, 32767).astype(np.int16, copy=False)
    clip = int(np.sum((pcm_i16 == 32767) | (pcm_i16 == -32768)))

    pre_n = int(round(fs * pre_ms / 1000.0))
    prelead_n = int(round(fs * (pre_ms + lead_ms) / 1000.0))
    x_pre = x[:pre_n]
    x_post = x[prelead_n:] if prelead_n < x.size else np.array([], dtype=np.float32)

    rms_pre = rms(x_pre)
    rms_post = rms(x_post)
    snr_proxy_db = 20.0 * safe_log10((rms_post + 1e-9) / (rms_pre + 1e-9))

    # thresholds by mode / label type
    if is_talkkey_label(lbl):
        min_peak = cfg.talkkey_min_peak
        min_snr_db = cfg.talkkey_min_snr_db
        max_rms_pre = cfg.talkkey_max_rms_pre
        ignore_noisy_pre = False
        k_sigma = cfg.onset_k_sigma_stress
        search_post_ms = cfg.onset_search_post_ms_stress
    elif mode == "stress":
        min_peak = cfg.stress_min_peak
        min_snr_db = cfg.stress_min_snr_db
        max_rms_pre = cfg.stress_max_rms_pre
        ignore_noisy_pre = cfg.stress_ignore_noisy_pre
        k_sigma = cfg.onset_k_sigma_stress
        search_post_ms = cfg.onset_search_post_ms_stress
    else:
        min_peak = cfg.min_peak
        min_snr_db = cfg.min_snr_db
        max_rms_pre = cfg.max_rms_pre
        ignore_noisy_pre = False
        k_sigma = cfg.onset_k_sigma_clean
        search_post_ms = cfg.onset_search_post_ms_clean

    search_pre_ms = int(max(cfg.onset_search_pre_ms, lead_ms + 80))

    onset_sample = spectral_flux_onset(
        x=x,
        fs=fs,
        enter_sample=enter_sample,
        search_pre_ms=search_pre_ms,
        search_post_ms=search_post_ms,
        frame_ms=cfg.onset_frame_ms,
        hop_ms=cfg.onset_hop_ms,
        f_lo=cfg.onset_f_lo,
        f_hi=cfg.onset_f_hi,
        consec=cfg.onset_consec,
        k_sigma=k_sigma,
    )

    onset_ms = (1000.0 * onset_sample / fs) if onset_sample is not None else None
    onset_minus_enter_ms = (onset_ms - enter_ms) if onset_ms is not None else None

    flags: List[str] = []

    if clip > 0:
        flags.append("clipped")
    if peak < min_peak:
        flags.append("too_quiet")
    if (not ignore_noisy_pre) and (rms_pre > max_rms_pre):
        flags.append("noisy_pre")
    if snr_proxy_db < min_snr_db:
        flags.append("low_snr")

    if onset_sample is None:
        flags.append("no_onset_found")
    else:
        if cfg.reject_onset_before_lead and onset_minus_enter_ms is not None and onset_minus_enter_ms < -lead_ms:
            flags.append("onset_before_lead")
        if cfg.reject_onset_after_post and onset_minus_enter_ms is not None and onset_minus_enter_ms > post_ms:
            flags.append("onset_after_post")

    is_bad = (len(flags) > 0)

    result: Dict[str, Any] = {
        "rep_folder": str(rep_folder),
        "label": lbl,
        "mode": mode,
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
        "onset_minus_enter_ms": onset_minus_enter_ms,
        "flags": flags,
        "is_bad": is_bad,
        "status": "ok" if not is_bad else "bad",
    }

    if is_bad and move_bad:
        try:
            moved_to = move_rep_to_bad(rep_folder, bad_dir=bad_dir)
            result["moved_to"] = str(moved_to)
        except Exception as e:
            result["move_error"] = str(e)

    if write_aligned and (not is_bad) and (onset_sample is not None):
        try:
            write_aligned_exports(rep_folder, x=x, fs=fs, onset_sample=int(onset_sample), cfg=cfg, qc_result=result)
            result["aligned_written"] = True
        except Exception as e:
            result["aligned_written"] = False
            result["aligned_error"] = str(e)

    return result


# ------------------------------
# Batch mode
# ------------------------------

def iter_rep_folders(captures_dir: Path) -> List[Path]:
    out: List[Path] = []
    if not captures_dir.exists():
        return out
    for label_dir in sorted(p for p in captures_dir.iterdir() if p.is_dir()):
        for rep_dir in sorted(p for p in label_dir.iterdir() if p.is_dir()):
            if REP_DIR_RE.match(rep_dir.name):
                out.append(rep_dir)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_dir", default="captures")
    ap.add_argument("--bad_dir", default="bad_captures")
    ap.add_argument("--out_csv", default="qc_report.csv")
    ap.add_argument("--redo_txt", default="redo.txt")

    ap.add_argument("--rep_dir", default=None, help="QC a single rep folder (overrides batch walk)")

    ap.add_argument("--move_bad", action="store_true", help="Move bad reps to bad_captures/")
    ap.add_argument("--write_aligned", action="store_true", help="Write aligned exports for good reps")
    ap.add_argument("--pre_align_ms", type=int, default=0)
    ap.add_argument("--post_align_ms", type=int, default=300)

    ap.add_argument("--include_talkkey", action="store_true", help="QC talkkey_* (default: on)")
    ap.add_argument("--no_talkkey", action="store_true", help="Disable QC for talkkey_* labels")

    # clean thresholds
    ap.add_argument("--min_peak", type=float, default=0.03)
    ap.add_argument("--min_snr_db", type=float, default=20.0)
    ap.add_argument("--max_rms_pre", type=float, default=0.005)

    # stress thresholds
    ap.add_argument("--stress_min_peak", type=float, default=0.03)
    ap.add_argument("--stress_min_snr_db", type=float, default=10.0)
    ap.add_argument("--stress_max_rms_pre", type=float, default=0.020)
    ap.add_argument("--stress_ignore_noisy_pre", action="store_true", help="do not fail stress reps for noisy_pre (default on)")

    # talkkey thresholds
    ap.add_argument("--talkkey_min_peak", type=float, default=0.03)
    ap.add_argument("--talkkey_min_snr_db", type=float, default=16.0)
    ap.add_argument("--talkkey_max_rms_pre", type=float, default=0.012)

    args = ap.parse_args()

    include_talkkey = True
    if args.no_talkkey:
        include_talkkey = False
    if args.include_talkkey:
        include_talkkey = True

    cfg = QcCfg(
        min_peak=args.min_peak,
        min_snr_db=args.min_snr_db,
        max_rms_pre=args.max_rms_pre,
        stress_min_peak=args.stress_min_peak,
        stress_min_snr_db=args.stress_min_snr_db,
        stress_max_rms_pre=args.stress_max_rms_pre,
        stress_ignore_noisy_pre=True if args.stress_ignore_noisy_pre else True,  # default on
        talkkey_min_peak=args.talkkey_min_peak,
        talkkey_min_snr_db=args.talkkey_min_snr_db,
        talkkey_max_rms_pre=args.talkkey_max_rms_pre,
        pre_align_ms=args.pre_align_ms,
        post_align_ms=args.post_align_ms,
    )

    captures_dir = Path(args.captures_dir)
    bad_dir = Path(args.bad_dir)

    if args.rep_dir:
        rep = Path(args.rep_dir)
        res = qc_one_rep(
            rep_folder=rep,
            cfg=cfg,
            include_talkkey=include_talkkey,
            move_bad=args.move_bad,
            bad_dir=bad_dir,
            write_aligned=args.write_aligned,
        )
        print(json.dumps(res, indent=2))
        return

    rows: List[Dict[str, Any]] = []
    redo_cmds: List[str] = []
    skipped = 0
    moved = 0
    errored = 0

    for rep_folder in iter_rep_folders(captures_dir):
        res = qc_one_rep(
            rep_folder=rep_folder,
            cfg=cfg,
            include_talkkey=include_talkkey,
            move_bad=args.move_bad,
            bad_dir=bad_dir,
            write_aligned=args.write_aligned,
        )

        status = res.get("status", "")
        if status == "skipped":
            skipped += 1
            continue
        if status == "error":
            errored += 1

        folder_rel = ""
        try:
            folder_rel = str(Path(res.get("rep_folder", "")).resolve().relative_to(captures_dir.resolve()))
        except Exception:
            folder_rel = res.get("rep_folder", "")

        row = {
            "label": res.get("label", ""),
            "mode": res.get("mode", ""),
            "folder": folder_rel,
            "fs": res.get("fs", ""),
            "pre_ms": res.get("pre_ms", ""),
            "lead_ms": res.get("lead_ms", ""),
            "post_ms": res.get("post_ms", ""),
            "peak": res.get("peak", ""),
            "clip": res.get("clip", ""),
            "rms_pre": res.get("rms_pre", ""),
            "rms_post": res.get("rms_post", ""),
            "snr_proxy_db": res.get("snr_proxy_db", ""),
            "onset_sample": res.get("onset_sample", ""),
            "onset_ms_est": res.get("onset_ms_est", ""),
            "onset_minus_enter_ms": res.get("onset_minus_enter_ms", ""),
            "flags": ",".join(res.get("flags", []) or []),
            "moved_to": res.get("moved_to", ""),
        }
        rows.append(row)

        if res.get("is_bad", False):
            redo_cmds.append(f"start {res.get('label','').lower()} 1")
            if args.move_bad and res.get("moved_to"):
                moved += 1

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["label", "folder"])
    df.to_csv(args.out_csv, index=False)

    Path(args.redo_txt).write_text("\n".join(redo_cmds) + ("\n" if redo_cmds else ""), encoding="utf-8")

    print(f"Wrote {args.out_csv} ({len(df)} qc-checked reps)")
    print(f"Wrote {args.redo_txt} ({len(redo_cmds)} commands)")
    print(f"Skipped {skipped} non-qc labels")
    if args.move_bad:
        print(f"Moved {moved} bad reps -> {args.bad_dir}/")
    if errored:
        print(f"Errors: {errored} reps had read/move issues")


if __name__ == "__main__":
    main()
