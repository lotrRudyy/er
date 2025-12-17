#!/usr/bin/env python3
import argparse
import numpy as np
import pandas as pd

def harm_vote_f0_from_peaks(peaks_hz, peaks_mag, kmax=8, bin_hz=0.5, fmin=20.0, fmax=1500.0):
    """
    peaks_hz, peaks_mag: arrays of same length (top peaks)
    Returns: estimated f0 for ONE window using harmonic voting.
    """
    peaks_hz = np.asarray(peaks_hz, dtype=float)
    peaks_mag = np.asarray(peaks_mag, dtype=float)

    m = np.isfinite(peaks_hz) & np.isfinite(peaks_mag) & (peaks_hz > 0) & (peaks_mag > 0)
    peaks_hz, peaks_mag = peaks_hz[m], peaks_mag[m]
    if peaks_hz.size == 0:
        return np.nan

    # Normalize mags to reduce loudness dependence
    peaks_mag = peaks_mag / np.max(peaks_mag)

    # Build votes: candidate = peak/k
    cand = []
    wts = []
    for hz, mag in zip(peaks_hz, peaks_mag):
        for k in range(1, kmax + 1):
            f0 = hz / k
            if fmin <= f0 <= fmax:
                cand.append(f0)
                # weight: stronger peaks + prefer lower k a bit (fundamental-ish)
                wts.append(mag * (1.0 / (k ** 0.6)))
    if not cand:
        return np.nan

    cand = np.asarray(cand, dtype=float)
    wts  = np.asarray(wts, dtype=float)

    # Weighted histogram
    lo, hi = float(np.min(cand)), float(np.max(cand))
    bins = np.arange(lo, hi + bin_hz, bin_hz)
    if bins.size < 3:
        return float(np.average(cand, weights=wts))

    hist, edges = np.histogram(cand, bins=bins, weights=wts)
    i = int(np.argmax(hist))
    f0 = float((edges[i] + edges[i+1]) * 0.5)

    # Refine: weighted average of candidates near the winning bin
    center = f0
    near = np.abs(cand - center) <= (1.5 * bin_hz)
    if np.any(near):
        f0 = float(np.average(cand[near], weights=wts[near]))
    return f0

def robust_mode(x, bin_hz=0.5):
    x = x[np.isfinite(x)]
    if x.size == 0: return np.nan
    lo, hi = float(np.min(x)), float(np.max(x))
    bins = np.arange(lo, hi + bin_hz, bin_hz)
    if bins.size < 3:
        return float(np.median(x))
    hist, edges = np.histogram(x, bins=bins)
    i = int(np.argmax(hist))
    return float((edges[i] + edges[i+1]) * 0.5)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="out/cal_windows_parsed.csv")
    ap.add_argument("--min_windows", type=int, default=30)
    ap.add_argument("--out_csv", default="learned_key_freqs_fixed.csv")
    ap.add_argument("--out_h", default="learned_key_freqs_fixed.h")
    ap.add_argument("--bin_hz", type=float, default=0.5)
    ap.add_argument("--kmax", type=int, default=8)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)

    # You should NOT include SILENCE as a “key”
    df = df[df["key"].astype(str).str.upper() != "SILENCE"].copy()

    need_cols = []
    for i in range(1, 7):
        need_cols += [f"p{i}_hz", f"p{i}_mag"]
    for c in ["key", "key_idx"] + need_cols:
        if c not in df.columns:
            raise SystemExit(f"Missing column {c} in CSV")

    rows = []
    for (key_idx, key), g in df.groupby(["key_idx", "key"]):
        if len(g) < args.min_windows:
            continue

        # per-window f0 estimates
        f0s = []
        for _, r in g.iterrows():
            peaks_hz  = [r[f"p{i}_hz"] for i in range(1, 7)]
            peaks_mag = [r[f"p{i}_mag"] for i in range(1, 7)]
            f0 = harm_vote_f0_from_peaks(peaks_hz, peaks_mag, kmax=args.kmax, bin_hz=args.bin_hz)
            if np.isfinite(f0):
                f0s.append(f0)

        f0s = np.asarray(f0s, dtype=float)
        if f0s.size < args.min_windows:
            continue

        # robust aggregate per key
        f0 = robust_mode(f0s, bin_hz=args.bin_hz)
        med = float(np.median(f0s))
        iqr = float(np.subtract(*np.percentile(f0s, [75, 25])))
        spread = iqr if np.isfinite(iqr) else np.nan

        rows.append((int(key_idx), str(key), len(g), float(f0), med, spread))

    out = pd.DataFrame(rows, columns=["key_idx", "key", "windows", "f0_hz", "median_hz", "iqr_hz"])
    out = out.sort_values("f0_hz")

    out.to_csv(args.out_csv, index=False)

    # Arduino header
    with open(args.out_h, "w", encoding="utf-8") as f:
        f.write("// Auto-generated: harmonic-vote f0 per key (do NOT edit)\n")
        f.write("#pragma once\n\n")
