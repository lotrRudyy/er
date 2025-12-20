#!/usr/bin/env python3
"""
matcher.py — deterministic set-of-exemplars piano key matcher (ALIGNED-ONLY)

This version enforces a strict invariant:
  - Features are computed ONLY from qc_captures.py outputs:
      aligned_i16.raw + aligned_meta.json
  - If aligned files are missing, we raise an error.
  - We do NOT use raw_audio_i16.raw at all.

Supported features:
  - post: log-magnitude median spectrum from a post window starting at onset
  - delta: log(post+eps) - log(pre+eps) around onset (both from aligned audio)

Aligned conventions:
  aligned_i16.raw is a slice of raw around onset.
  onset position INSIDE aligned is stored as:
    aligned.onset_in_aligned_sample   (preferred)
  else we infer from:
    aligned.pre_align_ms (onset occurs abs(pre_align_ms) into aligned)

Requires: numpy only.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ------------------------- IO helpers -------------------------

def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))

def read_i16_raw(p: Path) -> np.ndarray:
    return np.fromfile(p, dtype="<i2").astype(np.int16, copy=False)

def to_float(x_i16: np.ndarray) -> np.ndarray:
    return (x_i16.astype(np.float32) / 32768.0)

def ensure_lower(s: str) -> str:
    return (s or "").strip().lower()


# ------------------------- Feature extraction -------------------------

@dataclass
class FeatConfig:
    fs: int = 48000

    feature: str = "post"   # "post" or "delta"

    # windows (ms)
    seg_ms: int = 200       # for post
    pre_ms: int = 200       # for delta
    post_ms: int = 200      # for delta

    # stft params
    fft_n: int = 4096
    hop: int = 512
    fmin_hz: float = 80.0
    fmax_hz: float = 8000.0
    smooth_bins: int = 3    # 0 disables
    eps: float = 1e-7


def hann(n: int) -> np.ndarray:
    # deterministic Hann
    if n <= 1:
        return np.ones((n,), dtype=np.float32)
    t = np.arange(n, dtype=np.float32)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * t / (n - 1))


def stft_mag(x: np.ndarray, cfg: FeatConfig) -> np.ndarray:
    """
    Magnitude spectrogram [frames, bins] for rFFT bins.
    """
    n = x.size
    win = hann(cfg.fft_n)
    step = cfg.hop

    if n < cfg.fft_n:
        x = np.pad(x, (0, cfg.fft_n - n), mode="constant")
        n = x.size

    nframes = 1 + (n - cfg.fft_n) // step
    mags: List[np.ndarray] = []
    for i in range(nframes):
        s = i * step
        frame = x[s:s + cfg.fft_n]
        if frame.size < cfg.fft_n:
            frame = np.pad(frame, (0, cfg.fft_n - frame.size), mode="constant")
        X = np.fft.rfft(frame * win)
        mags.append(np.abs(X).astype(np.float32))
    if not mags:
        return np.zeros((0, cfg.fft_n // 2 + 1), dtype=np.float32)
    return np.stack(mags, axis=0)


def smooth1d(v: np.ndarray, k: int) -> np.ndarray:
    """
    Simple moving-average smoothing (reflect padding).
    k=0 disables. Uses a window of size 2k+1.
    """
    if k <= 0:
        return v.astype(np.float32, copy=False)
    w = 2 * k + 1
    vp = np.pad(v, (k, k), mode="reflect")
    c = np.cumsum(vp, dtype=np.float64)
    out = (c[w:] - c[:-w]) / float(w)
    return out.astype(np.float32, copy=False)


def _logmag_median(seg: np.ndarray, fs: int, cfg: FeatConfig) -> np.ndarray:
    mag = stft_mag(seg, cfg)
    if mag.size == 0:
        return np.zeros((1,), dtype=np.float32)

    logm = np.log(cfg.eps + mag)
    v = np.median(logm, axis=0).astype(np.float32)

    freqs = np.fft.rfftfreq(cfg.fft_n, d=1.0 / float(fs))
    m = (freqs >= cfg.fmin_hz) & (freqs <= cfg.fmax_hz)
    v = v[m]
    if v.size == 0:
        return np.zeros((1,), dtype=np.float32)

    if cfg.smooth_bins > 0:
        v = smooth1d(v, cfg.smooth_bins)
    return v


def _norm(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32, copy=False)
    v = v - float(np.mean(v))
    nrm = float(np.linalg.norm(v) + 1e-12)
    return (v / nrm).astype(np.float32, copy=False)


def onset_in_aligned_from_meta(aligned_meta: dict, fs: int) -> int:
    """
    Preferred: aligned.onset_in_aligned_sample
    Fallback:  abs(aligned.pre_align_ms) converted to samples.
    """
    a = aligned_meta.get("aligned", {}) if isinstance(aligned_meta, dict) else {}
    if "onset_in_aligned_sample" in a:
        try:
            return int(a["onset_in_aligned_sample"])
        except Exception:
            pass

    pre_align_ms = a.get("pre_align_ms", 0)
    try:
        pre_align_ms_i = int(pre_align_ms)
    except Exception:
        pre_align_ms_i = 0
    return int(round(fs * (abs(pre_align_ms_i) / 1000.0)))


def extract_feature_from_aligned_only(
    aligned_i16: np.ndarray,
    aligned_meta: dict,
    cfg: FeatConfig,
) -> np.ndarray:
    # fs from QC meta if present, else cfg.fs
    fs = int(aligned_meta.get("qc", {}).get("fs", aligned_meta.get("fs", cfg.fs)))
    cfg_local = FeatConfig(**{**cfg.__dict__, "fs": fs})

    x = to_float(aligned_i16)
    onset = onset_in_aligned_from_meta(aligned_meta, fs)

    if onset < 0 or onset >= x.size:
        raise RuntimeError(f"Invalid onset_in_aligned_sample={onset} for aligned length {x.size}")

    if cfg_local.feature == "post":
        seg_len = int(round(fs * (cfg_local.seg_ms / 1000.0)))
        a0 = onset
        a1 = onset + seg_len
        if a1 > x.size:
            raise RuntimeError(
                f"Aligned audio too short for post feature: need onset+{seg_len} samples, "
                f"have {x.size} (onset={onset})"
            )
        seg = x[a0:a1]
        v = _logmag_median(seg, fs, cfg_local)
        return _norm(v)

    if cfg_local.feature == "delta":
        pre_len = int(round(fs * (cfg_local.pre_ms / 1000.0)))
        post_len = int(round(fs * (cfg_local.post_ms / 1000.0)))

        p0 = onset - pre_len
        p1 = onset
        q0 = onset
        q1 = onset + post_len

        if p0 < 0:
            raise RuntimeError(
                f"Aligned audio missing required pre context: need {pre_len} samples before onset, "
                f"have {onset} (onset={onset})"
            )
        if q1 > x.size:
            raise RuntimeError(
                f"Aligned audio missing required post context: need {post_len} samples after onset, "
                f"have {x.size - onset} (onset={onset}, len={x.size})"
            )

        pre_seg = x[p0:p1]
        post_seg = x[q0:q1]
        v_pre = _logmag_median(pre_seg, fs, cfg_local)
        v_post = _logmag_median(post_seg, fs, cfg_local)
        n = min(v_pre.size, v_post.size)
        d = (v_post[:n] - v_pre[:n]).astype(np.float32)
        return _norm(d)

    raise ValueError(f"Unknown feature={cfg_local.feature!r}")


# ------------------------- Templates + matching -------------------------

@dataclass
class PredictResult:
    pred_label: str
    s1: float
    s2: float
    margin: float
    accepted: bool
    scores: Dict[str, float]

class ExemplarMatcher:
    def __init__(self, templates: Dict[str, np.ndarray]):
        self.templates = {ensure_lower(k): v.astype(np.float32, copy=False) for k, v in templates.items()}
        dims = {v.shape[1] for v in self.templates.values() if v.ndim == 2 and v.shape[0] > 0}
        if len(dims) > 1:
            raise ValueError(f"Template feature dimension mismatch: {dims}")

    def score(self, q: np.ndarray) -> Dict[str, float]:
        q = q.astype(np.float32, copy=False)
        scores: Dict[str, float] = {}
        for lbl, ex in self.templates.items():
            sims = ex @ q
            scores[lbl] = float(np.max(sims)) if sims.size else float("-inf")
        return scores

    def predict(self, q: np.ndarray, t_abs: Optional[float] = None, t_margin: Optional[float] = None) -> PredictResult:
        scores = self.score(q)
        items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if not items:
            return PredictResult(pred_label="", s1=float("-inf"), s2=float("-inf"),
                                 margin=float("-inf"), accepted=False, scores=scores)
        pred, s1 = items[0]
        s2 = items[1][1] if len(items) > 1 else float("-inf")
        margin = float(s1 - s2)
        accepted = True
        if t_abs is not None and s1 < t_abs:
            accepted = False
        if t_margin is not None and margin < t_margin:
            accepted = False
        return PredictResult(pred_label=pred, s1=float(s1), s2=float(s2),
                             margin=margin, accepted=accepted, scores=scores)


# ------------------------- folder feature loader (ALIGNED ONLY) -------------------------

def find_rep_dirs(root: Path) -> List[Path]:
    rep_dirs: List[Path] = []
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            rep_dirs.append(rep_dir)
    return rep_dirs


def load_feature_for_rep(rep_dir: Path, cfg: FeatConfig) -> Tuple[np.ndarray, str]:
    meta_path = rep_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing meta.json in {rep_dir}")

    meta = load_json(meta_path)
    label = ensure_lower(meta.get("label", rep_dir.parent.name))

    aligned_path = rep_dir / "aligned_i16.raw"
    aligned_meta_path = rep_dir / "aligned_meta.json"

    if not aligned_path.exists() or not aligned_meta_path.exists():
        raise FileNotFoundError(
            f"Missing aligned outputs in {rep_dir}. "
            f"Need aligned_i16.raw and aligned_meta.json"
        )

    a = read_i16_raw(aligned_path)
    am = load_json(aligned_meta_path)
    f = extract_feature_from_aligned_only(a, am, cfg)
    return f, label


# ------------------------- CLI (optional) -------------------------

def cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--out_npz", default="templates.npz")

    ap.add_argument("--feature", default="post", choices=["post", "delta"])
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--pre_ms", type=int, default=200)
    ap.add_argument("--post_ms", type=int, default=200)
    ap.add_argument("--eps", type=float, default=1e-7)

    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--hop", type=int, default=512)
    ap.add_argument("--fmin", type=float, default=80.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)
    args = ap.parse_args()

    cfg = FeatConfig(
        feature=args.feature,
        seg_ms=args.seg_ms,
        pre_ms=args.pre_ms,
        post_ms=args.post_ms,
        eps=args.eps,
        fft_n=args.fft_n,
        hop=args.hop,
        fmin_hz=args.fmin,
        fmax_hz=args.fmax,
        smooth_bins=args.smooth_bins,
    )

    root = Path(args.captures_root)
    templates: Dict[str, List[np.ndarray]] = {}

    for rep_dir in find_rep_dirs(root):
        f, lbl = load_feature_for_rep(rep_dir, cfg)  # raises if aligned missing
        templates.setdefault(lbl, []).append(f)

    tmpl_arr: Dict[str, np.ndarray] = {
        lbl: np.stack(v, axis=0).astype(np.float32) for lbl, v in templates.items() if len(v) > 0
    }
    np.savez_compressed(Path(args.out_npz), **{f"tmpl__{lbl}": arr for lbl, arr in tmpl_arr.items()})
    print(f"Wrote {args.out_npz} with labels={len(tmpl_arr)} (feature={cfg.feature})")


if __name__ == "__main__":
    cli()
