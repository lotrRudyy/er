#!/usr/bin/env python3
"""
matcher.py — deterministic set-of-exemplars piano key matcher

Reads per-rep folders produced by receiver/QC:
  captures/<label>/repNNN_YYYYMMDD-HHMMSS/
    meta.json
    raw_audio_i16.raw
    (optional) aligned_i16.raw + aligned_meta.json

Feature extractor (deterministic):
  - segment: post-onset window (default 200 ms)
  - STFT magnitude with Hann window
  - median over time of log-magnitude spectrum
  - bandpass select (default 80..8000 Hz)
  - optional mild frequency smoothing
  - L2 normalize

Matcher:
  - templates: set of exemplar feature vectors per label
  - similarity: cosine
  - label score: max(sim(query, exemplar)) over exemplars
  - prediction: argmax label score
  - confidence: (S1, S2, margin=S1-S2)
  - optional reject: accept if S1>=T_abs and margin>=T_margin

Outputs:
  - predict_one(...) returns best label + diagnostics
  - can save/load templates to npz

"""

from __future__ import annotations

import argparse
import json
import math
import os
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
    seg_ms: int = 200           # post-onset segment length used for features
    fft_n: int = 4096
    hop: int = 512
    fmin_hz: float = 80.0
    fmax_hz: float = 8000.0
    smooth_bins: int = 3        # 0 disables smoothing
    eps: float = 1e-8

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
        # zero-pad to at least one frame
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
    # simple moving average, reflect pad
    pad = k
    vp = np.pad(v, (pad, pad), mode="reflect")
    c = np.cumsum(vp, dtype=np.float64)
    out = (c[2 * pad:] - c[:-2 * pad]) / float(2 * pad)
    return out.astype(np.float32, copy=False)

def extract_feature_from_aligned(
    aligned_i16: np.ndarray,
    meta: dict,
    cfg: FeatConfig,
    onset_sample_in_aligned: int = 0,
) -> np.ndarray:
    """
    aligned signal is assumed already onset-aligned with onset at index 0 by default.
    We take [onset : onset + seg_ms].
    """
    fs = int(meta.get("fs", cfg.fs))
    cfg_local = FeatConfig(**{**cfg.__dict__, "fs": fs})

    x = to_float(aligned_i16)
    seg_len = int(round(fs * (cfg_local.seg_ms / 1000.0)))
    a0 = max(0, onset_sample_in_aligned)
    a1 = min(x.size, a0 + seg_len)
    seg = x[a0:a1]
    if seg.size < max(64, cfg_local.fft_n // 8):
        # pad to avoid empty STFT
        seg = np.pad(seg, (0, max(0, cfg_local.fft_n - seg.size)), mode="constant")

    mag = stft_mag(seg, cfg_local)  # [T, F]
    if mag.size == 0:
        return np.zeros((1,), dtype=np.float32)

    # log compression
    logm = np.log(cfg_local.eps + mag)

    # median over time
    v = np.median(logm, axis=0).astype(np.float32)

    # band selection
    freqs = np.fft.rfftfreq(cfg_local.fft_n, d=1.0 / fs)
    m = (freqs >= cfg_local.fmin_hz) & (freqs <= cfg_local.fmax_hz)
    v = v[m]
    if v.size == 0:
        return np.zeros((1,), dtype=np.float32)

    # optional smoothing
    if cfg_local.smooth_bins > 0:
        v = smooth1d(v, cfg_local.smooth_bins)

    # normalize (zero-mean optional; L2 is simplest for cosine)
    v = v - float(np.mean(v))
    nrm = float(np.linalg.norm(v) + 1e-12)
    v = (v / nrm).astype(np.float32, copy=False)
    return v

def extract_feature_from_raw_rep(
    raw_i16: np.ndarray,
    meta: dict,
    cfg: FeatConfig,
    onset_hop: int = 256,
) -> Tuple[np.ndarray, Optional[int]]:
    """
    Fallback when aligned_i16.raw is not present:
    - compute onset via RMS
    - take segment starting at onset
    """
    fs = int(meta["fs"])
    pre_ms = int(meta["pre_ms"])
    lead_ms = int(meta["lead_ms"])
    enter_sample = int(meta.get("enter_sample", int(round(fs * (pre_ms + lead_ms) / 1000.0))))

    x = to_float(raw_i16)
    onset = detect_onset_rms(x, fs=fs, enter_sample=enter_sample, hop=onset_hop)
    if onset is None:
        return np.zeros((1,), dtype=np.float32), None

    seg_len = int(round(fs * (cfg.seg_ms / 1000.0)))
    a0 = onset
    a1 = min(x.size, a0 + seg_len)
    seg = x[a0:a1]
    if seg.size < max(64, cfg.fft_n // 8):
        seg = np.pad(seg, (0, max(0, cfg.fft_n - seg.size)), mode="constant")

    cfg_local = FeatConfig(**{**cfg.__dict__, "fs": fs})
    mag = stft_mag(seg, cfg_local)
    logm = np.log(cfg_local.eps + mag)
    v = np.median(logm, axis=0).astype(np.float32)

    freqs = np.fft.rfftfreq(cfg_local.fft_n, d=1.0 / fs)
    m = (freqs >= cfg_local.fmin_hz) & (freqs <= cfg_local.fmax_hz)
    v = v[m]
    if v.size == 0:
        return np.zeros((1,), dtype=np.float32), onset

    if cfg_local.smooth_bins > 0:
        v = smooth1d(v, cfg_local.smooth_bins)

    v = v - float(np.mean(v))
    nrm = float(np.linalg.norm(v) + 1e-12)
    v = (v / nrm).astype(np.float32, copy=False)
    return v, onset


# ------------------------- Templates + matching -------------------------

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    # assumes already normalized; still guard
    da = float(np.linalg.norm(a) + 1e-12)
    db = float(np.linalg.norm(b) + 1e-12)
    return float(np.dot(a, b) / (da * db))

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
        """
        templates[label] = array shape [N, D] of float32 normalized vectors.
        """
        self.templates = {ensure_lower(k): v.astype(np.float32, copy=False) for k, v in templates.items()}

        # sanity: ensure consistent dims
        dims = {v.shape[1] for v in self.templates.values() if v.ndim == 2 and v.shape[0] > 0}
        if len(dims) > 1:
            raise ValueError(f"Template feature dimension mismatch: {dims}")

    def score(self, q: np.ndarray) -> Dict[str, float]:
        q = q.astype(np.float32, copy=False)
        scores: Dict[str, float] = {}
        for lbl, ex in self.templates.items():
            # label score = max cosine similarity over exemplars
            # faster via dot product since vectors are normalized-ish
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

def save_templates_npz(out_path: Path, templates: Dict[str, np.ndarray], feat_cfg: FeatConfig):
    out = {"__feat_cfg__": np.array([json.dumps(feat_cfg.__dict__)], dtype=object)}
    for lbl, arr in templates.items():
        out[f"tmpl__{ensure_lower(lbl)}"] = arr.astype(np.float32, copy=False)
    np.savez_compressed(out_path, **out)

def load_templates_npz(path: Path) -> Tuple[Dict[str, np.ndarray], FeatConfig]:
    z = np.load(path, allow_pickle=True)
    cfg = FeatConfig()
    if "__feat_cfg__" in z:
        cfgd = json.loads(str(z["__feat_cfg__"][0]))
        cfg = FeatConfig(**cfgd)
    templates: Dict[str, np.ndarray] = {}
    for k in z.files:
        if k.startswith("tmpl__"):
            lbl = k[len("tmpl__"):]
            templates[lbl] = z[k].astype(np.float32, copy=False)
    return templates, cfg


# ------------------------- CLI: build templates from folders -------------------------

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

    aligned_path = rep_dir / "aligned_i16.raw"
    aligned_meta_path = rep_dir / "aligned_meta.json"
    if aligned_path.exists() and aligned_meta_path.exists():
        a = read_i16_raw(aligned_path)
        am = load_json(aligned_meta_path)
        # aligned is onset-aligned, onset at index 0
        f = extract_feature_from_aligned(a, am, cfg, onset_sample_in_aligned=0)
        return f, label

    # fallback raw
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        return None, label
    x = read_i16_raw(raw_path)
    f, onset = extract_feature_from_raw_rep(x, meta, cfg)
    if onset is None:
        return None, label
    return f, label

def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--out_npz", default="templates.npz")
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--hop", type=int, default=512)
    ap.add_argument("--fmin", type=float, default=80.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)
    args = ap.parse_args()

    cfg = FeatConfig(seg_ms=args.seg_ms, fft_n=args.fft_n, hop=args.hop, fmin_hz=args.fmin, fmax_hz=args.fmax, smooth_bins=args.smooth_bins)
    root = Path(args.captures_root)
    templates: Dict[str, List[np.ndarray]] = {}

    for rep_dir in find_rep_dirs(root):
        f, lbl = load_feature_for_rep(rep_dir, cfg)
        if f is None or lbl is None:
            continue
        templates.setdefault(lbl, []).append(f)

    tmpl_arr: Dict[str, np.ndarray] = {lbl: np.stack(v, axis=0).astype(np.float32) for lbl, v in templates.items() if len(v) > 0}
    save_templates_npz(Path(args.out_npz), tmpl_arr, cfg)
    print(f"Wrote {args.out_npz} with labels={len(tmpl_arr)}")

if __name__ == "__main__":
    cli()
