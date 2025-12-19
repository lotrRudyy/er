#!/usr/bin/env python3
"""
qc_captures.py

QC + aligned exports + redo generation for ESP32 piano captures.

Inputs:
  captures/<label>/repNNN_YYYYMMDD-HHMMSS/
    raw_audio_i16.raw   (int16 LE mono)
    meta.json           (fs, pre_ms, lead_ms, post_ms, enter_sample, etc.)

Outputs:
  - optionally writes aligned export per rep (NON-silence only):
      aligned_i16.raw
      aligned_meta.json
  - optionally moves bad reps to:
      bad_captures/<label>/repNNN_YYYYMMDD-HHMMSS/
  - writes redo.txt with:
      start <label> 1
    repeated per bad rep

Hard rules implemented:
  - Never delete anything
  - Only move the rep folder (never a whole label folder)
  - Only classification is OK vs BAD

Label policy:
  silence:
    - 'too_quiet' does NOT apply
    - fail if not silent enough (RMS too high)
    - onset is NOT computed and NOT required
    - aligned exports are NOT written for silence

  template keys: labels like c4, f#3, a0, a#0
    - require quiet pre (noisy_pre disallowed)
    - require onset found
    - require SNR proxy above threshold
    - bad if onset occurs before Enter (onset_minus_enter_ms < -lead_ms). With lead usually 0, this is onset < enter.

  stress: talk, talkkey_*
    - allow noisy pre
    - require onset found (as requested)
    - looser SNR thresholds

Onset detection:
  - Uses short-time RMS over hop samples (default hop=256), window=hop
  - Pre statistics computed from frames strictly before enter_sample
  - Threshold: thr = pre_mean + K * pre_std, with fallback multiplier when std tiny
  - Onset is first frame after (enter_sample - search_back_ms) crossing thr for NEED_CONSEC frames
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


KEY_RE = re.compile(r"^[a-g](#)?[0-7]$")  # lowercase only
TALKKEY_RE = re.compile(r"^talkkey_.+$")


def is_key_label(lbl: str) -> bool:
    return bool(KEY_RE.match(lbl))


def is_silence(lbl: str) -> bool:
    return lbl == "silence"


def is_stress(lbl: str) -> bool:
    return lbl == "talk" or bool(TALKKEY_RE.match(lbl))


def ensure_lower(s: str) -> str:
    return (s or "").strip().lower()


@dataclass
class QCResult:
    ok: bool
    flags: List[str]
    onset_sample: Optional[int]
    onset_minus_enter_ms: Optional[float]
    rms_pre: float
    rms_post: float
    peak: float
    snr_db: Optional[float]


def read_rep(rep_dir: Path) -> Tuple[np.ndarray, Dict]:
    meta_path = rep_dir / "meta.json"
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not meta_path.exists() or not raw_path.exists():
        raise FileNotFoundError("missing meta.json or raw_audio_i16.raw")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    fs = int(meta["fs"])
    nsamples = int(meta.get("nsamples", meta.get("total_samples")))
    pcm = np.fromfile(raw_path, dtype="<i2")
    if pcm.size != nsamples:
        # tolerate mismatch but flag later (if you want, add a flag here)
        pass
    return pcm.astype(np.int16, copy=False), meta


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    xf = x.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(xf * xf) + 1e-12))


def peak_abs(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.max(np.abs(x.astype(np.float32) / 32768.0)))


def frame_rms_series(x: np.ndarray, hop: int) -> np.ndarray:
    # window = hop, stride = hop (deterministic)
    n = x.size
    nframes = n // hop
    if nframes <= 0:
        return np.zeros((0,), dtype=np.float32)
    x = x[: nframes * hop].astype(np.float32) / 32768.0
    x = x.reshape(nframes, hop)
    return np.sqrt(np.mean(x * x, axis=1) + 1e-12)


def detect_onset(
    x: np.ndarray,
    fs: int,
    enter_sample: int,
    hop: int,
    search_back_ms: int = 50,
    k_rms: float = 3.0,
    need_consec: int = 2,
) -> Optional[int]:
    """
    Returns onset sample index (in samples), or None if not found.
    Onset is detected in RMS frame domain.
    """
    if x.size < hop * 4:
        return None

    fr = frame_rms_series(x, hop)
    if fr.size == 0:
        return None

    enter_frame = max(0, min(fr.size - 1, enter_sample // hop))
    # Use all frames strictly before enter_frame for baseline
    pre_end = max(1, enter_frame)
    pre = fr[:pre_end]

    pre_mean = float(np.mean(pre))
    pre_std = float(np.std(pre))

    thr = pre_mean + k_rms * pre_std
    if pre_std < 1e-6:
        thr = max(thr, pre_mean * 3.0)

    # allow search to start slightly before enter to catch early attack
    back_frames = int(round((fs * (search_back_ms / 1000.0)) / hop))
    start_frame = max(0, enter_frame - back_frames)

    consec = 0
    for fi in range(start_frame, fr.size):
        if fr[fi] >= thr:
            consec += 1
            if consec >= need_consec:
                onset_frame = fi - need_consec + 1
                return int(onset_frame * hop)
        else:
            consec = 0

    return None


def qc_one(
    label: str,
    x: np.ndarray,
    meta: Dict,
    hop: int,
    # thresholds (float in [-1,1] domain)
    peak_min: float = 0.02,
    # silence: must be below this RMS
    silence_rms_max: float = 0.0020,
    # template: noisy pre threshold
    template_rms_pre_max: float = 0.0030,
    # snr thresholds (dB)
    template_snr_min_db: float = 14.0,
    stress_snr_min_db: float = 8.0,
) -> QCResult:
    fs = int(meta["fs"])
    enter_sample = int(
        meta.get(
            "enter_sample",
            int(round(fs * (int(meta["pre_ms"]) + int(meta["lead_ms"])) / 1000.0)),
        )
    )
    lead_ms = int(meta["lead_ms"])
    post_ms = int(meta["post_ms"])

    # windows
    pre_end = max(0, min(x.size, enter_sample))
    post_start = pre_end
    post_end = min(x.size, post_start + int(round(fs * (post_ms / 1000.0))))

    x_pre = x[:pre_end]
    x_post = x[post_start:post_end] if post_end > post_start else x[post_start:]

    r_pre = rms(x_pre)
    r_post = rms(x_post)
    pk = peak_abs(x)

    flags: List[str] = []

    # label type
    lbl = ensure_lower(label)

    # ---------------- Silence policy ----------------
    # IMPORTANT: silence has no onset. Do NOT attempt onset detection and do NOT align it.
    if is_silence(lbl):
        onset_samp = None
        onset_minus_enter_ms = None

        snr_db: Optional[float] = None
        if r_pre > 1e-9:
            snr_db = 20.0 * math.log10((r_post + 1e-9) / (r_pre + 1e-9))

        # don't apply too_quiet; instead enforce "is actually silent"
        if r_post > silence_rms_max or r_pre > silence_rms_max:
            flags.append("not_silent")

        ok = len(flags) == 0
        return QCResult(
            ok=ok,
            flags=flags,
            onset_sample=onset_samp,
            onset_minus_enter_ms=onset_minus_enter_ms,
            rms_pre=r_pre,
            rms_post=r_post,
            peak=pk,
            snr_db=snr_db,
        )

    # ---------------- Onset detection (non-silence only) ----------------
    onset_samp = detect_onset(x, fs=fs, enter_sample=enter_sample, hop=hop)
    onset_minus_enter_ms: Optional[float] = None
    if onset_samp is not None:
        onset_minus_enter_ms = 1000.0 * (onset_samp - enter_sample) / float(fs)

    snr_db: Optional[float] = None
    if r_pre > 1e-9:
        snr_db = 20.0 * math.log10((r_post + 1e-9) / (r_pre + 1e-9))

    # ---------------- Common: too_quiet (non-silence only) ----------------
    if pk < peak_min:
        flags.append("too_quiet")

    # ---------------- Template keys (clean) ----------------
    if is_key_label(lbl):
        if r_pre > template_rms_pre_max:
            flags.append("noisy_pre")

        if onset_samp is None:
            flags.append("no_onset")
        else:
            # onset_before_lead: onset_minus_enter_ms < -lead_ms
            if onset_minus_enter_ms is not None and onset_minus_enter_ms < -float(lead_ms):
                flags.append("onset_before_lead")

        if snr_db is None or snr_db < template_snr_min_db:
            flags.append("low_snr")

        ok = len(flags) == 0
        return QCResult(
            ok=ok,
            flags=flags,
            onset_sample=onset_samp,
            onset_minus_enter_ms=onset_minus_enter_ms,
            rms_pre=r_pre,
            rms_post=r_post,
            peak=pk,
            snr_db=snr_db,
        )

    # ---------------- Stress labels ----------------
    if is_stress(lbl):
        if onset_samp is None:
            flags.append("no_onset")
        else:
            if onset_minus_enter_ms is not None and onset_minus_enter_ms < -float(lead_ms):
                flags.append("onset_before_lead")

        if snr_db is None or snr_db < stress_snr_min_db:
            flags.append("low_snr")

        ok = len(flags) == 0
        return QCResult(
            ok=ok,
            flags=flags,
            onset_sample=onset_samp,
            onset_minus_enter_ms=onset_minus_enter_ms,
            rms_pre=r_pre,
            rms_post=r_post,
            peak=pk,
            snr_db=snr_db,
        )

    # ---------------- Default policy for unknown labels ----------------
    # Be conservative: require onset + non-trivial amplitude
    if onset_samp is None:
        flags.append("no_onset")
    ok = len(flags) == 0
    return QCResult(
        ok=ok,
        flags=flags,
        onset_sample=onset_samp,
        onset_minus_enter_ms=onset_minus_enter_ms,
        rms_pre=r_pre,
        rms_post=r_post,
        peak=pk,
        snr_db=snr_db,
    )


def safe_move_rep(rep_dir: Path, bad_root: Path) -> Path:
    """
    Move rep_dir to bad_root/<label>/<repdir_name>, avoiding collisions.
    Returns destination path.
    """
    label = rep_dir.parent.name
    dst_parent = bad_root / label
    dst_parent.mkdir(parents=True, exist_ok=True)
    dst = dst_parent / rep_dir.name

    if not dst.exists():
        shutil.move(str(rep_dir), str(dst))
        return dst

    # collision: add suffix
    k = 2
    while True:
        dst2 = dst_parent / f"{rep_dir.name}_dup{k}"
        if not dst2.exists():
            shutil.move(str(rep_dir), str(dst2))
            return dst2
        k += 1


def write_aligned(rep_dir: Path, x: np.ndarray, meta: Dict, qc: QCResult,
                  pre_align_ms: int, post_align_ms: int):
    """
    Write onset-aligned export for NON-silence labels only.

    Caller must ensure label != 'silence'. If onset is missing, we still write
    aligned_meta.json (so downstream can see QC + "no onset" explicitly), but we
    DO NOT write aligned_i16.raw.
    """
    fs = int(meta["fs"])
    enter_sample = int(meta.get("enter_sample", int(round(fs * (int(meta["pre_ms"]) + int(meta["lead_ms"])) / 1000.0))))
    label = ensure_lower(meta.get("label", rep_dir.parent.name))

    if label == "silence":
        # Safety: never align silence
        return

    if qc.onset_sample is None:
        # cannot align; write meta anyway (non-silence)
        aligned_meta = {
            "label": label,
            "rep_dir": rep_dir.name,
            "fs": fs,
            "enter_sample": enter_sample,
            "onset_sample": None,
            "onset_minus_enter_ms": None,
            "qc_ok": qc.ok,
            "qc_flags": qc.flags,
            "rms_pre": qc.rms_pre,
            "rms_post": qc.rms_post,
            "peak": qc.peak,
            "snr_db": qc.snr_db,
            "pre_align_ms": pre_align_ms,
            "post_align_ms": post_align_ms,
        }
        (rep_dir / "aligned_meta.json").write_text(json.dumps(aligned_meta, indent=2), encoding="utf-8")
        return

    onset = int(qc.onset_sample)
    pre_samp = int(round(fs * (pre_align_ms / 1000.0)))
    post_samp = int(round(fs * (post_align_ms / 1000.0)))

    a0 = max(0, onset - pre_samp)
    a1 = min(x.size, onset + post_samp)

    aligned = x[a0:a1].astype(np.int16, copy=False)
    (rep_dir / "aligned_i16.raw").write_bytes(aligned.tobytes(order="C"))

    aligned_meta = {
        "label": label,
        "rep_dir": rep_dir.name,
        "fs": fs,
        "enter_sample": enter_sample,
        "onset_sample": onset,
        "onset_minus_enter_ms": qc.onset_minus_enter_ms,
        "align_window_samples": [int(a0), int(a1)],
        "aligned_nsamples": int(aligned.size),
        "qc_ok": qc.ok,
        "qc_flags": qc.flags,
        "rms_pre": qc.rms_pre,
        "rms_post": qc.rms_post,
        "peak": qc.peak,
        "snr_db": qc.snr_db,
        "pre_align_ms": pre_align_ms,
        "post_align_ms": post_align_ms,
    }
    (rep_dir / "aligned_meta.json").write_text(json.dumps(aligned_meta, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--bad_root", default="bad_captures")
    ap.add_argument("--hop", type=int, default=256, choices=[128, 256, 512])
    ap.add_argument("--move_bad", action="store_true", help="Move bad reps to bad_captures/")
    ap.add_argument("--write_aligned", action="store_true", help="Write aligned_i16.raw + aligned_meta.json (non-silence only)")
    ap.add_argument("--pre_align_ms", type=int, default=20)
    ap.add_argument("--post_align_ms", type=int, default=120)

    # thresholds
    ap.add_argument("--peak_min", type=float, default=0.02)
    ap.add_argument("--silence_rms_max", type=float, default=0.0020)
    ap.add_argument("--template_rms_pre_max", type=float, default=0.0030)
    ap.add_argument("--template_snr_min_db", type=float, default=14.0)
    ap.add_argument("--stress_snr_min_db", type=float, default=8.0)

    ap.add_argument("--redo_path", default="redo.txt")
    args = ap.parse_args()

    captures_root = Path(args.captures_root)
    bad_root = Path(args.bad_root)

    if not captures_root.exists():
        raise SystemExit(f"captures_root not found: {captures_root}")

    redo_lines: List[str] = []

    rep_dirs: List[Path] = []
    for label_dir in sorted(p for p in captures_root.iterdir() if p.is_dir()):
        for rep_dir in sorted(p for p in label_dir.iterdir() if p.is_dir() and p.name.startswith("rep")):
            rep_dirs.append(rep_dir)

    total = 0
    bad = 0

    for rep_dir in rep_dirs:
        total += 1
        label = ensure_lower(rep_dir.parent.name)

        try:
            x, meta = read_rep(rep_dir)
        except Exception as e:
            print(f"[BAD] {rep_dir} read error: {e}")
            bad += 1
            redo_lines.append(f"start {label} 1")
            if args.move_bad:
                try:
                    dst = safe_move_rep(rep_dir, bad_root)
                    rep_dir = dst
                except Exception as me:
                    print(f"  WARN move failed: {me}")
            continue

        # QC
        qc = qc_one(
            label=label,
            x=x,
            meta=meta,
            hop=args.hop,
            peak_min=args.peak_min,
            silence_rms_max=args.silence_rms_max,
            template_rms_pre_max=args.template_rms_pre_max,
            template_snr_min_db=args.template_snr_min_db,
            stress_snr_min_db=args.stress_snr_min_db,
        )

        if args.write_aligned:
            # IMPORTANT: never align silence
            if label != "silence":
                try:
                    write_aligned(rep_dir, x, meta, qc, args.pre_align_ms, args.post_align_ms)
                except Exception as ae:
                    print(f"  WARN aligned write failed: {ae}")

        if qc.ok:
            print(f"[OK ] {rep_dir} rms_pre={qc.rms_pre:.6f} rms_post={qc.rms_post:.6f} "
                  f"peak={qc.peak:.4f} snr_db={qc.snr_db if qc.snr_db is not None else None} "
                  f"onset_ms={qc.onset_minus_enter_ms}")
        else:
            bad += 1
            print(f"[BAD] {rep_dir} flags={qc.flags} "
                  f"rms_pre={qc.rms_pre:.6f} rms_post={qc.rms_post:.6f} peak={qc.peak:.4f} "
                  f"snr_db={qc.snr_db if qc.snr_db is not None else None} "
                  f"onset_ms={qc.onset_minus_enter_ms}")

            redo_lines.append(f"start {label} 1")

            if args.move_bad:
                try:
                    dst = safe_move_rep(rep_dir, bad_root)
                    rep_dir = dst
                except Exception as me:
                    print(f"  WARN move failed: {me}")

    # write redo.txt
    redo_path = Path(args.redo_path)
    redo_path.write_text("\n".join(redo_lines) + ("\n" if redo_lines else ""), encoding="utf-8")

    print(f"\nSummary: total={total} bad={bad} ok={total - bad}")
    print(f"redo.txt: {redo_path} ({len(redo_lines)} lines)")
    if args.move_bad:
        print(f"bad moved under: {bad_root.resolve()}")


if __name__ == "__main__":
    main()
