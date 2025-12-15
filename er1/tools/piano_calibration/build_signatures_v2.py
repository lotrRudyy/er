#!/usr/bin/env python3
"""
Build model_v2.json from CAL_WIN logs.

Input: a log file that contains CSV rows with header:
type,t_ms,key_idx,key,mean,rms_ac,ac_peak,crest,zc,min,max,spec_total,centroid_hz,bandwidth_hz,rolloff85_hz,flatness,
band1_20_80,...,band6_1280_4000,p1_hz,p1_mag,...,p8_hz,p8_mag
(see your logger header)
"""

import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# -----------------------------
# Helpers
# -----------------------------

def median(xs: List[float]) -> float:
    return statistics.median(xs) if xs else 0.0

def mad(xs: List[float]) -> float:
    """Median absolute deviation (not scaled)."""
    if not xs:
        return 0.0
    m = median(xs)
    return median([abs(x - m) for x in xs])

def cents_diff(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0:
        return 1e9
    return abs(1200.0 * math.log2(a_hz / b_hz))

def hz_to_key_idx_a0(f_hz: float) -> int:
    """key_idx 0 = A0 = 27.5 Hz"""
    if f_hz <= 0:
        return -999
    return int(round(12.0 * math.log2(f_hz / 27.5)))

def key_idx_to_hz_a0(key_idx: int) -> float:
    return 27.5 * (2.0 ** (key_idx / 12.0))

# -----------------------------
# Peak-based f0 estimator (same logic used on ESP32)
# -----------------------------

@dataclass
class Peak:
    hz: float
    mag: float

@dataclass
class F0Result:
    f0_hz: float
    harm_score: float
    harm_hits: int
    coverage: float
    alt2_score: float  # score for 2*f0
    sub_score: float   # score for 0.5*f0

def harmonic_score(peaks: List[Peak], f0: float, tol_cents: float, max_harm: int) -> Tuple[float, int, float]:
    if f0 <= 0:
        return 0.0, 0, 0.0
    total_top = sum(p.mag for p in peaks) + 1e-9
    used = [False] * len(peaks)

    score = 0.0
    hits = 0
    matched_mag = 0.0

    for n in range(1, max_harm + 1):
        target = f0 * n
        best_i = -1
        best_mag = 0.0
        for i, p in enumerate(peaks):
            if used[i]:
                continue
            if cents_diff(p.hz, target) <= tol_cents:
                if p.mag > best_mag:
                    best_mag = p.mag
                    best_i = i
        if best_i >= 0:
            used[best_i] = True
            # downweight higher harmonics slightly
            w = 1.0 / (1.0 + 0.15 * (n - 1))
            score += w * best_mag
            matched_mag += best_mag
            hits += 1

    coverage = matched_mag / total_top
    return score, hits, coverage

def estimate_f0_from_peaks(
    peaks: List[Peak],
    fmin: float = 27.5,
    fmax: float = 4186.0,
    tol_cents_low: float = 25.0,
    tol_cents_mid: float = 18.0,
    tol_cents_high: float = 12.0,
    max_harm: int = 10,
) -> Optional[F0Result]:
    if not peaks:
        return None

    # Decide tolerance based on candidate frequency (will refine after picking candidate).
    def tol_for(f0: float) -> float:
        if f0 < 140.0:
            return tol_cents_low
        if f0 < 900.0:
            return tol_cents_mid
        return tol_cents_high

    candidates: List[float] = []
    for p in peaks:
        if p.hz <= 0:
            continue
        for h in range(1, max_harm + 1):
            f0 = p.hz / h
            if fmin <= f0 <= fmax:
                candidates.append(f0)

    if not candidates:
        return None

    best = None
    best_tuple = (-1.0, -1, -1.0)  # (score, hits, coverage)

    for f0 in candidates:
        tol = tol_for(f0)
        score, hits, cov = harmonic_score(peaks, f0, tol, max_harm)

        # extra push for low f0 missing-fundamental cases: allow hits excluding harmonic 1
        # (we implement by not requiring harm1, but we still use score/hits/cov to decide)
        tup = (score, hits, cov)
        if tup > best_tuple:
            best_tuple = tup
            best = f0

    if best is None:
        return None

    tol = tol_for(best)
    score, hits, cov = harmonic_score(peaks, best, tol, max_harm)

    # octave competition
    alt2, _, _ = harmonic_score(peaks, best * 2.0, tol_for(best * 2.0), max_harm)
    sub,  _, _ = harmonic_score(peaks, best * 0.5, tol_for(best * 0.5), max_harm)

    return F0Result(best, score, hits, cov, alt2, sub)

# -----------------------------
# Parsing
# -----------------------------

def parse_cal_win_rows(path: Path) -> List[Dict]:
    rows = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("{") or line.startswith("===") or line.startswith("TIP:"):
                continue
            if not line.startswith("CAL_WIN,"):
                continue
            # Parse using csv to respect commas
            parts = next(csv.reader([line]))
            # Expect at least up to p8_mag
            if len(parts) < 16 + 6 + 16:
                continue

            # fixed columns
            d = {
                "t_ms": int(parts[1]),
                "key_idx": int(parts[2]),
                "key": parts[3],
                "mean": float(parts[4]),
                "rms_ac": float(parts[5]),
                "spec_total": float(parts[11]),
                "flatness": float(parts[15]),
            }

            # peaks start after band6; indexes:
            # header shows bands 1..6 then p1_hz,p1_mag,...,p8_hz,p8_mag
            # bands are parts[16..21], so p1_hz starts at 22
            peaks = []
            pstart = 22
            for i in range(8):
                hz = float(parts[pstart + i * 2 + 0])
                mag = float(parts[pstart + i * 2 + 1])
                if hz > 0 and mag > 0:
                    peaks.append(Peak(hz, mag))
            # sort by mag desc (just in case)
            peaks.sort(key=lambda p: p.mag, reverse=True)
            d["peaks"] = peaks
            rows.append(d)
    return rows

# -----------------------------
# Training
# -----------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", type=Path, help="piano_calibration.log or extracted CSV log")
    ap.add_argument("-o", "--out", type=Path, default=Path("model_v2.json"))
    args = ap.parse_args()

    rows = parse_cal_win_rows(args.logfile)
    if not rows:
        raise SystemExit("No CAL_WIN rows parsed.")

    # Split noise vs note
    noise = [r for r in rows if r["key_idx"] < 0 or r["key"].upper() == "SILENCE"]
    notes = [r for r in rows if r not in noise and r["key_idx"] >= 0]

    if not noise:
        raise SystemExit("No SILENCE frames found (key_idx=-1).")
    if not notes:
        raise SystemExit("No note frames found (key_idx>=0).")

    # Noise model
    noise_rms = [r["rms_ac"] for r in noise]
    noise_spec = [r["spec_total"] for r in noise]
    noise_flat = [r["flatness"] for r in noise]

    noise_model = {
        "rms_med": median(noise_rms),
        "rms_mad": mad(noise_rms),
        "spec_med": median(noise_spec),
        "spec_mad": mad(noise_spec),
        "flat_med": median(noise_flat),
        "flat_mad": mad(noise_flat),
    }

    # Estimate f0 + harmonic metrics for note rows
    f0_rows = []
    for r in notes:
        res = estimate_f0_from_peaks(r["peaks"])
        if res is None:
            continue
        f0_rows.append((r, res))

    if len(f0_rows) < 50:
        raise SystemExit(f"Too few frames with usable f0 ({len(f0_rows)}).")

    # Global tuning offset (in cents): est_f0 vs theoretical for labeled key_idx
    deltas = []
    hs = []
    hits = []
    covs = []
    flats = []
    for r, res in f0_rows:
        theo = key_idx_to_hz_a0(r["key_idx"])
        deltas.append(1200.0 * math.log2(res.f0_hz / theo))
        hs.append(res.harm_score)
        hits.append(res.harm_hits)
        covs.append(res.coverage)
        flats.append(r["flatness"])

    tuning_offset_cents = median(deltas)

    # Robust global thresholds from note distributions
    # We set:
    # - flat_max = median(flat) + 4*MAD(flat) (cap to 0.85)
    # - hs_min   = median(hs)   - 4*MAD(hs)   (floor to >0)
    # - hits_min = max(3, round(median(hits) - 2))
    # - cov_min  = median(cov)  - 4*MAD(cov)  (floor to 0.35)
    flat_max = min(0.85, median(flats) + 4.0 * mad(flats))
    hs_min = max(0.0, median(hs) - 4.0 * mad(hs))
    hits_min = max(3, int(round(median(hits) - 2)))
    cov_min = max(0.35, median(covs) - 4.0 * mad(covs))

    # Gates relative to noise (these are intentionally conservative)
    gates = {
        "k_rms": 8.0,
        "k_spec": 8.0,
        "flat_max": float(flat_max),
        "hs_min": float(hs_min),
        "hits_min": int(hits_min),
        "cov_min": float(cov_min),

        # octave guard:
        "oct_ratio_max": 0.92,  # if score(2*f0) >= 0.92*score(f0), prefer lower f0 if hits not worse
    }

    # Pitch tolerances (cents) used in peak-harmonic matching
    pitch = {
        "fmin": 27.5,
        "fmax": 4186.0,
        "max_harm": 10,
        "tol_cents_low": 25.0,
        "tol_cents_mid": 18.0,
        "tol_cents_high": 12.0,
        "tuning_offset_cents": float(tuning_offset_cents),
    }

    model = {
        "version": 2,
        "noise": noise_model,
        "gates": gates,
        "pitch": pitch,
        "stability": {
            "stable_ms": 300,
            "cents_stable": 25.0,
            "hold_ms": 180,
        }
    }

    args.out.write_text(json.dumps(model, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} with {len(noise)} noise frames and {len(notes)} note frames ({len(f0_rows)} f0-usable).")

if __name__ == "__main__":
    main()
