#!/usr/bin/env python3
import argparse
import numpy as np
import pandas as pd

def robust_mode(x, bin_hz=0.5):
    """Histogram mode with small bins, robust to outliers."""
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.nan
    lo, hi = np.min(x), np.max(x)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.nan
    bins = np.arange(lo, hi + bin_hz, bin_hz)
    if len(bins) < 3:
        return float(np.median(x))
    hist, edges = np.histogram(x, bins=bins)
    i = int(np.argmax(hist))
    # return center of the most-populated bin
    return float((edges[i] + edges[i+1]) * 0.5)

def estimate_f0_for_key(dfk: pd.DataFrame) -> float:
    """
    Estimate per-key f0 from measured peaks.
    Strategy:
      - prefer p1_hz, but remove obvious octave errors by checking whether p2/p3 look like harmonics
      - fall back to p2_hz/2 if that looks more consistent
    """
    p1 = dfk["p1_hz"].to_numpy(dtype=float)
    p2 = dfk["p2_hz"].to_numpy(dtype=float)
    p3 = dfk["p3_hz"].to_numpy(dtype=float)

    # candidate fundamentals per window:
    # - assume p1 is f0
    c1 = p1.copy()

    # - if p2 is ~2*f0, then f0 ~ p2/2
    c2 = p2 / 2.0

    # - if p3 is ~3*f0, then f0 ~ p3/3
    c3 = p3 / 3.0

    # Score each candidate by how well it explains harmonics present in that window
    # We only use cheap checks: closeness of p2 to 2*f0 and p3 to 3*f0
    def harm_score(f0):
        f0 = np.asarray(f0)
        score = np.zeros_like(f0, dtype=float)
        # tolerate big piano detune: allow ±2% relative error (roughly ±34 cents)
        tol = 0.02
        with np.errstate(invalid="ignore", divide="ignore"):
            score += np.where(np.isfinite(p2) & np.isfinite(f0) & (np.abs(p2 - 2*f0) / (2*f0) < tol), 1.0, 0.0)
            score += np.where(np.isfinite(p3) & np.isfinite(f0) & (np.abs(p3 - 3*f0) / (3*f0) < tol), 1.0, 0.0)
        return score

    s1 = harm_score(c1)
    s2 = harm_score(c2)
    s3 = harm_score(c3)

    # pick best candidate per window
    C = np.vstack([c1, c2, c3])   # 3 x N
    S = np.vstack([s1, s2, s3])   # 3 x N
    best = np.argmax(S, axis=0)
    f0 = C[best, np.arange(C.shape[1])]

    # drop nonsense
    f0 = f0[np.isfinite(f0) & (f0 > 20) & (f0 < 5000)]
    if f0.size == 0:
        return np.nan

    # final robust estimate: histogram-mode then median clamp
    m = robust_mode(f0, bin_hz=0.5)
    # clamp to central bulk to avoid weird tails
    med = float(np.median(f0))
    iqr = float(np.subtract(*np.percentile(f0, [75, 25])))
    if np.isfinite(iqr) and iqr > 0:
        lo, hi = med - 2.0*iqr, med + 2.0*iqr
        f0c = f0[(f0 >= lo) & (f0 <= hi)]
        if f0c.size >= max(10, int(0.3*f0.size)):
            m = robust_mode(f0c, bin_hz=0.5)
    return float(m)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="out/cal_windows_parsed.csv")
    ap.add_argument("--min_windows", type=int, default=20, help="minimum windows required per key")
    ap.add_argument("--out_csv", default="learned_key_freqs.csv")
    ap.add_argument("--out_h", default="learned_key_freqs.h")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "key" not in df.columns:
        raise SystemExit("CSV needs a 'key' column.")
    for col in ["p1_hz", "p2_hz", "p3_hz"]:
        if col not in df.columns:
            raise SystemExit(f"CSV missing {col}. Re-run the parser that outputs peaks.")

    rows = []
    for key, dfk in df.groupby("key"):
        if len(dfk) < args.min_windows:
            continue
        f0 = estimate_f0_for_key(dfk)
        if np.isfinite(f0):
            rows.append((key, len(dfk), f0))

    out = pd.DataFrame(rows, columns=["key", "windows", "f0_hz"]).sort_values("f0_hz")
    out.to_csv(args.out_csv, index=False)

    # Arduino header: store as floats in ascending frequency order
    with open(args.out_h, "w", encoding="utf-8") as f:
        f.write("// Auto-generated from calibration log\n")
        f.write("#pragma once\n\n")
        f.write("typedef struct { const char* key; float f0; } key_f0_t;\n\n")
        f.write(f"static const int KEY_F0_COUNT = {len(out)};\n")
        f.write("static const key_f0_t KEY_F0_TABLE[] = {\n")
        for _, r in out.iterrows():
            f.write(f'  {{"{r["key"]}", {r["f0_hz"]:.3f}f}},\n')
        f.write("};\n")

    print(f"Wrote {args.out_csv} and {args.out_h}")
    print(out.head(10).to_string(index=False))

if __name__ == "__main__":
    main()
