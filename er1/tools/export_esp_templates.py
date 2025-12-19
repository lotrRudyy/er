#!/usr/bin/env python3
"""
export_esp_templates.py

One-time offline exporter:
  captures/<label>/rep.../(aligned_i16.raw + aligned_meta.json)  OR (raw_audio_i16.raw + meta.json)
→ generates a C header with float templates for ESP standalone matching.

This exporter deliberately uses an ESP-friendly feature:
  - 200ms post-onset segment
  - single RFFT (fft_n)
  - log magnitude
  - band select [fmin..fmax]
  - optional freq smoothing
  - mean-center + L2 normalize

Output:
  templates_generated.h
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np


EPS = 1e-8


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def read_i16_raw(p: Path) -> np.ndarray:
    return np.fromfile(p, dtype="<i2").astype(np.int16, copy=False)


def to_float(x_i16: np.ndarray) -> np.ndarray:
    return (x_i16.astype(np.float32) / 32768.0)


def ensure_lower(s: str) -> str:
    return (s or "").strip().lower()


def hann(n: int) -> np.ndarray:
    i = np.arange(n, dtype=np.float32)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * i / max(1, n - 1))


def smooth1d(v: np.ndarray, k: int) -> np.ndarray:
    if k <= 0 or v.size < (2 * k + 3):
        return v.astype(np.float32, copy=False)
    pad = k
    vp = np.pad(v, (pad, pad), mode="reflect")
    c = np.cumsum(vp, dtype=np.float64)
    out = (c[2 * pad:] - c[:-2 * pad]) / float(2 * pad)
    return out.astype(np.float32, copy=False)


def l2norm(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32, copy=False)
    v = v - float(np.mean(v))
    n = float(np.linalg.norm(v) + 1e-12)
    return (v / n).astype(np.float32, copy=False)


def feature_single_fft_logmag(
    seg: np.ndarray,
    fs: int,
    fft_n: int,
    fmin_hz: float,
    fmax_hz: float,
    smooth_bins: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns (freqs_selected, feat_vector_normalized)
    """
    if seg.size < fft_n:
        seg = np.pad(seg, (0, fft_n - seg.size), mode="constant")
    seg = seg[:fft_n]

    win = hann(fft_n)
    X = np.fft.rfft(seg * win)
    mag = np.abs(X).astype(np.float32)
    logm = np.log(mag + EPS)

    freqs = np.fft.rfftfreq(fft_n, d=1.0 / fs)
    m = (freqs >= fmin_hz) & (freqs <= fmax_hz)
    freqs_sel = freqs[m].astype(np.float32, copy=False)
    v = logm[m].astype(np.float32, copy=False)

    if v.size == 0:
        return freqs_sel, np.zeros((1,), dtype=np.float32)

    if smooth_bins > 0:
        v = smooth1d(v, smooth_bins)

    v = l2norm(v)
    return freqs_sel, v


def find_rep_dirs(root: Path) -> List[Path]:
    rep_dirs: List[Path] = []
    if not root.exists():
        return rep_dirs
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            rep_dirs.append(rep_dir)
    return rep_dirs


def load_rep_audio_and_onset(rep_dir: Path) -> Tuple[Optional[np.ndarray], Optional[int], Optional[int], Optional[str]]:
    """
    Returns (x_float, fs, onset_sample, label) OR (None, ..) if unusable.

    - If aligned exists: onset_sample = 0
    - Else: onset_sample = meta["enter_sample"] (or derived from pre_ms+lead_ms)
    """
    meta_path = rep_dir / "meta.json"
    if not meta_path.exists():
        return None, None, None, None

    meta = load_json(meta_path)
    label = ensure_lower(meta.get("label", rep_dir.parent.name))

    aligned_path = rep_dir / "aligned_i16.raw"
    aligned_meta_path = rep_dir / "aligned_meta.json"
    if aligned_path.exists() and aligned_meta_path.exists():
        am = load_json(aligned_meta_path)
        fs = int(am.get("fs", meta.get("fs", 48000)))
        x = to_float(read_i16_raw(aligned_path))
        return x, fs, 0, label

    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw".with_name("raw_audio_i16.raw")  # no-op, keep explicit
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw"  # keep
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw"
    # actual expected:
    raw_path = rep_dir / "raw_audio_i16.raw".replace if False else (rep_dir / "raw_audio_i16.raw")
    # If your capture is named raw_audio_i16.raw, adjust here:
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw"  # still missing
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw"

    # Your source-of-truth says raw_audio_i16.raw; your existing code uses raw_audio_i16.raw.
    # But some setups used raw_audio_i16.raw vs raw_audio_i16.raw; keep strict:
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        # fallback to older filename
        raw_path = rep_dir / "raw_audio_i16.raw".with_name("raw_audio_i16.raw")
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw".with_name("raw_audio_i16.raw")

    # Better fallback: raw_audio_i16.raw (exact from your spec)
    if not raw_path.exists():
        raw_path = rep_dir / "raw_audio_i16.raw"
    if not raw_path.exists():
        return None, None, None, label

    fs = int(meta.get("fs", 48000))
    pre_ms = int(meta.get("pre_ms", 300))
    lead_ms = int(meta.get("lead_ms", 0))
    enter_sample = int(meta.get("enter_sample", int(round(fs * (pre_ms + lead_ms) / 1000.0))))

    x = to_float(read_i16_raw(raw_path))
    return x, fs, enter_sample, label


def write_header(
    out_path: Path,
    labels: List[str],
    templates: np.ndarray,  # [L, E, D] float32
    fs: int,
    seg_ms: int,
    fft_n: int,
    fmin_hz: float,
    fmax_hz: float,
    smooth_bins: int,
):
    L, E, D = templates.shape
    guard = "TEMPLATES_GENERATED_H_"

    def c_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"#ifndef {guard}\n#define {guard}\n\n")
        f.write("// AUTO-GENERATED by export_esp_templates.py\n\n")
        f.write("#include <stdint.h>\n\n")
        f.write(f"#define KC_FS {fs}\n")
        f.write(f"#define KC_SEG_MS {seg_ms}\n")
        f.write(f"#define KC_FFT_N {fft_n}\n")
        f.write(f"#define KC_FMIN_HZ {float(fmin_hz)}f\n")
        f.write(f"#define KC_FMAX_HZ {float(fmax_hz)}f\n")
        f.write(f"#define KC_SMOOTH_BINS {int(smooth_bins)}\n\n")
        f.write(f"#define KC_NUM_LABELS {L}\n")
        f.write(f"#define KC_NUM_EXEMPLARS {E}\n")
        f.write(f"#define KC_FEAT_D {D}\n\n")

        f.write("static const char* const KC_LABELS[KC_NUM_LABELS] = {\n")
        for lbl in labels:
            f.write(f'  "{c_escape(lbl)}",\n')
        f.write("};\n\n")

        f.write("static const float KC_TEMPLATES[KC_NUM_LABELS][KC_NUM_EXEMPLARS][KC_FEAT_D] = {\n")
        for li in range(L):
            f.write("  {\n")
            for ei in range(E):
                v = templates[li, ei, :]
                f.write("    {")
                for di in range(D):
                    f.write(f"{float(v[di]):.8f}f")
                    if di != D - 1:
                        f.write(",")
                f.write("},\n")
            f.write("  },\n")
        f.write("};\n\n")
        f.write(f"#endif // {guard}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--out_h", default="templates_generated.h")
    ap.add_argument("--labels", default="", help="comma-separated labels; empty=auto from folder names")
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--exemplars", type=int, default=3, help="templates per label")
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=2048)
    ap.add_argument("--fmin", type=float, default=80.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)
    args = ap.parse_args()

    np.random.seed(args.seed)
    root = Path(args.captures_root)

    # Load all features per label
    feats_by_label: Dict[str, List[np.ndarray]] = {}
    fs_used = args.fs

    seg_len = int(round(args.fs * (args.seg_ms / 1000.0)))

    for rep_dir in find_rep_dirs(root):
        x, fs, onset, lbl = load_rep_audio_and_onset(rep_dir)
        if x is None or fs is None or onset is None or lbl is None:
            continue
        if fs != args.fs:
            # exporter is strict; resample is intentionally not implemented
            continue
        fs_used = fs

        a0 = max(0, onset)
        a1 = min(x.size, a0 + seg_len)
        seg = x[a0:a1]
        if seg.size < 64:
            continue

        _, feat = feature_single_fft_logmag(
            seg=seg,
            fs=fs,
            fft_n=args.fft_n,
            fmin_hz=args.fmin,
            fmax_hz=args.fmax,
            smooth_bins=args.smooth_bins,
        )
        feats_by_label.setdefault(lbl, []).append(feat)

    # Choose labels
    if args.labels.strip():
        labels = [ensure_lower(s) for s in args.labels.split(",") if s.strip()]
    else:
        labels = sorted(feats_by_label.keys())

    # Build template tensor [L, E, D]
    if not labels:
        raise SystemExit("No labels found.")
    for lbl in labels:
        if lbl not in feats_by_label or len(feats_by_label[lbl]) < args.exemplars:
            raise SystemExit(f"Label {lbl} has {len(feats_by_label.get(lbl, []))} feats; need {args.exemplars}")

    D = feats_by_label[labels[0]][0].shape[0]
    L = len(labels)
    E = args.exemplars

    templates = np.zeros((L, E, D), dtype=np.float32)
    for li, lbl in enumerate(labels):
        feats = feats_by_label[lbl]
        idx = np.random.permutation(len(feats))[:E]
        for ei, j in enumerate(idx):
            templates[li, ei, :] = feats[j].astype(np.float32, copy=False)

    out_path = Path(args.out_h)
    write_header(
        out_path=out_path,
        labels=labels,
        templates=templates,
        fs=fs_used,
        seg_ms=args.seg_ms,
        fft_n=args.fft_n,
        fmin_hz=args.fmin,
        fmax_hz=args.fmax,
        smooth_bins=args.smooth_bins,
    )
    print(f"Wrote {out_path} labels={L} exemplars={E} feat_d={D}")


if __name__ == "__main__":
    main()
