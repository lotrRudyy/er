#!/usr/bin/env python3
"""
Visualize piano_calibration.log to see if each key is uniquely identifiable.

What it assumes about each CAL_WIN line (based on your log):
CAL_WIN,<ms>,<key_idx>,<key>,<err>,<rms>,<spec>,<pr>,<f0bin>,<min>,<max>,<energy>,
       <fA>,<fB>,<fC>,<flat>,
       <band0>,<band1>,<band2>,<band3>,<band4>,<band5>,
       then repeating pairs: <peak_hz_1>,<peak_mag_1>,<peak_hz_2>,<peak_mag_2>, ...

It will:
- Parse all CAL_WIN rows into a dataframe
- Derive robust “top peaks” features
- Aggregate per key (median/IQR)
- Plot:
  1) Peak scatter (peak1 vs peak2)
  2) PCA projection (2D) of per-window vectors
  3) “How unique is each key?” nearest-neighbor distance per key (bigger = better)
"""

from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np

# Optional deps
try:
    import pandas as pd
except Exception as e:
    raise SystemExit("Please install pandas: pip install pandas") from e

try:
    import matplotlib.pyplot as plt
except Exception as e:
    raise SystemExit("Please install matplotlib: pip install matplotlib") from e

# sklearn is optional but strongly recommended for PCA
try:
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import RobustScaler
    SKLEARN_OK = True
except Exception:
    SKLEARN_OK = False


def safe_float(x: str) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def parse_cal_win(parts: List[str]) -> Optional[Dict]:
    # parts[0] == "CAL_WIN"
    if len(parts) < 4:
        return None

    ms = safe_float(parts[1])
    key_idx = parts[2]
    key = parts[3]

    # Must have the core numeric block through "flat"
    # Indices:
    # 4 err, 5 rms, 6 spec, 7 pr, 8 f0bin, 9 min, 10 max, 11 energy,
    # 12 fA, 13 fB, 14 fC, 15 flat
    core_needed = 16
    if len(parts) < core_needed:
        return None

    nums = [safe_float(p) for p in parts[4:]]
    if any(v is None for v in nums[: (core_needed - 4)]):
        return None

    err, rms, spec, pr, f0bin, mn, mx, energy, fA, fB, fC, flat = nums[:12]

    # Next 6 values are band energies (if present)
    bands = [np.nan] * 6
    cursor = 12
    if len(nums) >= cursor + 6:
        bands = nums[cursor:cursor + 6]
        cursor += 6

    # Remaining are peak pairs: (hz, mag)
    peak_pairs = []
    remain = nums[cursor:]
    # keep only complete pairs
    for i in range(0, len(remain) - 1, 2):
        hz = remain[i]
        mag = remain[i + 1]
        if hz is None or mag is None:
            continue
        # ignore clearly bogus frequencies
        if hz <= 0 or hz > 12000:
            continue
        peak_pairs.append((hz, mag))

    # Sort peaks by magnitude desc
    peak_pairs.sort(key=lambda t: t[1], reverse=True)

    # Take top N peaks
    TOPN = 6
    top_peaks = peak_pairs[:TOPN]
    # pad
    while len(top_peaks) < TOPN:
        top_peaks.append((np.nan, np.nan))

    row = dict(
        ms=ms,
        key_idx=int(key_idx) if key_idx.lstrip("-").isdigit() else key_idx,
        key=key,
        err=err,
        rms=rms,
        spec=spec,
        pr=pr,
        f0bin=f0bin,
        min=mn,
        max=mx,
        energy=energy,
        fA=fA,
        fB=fB,
        fC=fC,
        flat=flat,
        band0=bands[0], band1=bands[1], band2=bands[2],
        band3=bands[3], band4=bands[4], band5=bands[5],
    )

    for i, (hz, mag) in enumerate(top_peaks, start=1):
        row[f"p{i}_hz"] = hz
        row[f"p{i}_mag"] = mag

    return row


def parse_feedback(line: str) -> Optional[Dict]:
    # Example (your log):
    # FEEDBACK key_idx=10 key=G1  best_f=134.97Hz best_conf=35.283  rms_mean=21946.4 ...
    if not line.startswith("FEEDBACK"):
        return None
    out = {}
    try:
        toks = line.replace("=", " ").replace("Hz", "").split()
        # naive extraction
        for i, t in enumerate(toks):
            if t == "key_idx":
                out["key_idx"] = int(toks[i + 1])
            elif t == "key":
                out["key"] = toks[i + 1]
            elif t == "best_f":
                out["best_f"] = float(toks[i + 1])
            elif t == "best_conf":
                out["best_conf"] = float(toks[i + 1])
            elif t == "rms_mean":
                out["rms_mean"] = float(toks[i + 1])
            elif t == "rms_max":
                out["rms_max"] = float(toks[i + 1])
            elif t == "windows":
                out["windows"] = int(toks[i + 1])
        if "key_idx" in out and "key" in out:
            return out
    except Exception:
        return None
    return None


def load_log(path: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows: List[Dict] = []
    feedback_rows: List[Dict] = []

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.startswith("CAL_WIN,"):
                parts = line.split(",")
                row = parse_cal_win(parts)
                if row:
                    rows.append(row)
            elif line.startswith("FEEDBACK"):
                fb = parse_feedback(line)
                if fb:
                    feedback_rows.append(fb)

    df = pd.DataFrame(rows)
    fbdf = pd.DataFrame(feedback_rows)

    if df.empty:
        raise SystemExit("No CAL_WIN rows parsed. Check the log format / file path.")

    return df, fbdf


def make_feature_matrix(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """
    Build per-window feature vectors that are reasonably stable for clustering.
    We use:
      - flat, pr, err, rms (log), energy (log)
      - band energies (log)
      - peak freqs (p1..p4_hz) and relative mags (p1..p4_mag normalized)
    """
    cols = []
    feats = []

    def add_col(name: str, arr: np.ndarray):
        cols.append(name)
        feats.append(arr.reshape(-1, 1))

    # Robust transforms
    add_col("flat", df["flat"].to_numpy(dtype=float))
    add_col("pr", df["pr"].to_numpy(dtype=float))
    add_col("err", df["err"].to_numpy(dtype=float))

    # Log-ish scaling for huge magnitudes
    add_col("log_rms", np.log10(np.maximum(df["rms"].to_numpy(dtype=float), 1e-9)))
    add_col("log_energy", np.log10(np.maximum(df["energy"].to_numpy(dtype=float), 1e-9)))

    for b in range(6):
        name = f"band{b}"
        add_col(f"log_{name}", np.log10(np.maximum(df[name].to_numpy(dtype=float), 1e-9)))

    # Peaks: take first 4
    for i in range(1, 5):
        add_col(f"p{i}_hz", df[f"p{i}_hz"].to_numpy(dtype=float))

    # Normalize peak magnitudes by p1_mag (to remove loudness dependence)
    p1 = df["p1_mag"].to_numpy(dtype=float)
    p1 = np.where(np.isfinite(p1) & (p1 > 0), p1, np.nan)

    for i in range(1, 5):
        mag = df[f"p{i}_mag"].to_numpy(dtype=float)
        rel = mag / p1
        add_col(f"p{i}_relmag", np.nan_to_num(rel, nan=0.0, posinf=0.0, neginf=0.0))

    X = np.hstack(feats)

    # Replace NaNs in hz with 0 (missing peak)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X, cols


def save_peak_scatter(df: pd.DataFrame, outdir: Path) -> None:
    plt.figure()
    # use p1_hz vs p2_hz; many keys separate just from this
    x = df["p1_hz"].to_numpy(dtype=float)
    y = df["p2_hz"].to_numpy(dtype=float)

    # color by key_idx (but keep it simple)
    c = df["key_idx"].to_numpy()

    sc = plt.scatter(x, y, s=6, c=c)
    plt.xlabel("Top peak frequency p1 (Hz)")
    plt.ylabel("2nd peak frequency p2 (Hz)")
    plt.title("Peak scatter: p1 vs p2 (colored by key_idx)")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(outdir / "scatter_p1_p2.png", dpi=200)
    plt.close()


def save_pca_plot(df: pd.DataFrame, X: np.ndarray, outdir: Path) -> None:
    if not SKLEARN_OK:
        print("sklearn not installed; skipping PCA plot. Install: pip install scikit-learn")
        return

    scaler = RobustScaler(with_centering=True, with_scaling=True, quantile_range=(10, 90))
    Xs = scaler.fit_transform(X)

    pca = PCA(n_components=2, random_state=0)
    Z = pca.fit_transform(Xs)

    plt.figure()
    c = df["key_idx"].to_numpy()
    plt.scatter(Z[:, 0], Z[:, 1], s=6, c=c)
    plt.xlabel("PC1")
    plt.ylabel("PC2")
    plt.title(f"PCA of per-window signatures (explained: {pca.explained_variance_ratio_[0]:.2f}, {pca.explained_variance_ratio_[1]:.2f})")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(outdir / "pca_windows.png", dpi=200)
    plt.close()


def save_uniqueness_plot(df: pd.DataFrame, X: np.ndarray, outdir: Path) -> None:
    """
    A practical “can I identify keys?” metric:

    For each key:
      - compute the median feature vector
      - find its nearest other-key median distance
    If nearest distance is small, that key will be confused with a neighbor.
    """
    # median vector per key
    keys = sorted(df["key"].unique().tolist())
    med = []
    for k in keys:
        idx = (df["key"] == k).to_numpy()
        med.append(np.median(X[idx], axis=0))
    M = np.vstack(med)

    # pairwise distances
    # (n_keys x n_keys)
    d = np.sqrt(((M[:, None, :] - M[None, :, :]) ** 2).sum(axis=2))
    np.fill_diagonal(d, np.inf)
    nn = d.min(axis=1)

    # plot
    order = np.argsort(nn)  # worst first
    plt.figure(figsize=(10, 5))
    plt.plot(nn[order], marker="o", linewidth=1)
    plt.xticks(range(len(keys)), [keys[i] for i in order], rotation=90, fontsize=7)
    plt.ylabel("Nearest-other-key distance (median signature space)")
    plt.title("Key uniqueness (higher = easier to identify uniquely)")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(outdir / "key_uniqueness.png", dpi=200)
    plt.close()

    # save a CSV for “which keys collide?”
    rows = []
    for i, k in enumerate(keys):
        j = int(np.argmin(d[i]))
        rows.append((k, keys[j], float(d[i, j])))
    rows.sort(key=lambda t: t[2])
    out_csv = outdir / "nearest_key_pairs.csv"
    with out_csv.open("w", encoding="utf-8") as f:
        f.write("key,nearest_key,distance\n")
        for a, b, dist in rows:
            f.write(f"{a},{b},{dist:.6f}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", type=str)
    ap.add_argument("--outdir", type=str, default="out")
    args = ap.parse_args()

    logpath = Path(args.logfile)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    df, fbdf = load_log(logpath)

    # Save parsed snapshots
    df.to_csv(outdir / "cal_windows_parsed.csv", index=False)
    if not fbdf.empty:
        fbdf.to_csv(outdir / "feedback_parsed.csv", index=False)

    # Build features
    X, cols = make_feature_matrix(df)

    # Plots
    save_peak_scatter(df, outdir)
    save_pca_plot(df, X, outdir)
    save_uniqueness_plot(df, X, outdir)

    # Quick console summary
    print(f"Parsed CAL_WIN windows: {len(df)} across keys: {df['key'].nunique()}")
    print(f"Wrote outputs to: {outdir.resolve()}")
    print("Generated:")
    print(" - scatter_p1_p2.png")
    if SKLEARN_OK:
        print(" - pca_windows.png")
    print(" - key_uniqueness.png")
    print(" - nearest_key_pairs.csv")
    print(" - cal_windows_parsed.csv (+ feedback_parsed.csv if present)")


if __name__ == "__main__":
    main()
