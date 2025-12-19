#!/usr/bin/env python3
"""
matcher.py — deterministic set-of-exemplars piano key matcher

Adds feature="post" (existing) and feature="delta" (new):
  delta = log(post+eps) - log(pre+eps), computed from RAW windows around onset.

Important with your QC:
- qc_captures.py default pre_align_ms=0 means aligned_i16.raw has no pre-onset context,
  so delta must use raw_audio_i16.raw (but can still use aligned_meta.json for onset_sample).
"""

from __future__ import annotations

import argparse
import json
import math
import re
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


# ------------------------- Onset detection (fallback) -------------------------

def frame_rms_series(x: np.ndarray, hop: int) -> np.ndarray:
    n = x.size
    nf = n // hop
    if nf <= 0:
        return np.zeros((0,), dtype=np.float32)
    xs = x[: nf * hop].astype(np.float32)
    xs = xs.reshape(nf, hop)
    return np.sqrt(np.mean(xs * xs, axis=1) + 1e-12)

def detect_onset_rms(
    x: np.ndarray,
    fs: int,
    enter_sample: int,
    hop: int = 256,
    search_back_ms: int = 50,
    k_rms: float = 3.0,
    need_consec: int = 2,
) -> Optional[int]:
    """
    Deterministic onset detector based on frame RMS.
    Returns onset sample index or None.
    """
    if x.size < hop * 4:
        return None
    fr = frame_rms_series(x, hop)
    if fr.size == 0:
        return None

    enter_frame = max(0, min(fr.size - 1, enter_sample // hop))
    pre_end = max(1, enter_frame)
    pre = fr[:pre_end]

    pre_mean = float(np.mean(pre))
    pre_std = float(np.std(pre))
    thr = pre_mean + k_rms * pre_std
    if pre_std < 1e-6:
        thr = max(thr, pre_mean * 3.0)

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


# ------------------------- Feature extraction -------------------------

@dataclass
class FeatConfig:
    fs: int = 48000

    # feature mode
    feature: str = "post"        # "post" or "delta"

    # windows
    seg_ms: int = 200            # post-only window length
    pre_ms: int = 200            # delta pre window length
    post_ms: int = 200           # delta post window length

    # stft
    fft_n: int = 4096
    hop: int = 512
    fmin_hz: float = 80.0
    fmax_hz: float = 8000.0
    smooth_bins: int = 3        # 0 disables smoothing
    eps: float = 1e-7


def hann(n: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=np.float32) / max(1, (n - 1)))

def stft_mag(x: np.ndarray, cfg: FeatConfig) -> np.ndarray:
    """
    Returns magnitude spectrogram [frames, bins] for real FFT bins (0..fft_n/2).
    """
    n = x.size
    win = hann(cfg.fft_n)
    step = cfg.hop
    if n < cfg.fft_n:
        pad = cfg.fft_n - n
        x = np.pad(x, (0, pad), mode="constant")
        n = x.size

    nframes = 1 + (n - cfg.fft_n) // step
    mags = []
    for i in range(nframes):
        s = i * step
        frame = x[s:s + cfg.fft_n]
        if frame.size < cfg.fft_n:
            frame = np.pad(frame, (0, cfg.fft_n - frame.size), mode="constant")
        X = np.fft.rfft(frame * win)
        mags.append(np.abs(X).astype(np.float32))
    return np.stack(mags, axis=0) if mags else np.zeros((0, cfg.fft_n // 2 + 1), dtype=np.float32)

def smooth1d(v: np.ndarray, k: int) -> np.ndarray:
    if k <= 0:
        return v
    pad = k
    vp = np.pad(v, (pad, pad), mode="reflect")
    c = np.cumsum(vp, dtype=np.float64)
    out = (c[2 * pad:] - c[:-2 * pad]) / float(2 * pad)
    return out.astype(np.float32, copy=False)

def _logmag_median(seg: np.ndarray, fs: int, cfg: FeatConfig) -> np.ndarray:
    mag = stft_mag(seg, cfg)  # [T, F]
    if mag.size == 0:
        return np.zeros((1,), dtype=np.float32)
    logm = np.log(cfg.eps + mag)
    v = np.median(logm, axis=0).astype(np.float32)

    freqs = np.fft.rfftfreq(cfg.fft_n, d=1.0 / fs)
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

def extract_feature_from_raw_rep(
    raw_i16: np.ndarray,
    meta: dict,
    cfg: FeatConfig,
    onset_hop: int = 256,
) -> Tuple[np.ndarray, Optional[int]]:
    """
    Use aligned_meta.json onset_sample if present (preferred), else fallback RMS onset.
    Then compute either post-only or delta feature from RAW.
    """
    fs = int(meta["fs"])
    pre_ms_cap = int(meta.get("pre_ms", 0))
    lead_ms = int(meta.get("lead_ms", 0))
    enter_sample = int(meta.get("enter_sample", int(round(fs * ((pre_ms_cap + lead_ms) / 1000.0)))))

    x = to_float(raw_i16)

    onset = None
    # if aligned_meta.json exists, it may contain onset_sample (absolute, in raw)
    # (template_curve_eval calls load_feature_for_rep; we load that file there)
    if "aligned_onset_sample" in meta:
        try:
            onset = int(meta["aligned_onset_sample"])
        except Exception:
            onset = None

    if onset is None:
        onset = detect_onset_rms(x, fs=fs, enter_sample=enter_sample, hop=onset_hop)
    if onset is None:
        return np.zeros((1,), dtype=np.float32), None

    cfg_local = FeatConfig(**{**cfg.__dict__, "fs": fs})

    if cfg_local.feature == "delta":
        pre_len = int(round(fs * (cfg_local.pre_ms / 1000.0)))
        post_len = int(round(fs * (cfg_local.post_ms / 1000.0)))

        p0 = max(0, onset - pre_len)
        p1 = max(0, onset)
        pre_seg = x[p0:p1]
        if pre_seg.size < pre_len:
            pre_seg = np.pad(pre_seg, (pre_len - pre_seg.size, 0), mode="constant")

        q0 = onset
        q1 = min(x.size, onset + post_len)
        post_seg = x[q0:q1]
        if post_seg.size < post_len:
            post_seg = np.pad(post_seg, (0, post_len - post_seg.size), mode="constant")

        v_pre = _logmag_median(pre_seg, fs, cfg_local)
        v_post = _logmag_median(post_seg, fs, cfg_local)
        n = min(v_pre.size, v_post.size)
        d = (v_post[:n] - v_pre[:n]).astype(np.float32)
        return _norm(d), onset

    # post-only
    seg_len = int(round(fs * (cfg_local.seg_ms / 1000.0)))
    a0 = onset
    a1 = min(x.size, a0 + seg_len)
    seg = x[a0:a1]
    if seg.size < max(64, cfg_local.fft_n // 8):
        seg = np.pad(seg, (0, max(0, cfg_local.fft_n - seg.size)), mode="constant")
    v = _logmag_median(seg, fs, cfg_local)
    return _norm(v), onset


def extract_feature_from_aligned(
    aligned_i16: np.ndarray,
    aligned_meta: dict,
    cfg: FeatConfig,
    onset_sample_in_aligned: int = 0,
) -> np.ndarray:
    """
    For post-only features, aligned_i16 is fine (onset at index 0 by default).
    For delta features, aligned_i16 is only usable if it contains pre-onset context;
    your QC default pre_align_ms=0 usually does NOT, so we avoid delta-on-aligned here.
    """
    fs = int(aligned_meta.get("fs", cfg.fs))
    cfg_local = FeatConfig(**{**cfg.__dict__, "fs": fs})

    x = to_float(aligned_i16)
    if cfg_local.feature == "delta":
        # refuse silently; caller should fall back to raw
        return np.zeros((1,), dtype=np.float32)

    seg_len = int(round(fs * (cfg_local.seg_ms / 1000.0)))
    a0 = max(0, onset_sample_in_aligned)
    a1 = min(x.size, a0 + seg_len)
    seg = x[a0:a1]
    if seg.size < max(64, cfg_local.fft_n // 8):
        seg = np.pad(seg, (0, max(0, cfg_local.fft_n - seg.size)), mode="constant")

    v = _logmag_median(seg, fs, cfg_local)
    return _norm(v)


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

    def predict(
        self,
        q: np.ndarray,
        t_abs: Optional[float] = None,
        t_margin: Optional[float] = None,
    ) -> PredictResult:
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


# ------------------------- folder feature loader -------------------------

def find_rep_dirs(root: Path) -> List[Path]:
    rep_dirs: List[Path] = []
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            rep_dirs.append(rep_dir)
    return rep_dirs

def load_feature_for_rep(rep_dir: Path, cfg: FeatConfig) -> Tuple[Optional[np.ndarray], Optional[str]]:
    meta_path = rep_dir / "meta.json"
    if not meta_path.exists():
        return None, None
    meta = load_json(meta_path)
    label = ensure_lower(meta.get("label", rep_dir.parent.name))

    # If QC onset exists, inject it into meta so raw extractor can use it
    aligned_meta_path = rep_dir / "aligned_meta.json"
    if aligned_meta_path.exists():
        am = load_json(aligned_meta_path)
        onset_sample = am.get("aligned", {}).get("onset_sample", None)
        if isinstance(onset_sample, (int, float)):
            meta = dict(meta)
            meta["aligned_onset_sample"] = int(onset_sample)

    aligned_path = rep_dir / "aligned_i16.raw"
    if cfg.feature == "post" and aligned_path.exists() and aligned_meta_path.exists():
        a = read_i16_raw(aligned_path)
        am = load_json(aligned_meta_path)
        f = extract_feature_from_aligned(a, am, cfg, onset_sample_in_aligned=0)
        return f, label

    # fallback to RAW (and for delta, RAW is required)
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        return None, label
    x = read_i16_raw(raw_path)
    f, onset = extract_feature_from_raw_rep(x, meta, cfg)
    if onset is None:
        return None, label
    return f, label


# ------------------------- CLI (optional) -------------------------

def cli():
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
        f, lbl = load_feature_for_rep(rep_dir, cfg)
        if f is None or lbl is None:
            continue
        templates.setdefault(lbl, []).append(f)

    tmpl_arr: Dict[str, np.ndarray] = {
        lbl: np.stack(v, axis=0).astype(np.float32) for lbl, v in templates.items() if len(v) > 0
    }
    np.savez_compressed(Path(args.out_npz), **{f"tmpl__{lbl}": arr for lbl, arr in tmpl_arr.items()})
    print(f"Wrote {args.out_npz} with labels={len(tmpl_arr)} (feature={cfg.feature})")

if __name__ == "__main__":
    cli()
