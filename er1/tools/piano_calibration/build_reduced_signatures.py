#!/usr/bin/env python3
import argparse, csv, json, math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np

# ----------------------------
# Helpers
# ----------------------------
def is_number(x) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False

def hz_to_cents_from_1hz(f_hz: float) -> float:
    if f_hz <= 0:
        return float("nan")
    return 1200.0 * math.log2(f_hz)

def cents_diff(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0:
        return float("inf")
    return abs(hz_to_cents_from_1hz(a_hz) - hz_to_cents_from_1hz(b_hz))

def robust_mad(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    m = np.median(x)
    mad = np.median(np.abs(x - m))
    return float(mad * 1.4826)

def safe_median(x: np.ndarray, default: float = 0.0) -> float:
    return float(np.median(x)) if x.size else default

def safe_mad(x: np.ndarray) -> float:
    return robust_mad(x) if x.size else 0.0

# ----------------------------
# Parse CAL_WIN rows from your serial log
# ----------------------------
PEAK_FIELDS = [
    ("p1_hz", "p1_mag"),
    ("p2_hz", "p2_mag"),
    ("p3_hz", "p3_mag"),
    ("p4_hz", "p4_mag"),
    ("p5_hz", "p5_mag"),
    ("p6_hz", "p6_mag"),
    ("p7_hz", "p7_mag"),
    ("p8_hz", "p8_mag"),
]

def iter_cal_win_rows(log_path: Path) -> Tuple[List[str], List[List[str]]]:
    header: Optional[List[str]] = None
    rows: List[List[str]] = []
    with log_path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if header is None and line.startswith("type,t_ms,key_idx,key,"):
                header = next(csv.reader([line]))
                continue
            if line.startswith("CAL_WIN,"):
                if header is None:
                    raise RuntimeError("Found CAL_WIN but no CSV header line")
                rows.append(next(csv.reader([line])))
    if header is None:
        raise RuntimeError("No CSV header found in log.")
    if not rows:
        raise RuntimeError("No CAL_WIN rows found in log.")
    return header, rows

def rows_to_dicts(header: List[str], rows: List[List[str]]) -> List[Dict[str, str]]:
    out = []
    hlen = len(header)
    for r in rows:
        if len(r) < hlen:
            r = r + [""] * (hlen - len(r))
        out.append({header[i]: r[i] for i in range(hlen)})
    return out

# ----------------------------
# Noise gate (from SILENCE rows)
# ----------------------------
@dataclass
class NoiseModel:
    rms_ac_med: float
    rms_ac_mad: float
    spec_total_med: float
    spec_total_mad: float
    flatness_med: float
    flatness_mad: float

def detect_silence_rows(dict_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    sil = []
    for d in dict_rows:
        key = (d.get("key","") or "").strip().lower()
        idx_s = (d.get("key_idx","") or "").strip()
        if key in ("silence", "sil", "noise") or idx_s == "-1":
            sil.append(d)
    return sil

def build_noise_model(sil_rows: List[Dict[str, str]]) -> NoiseModel:
    def col(name: str) -> np.ndarray:
        vals = []
        for d in sil_rows:
            v = d.get(name, "")
            if is_number(v):
                vals.append(float(v))
        return np.asarray(vals, dtype=np.float64)

    rms  = col("rms_ac")
    tot  = col("spec_total")
    flat = col("flatness")

    return NoiseModel(
        rms_ac_med=float(np.median(rms)) if rms.size else 0.0,
        rms_ac_mad=safe_mad(rms),
        spec_total_med=float(np.median(tot)) if tot.size else 0.0,
        spec_total_mad=safe_mad(tot),
        flatness_med=float(np.median(flat)) if flat.size else 0.0,
        flatness_mad=safe_mad(flat),
    )

def passes_gate(d: Dict[str, str], noise: NoiseModel, gate_sigma: float) -> bool:
    def getf(name: str) -> float:
        v = d.get(name, "")
        return float(v) if is_number(v) else float("nan")

    rms  = getf("rms_ac")
    tot  = getf("spec_total")
    flat = getf("flatness")

    rms_thr = noise.rms_ac_med + gate_sigma * max(noise.rms_ac_mad, 1e-9)
    tot_thr = noise.spec_total_med + gate_sigma * max(noise.spec_total_mad, 1e-9)

    ok = True
    if np.isfinite(rms): ok &= (rms > rms_thr)
    if np.isfinite(tot): ok &= (tot > tot_thr)

    # Reject broadband/noisy (too flat)
    if np.isfinite(flat) and noise.flatness_mad > 0:
        flat_thr = noise.flatness_med + 6.0 * noise.flatness_mad
        ok &= (flat < flat_thr)

    return bool(ok)

# ----------------------------
# Reduced signature extraction
# ----------------------------
@dataclass
class KeyReduced:
    key_idx: int
    key: str
    windows_total: int
    windows_used: int
    # f0 in Hz
    f0_med_hz: float
    f0_mad_hz: float
    # harmonic ratios (mag at k*f0 / mag at f0)
    h2_h1_med: float
    h2_h1_mad: float
    h3_h1_med: float
    h3_h1_mad: float
    h4_h1_med: float
    h4_h1_mad: float

def get_peaks(d: Dict[str,str]) -> List[Tuple[float,float]]:
    peaks = []
    for fhz_name, fmag_name in PEAK_FIELDS:
        fhz = d.get(fhz_name,"")
        fmag = d.get(fmag_name,"")
        if is_number(fhz) and is_number(fmag):
            f = float(fhz); m = float(fmag)
            if f > 0 and m > 0:
                peaks.append((f,m))
    # strongest first (already logged strongest-first usually, but enforce)
    peaks.sort(key=lambda x: x[1], reverse=True)
    return peaks

def harmonic_score_f0(peaks: List[Tuple[float,float]], f0: float,
                     tol_cents: float, max_harm: int) -> Tuple[float,int]:
    # score by how much peak energy aligns with harmonics of f0
    score = 0.0
    matches = 0
    for (f,m) in peaks:
        k = int(round(f / f0))
        if k < 1 or k > max_harm:
            continue
        if cents_diff(f, f0*k) <= tol_cents:
            # weight closer matches higher
            cd = cents_diff(f, f0*k)
            w = 1.0 - (cd / tol_cents)
            score += m * max(0.0, w)
            matches += 1
    return score, matches

def pick_f0_from_peaks(peaks: List[Tuple[float,float]],
                       f0_min: float, f0_max: float,
                       tol_cents: float = 25.0,
                       max_div: int = 6,
                       max_harm: int = 10) -> Optional[float]:
    if not peaks:
        return None

    best = None
    best_score = -1.0
    best_matches = 0

    # candidates: peak_f / div
    for (pf, pm) in peaks:
        for div in range(1, max_div+1):
            f0 = pf / float(div)
            if f0 < f0_min or f0 > f0_max:
                continue
            sc, matches = harmonic_score_f0(peaks, f0, tol_cents=tol_cents, max_harm=max_harm)
            # require at least some harmonic structure
            if matches < 3:
                continue
            # small bias: prefer more matches
            sc2 = sc * (1.0 + 0.15*matches)
            if sc2 > best_score:
                best_score = sc2
                best = f0
                best_matches = matches

    return best

def mag_near(peaks: List[Tuple[float,float]], target_hz: float, tol_cents: float) -> float:
    best_m = 0.0
    best_cd = 1e9
    for (f,m) in peaks:
        cd = cents_diff(f, target_hz)
        if cd <= tol_cents and cd < best_cd:
            best_cd = cd
            best_m = m
    return best_m

def build_reduced_for_key(key_idx: int, key: str, rows: List[Dict[str,str]],
                          noise: NoiseModel,
                          gate_sigma: float,
                          f0_min: float, f0_max: float,
                          tol_cents_f0: float,
                          tol_cents_h: float) -> KeyReduced:
    used = [d for d in rows if passes_gate(d, noise, gate_sigma)]
    f0s = []
    r2 = []
    r3 = []
    r4 = []

    for d in used:
        peaks = get_peaks(d)
        f0 = pick_f0_from_peaks(peaks, f0_min, f0_max, tol_cents=tol_cents_f0)
        if f0 is None:
            continue

        m1 = mag_near(peaks, f0, tol_cents=tol_cents_h)
        if m1 <= 0:
            continue

        m2 = mag_near(peaks, 2.0*f0, tol_cents=tol_cents_h)
        m3 = mag_near(peaks, 3.0*f0, tol_cents=tol_cents_h)
        m4 = mag_near(peaks, 4.0*f0, tol_cents=tol_cents_h)

        # require at least 2 harmonics present to make ratios meaningful
        present = sum(1 for m in (m2,m3,m4) if m > 0)
        if present < 2:
            continue

        f0s.append(f0)
        if m2 > 0: r2.append(m2/m1)
        if m3 > 0: r3.append(m3/m1)
        if m4 > 0: r4.append(m4/m1)

    f0_arr = np.asarray(f0s, dtype=np.float64)
    r2_arr = np.asarray(r2, dtype=np.float64)
    r3_arr = np.asarray(r3, dtype=np.float64)
    r4_arr = np.asarray(r4, dtype=np.float64)

    return KeyReduced(
        key_idx=key_idx,
        key=key,
        windows_total=len(rows),
        windows_used=len(used),
        f0_med_hz=safe_median(f0_arr, 0.0),
        f0_mad_hz=safe_mad(f0_arr),
        h2_h1_med=safe_median(r2_arr, 0.0),
        h2_h1_mad=safe_mad(r2_arr),
        h3_h1_med=safe_median(r3_arr, 0.0),
        h3_h1_mad=safe_mad(r3_arr),
        h4_h1_med=safe_median(r4_arr, 0.0),
        h4_h1_mad=safe_mad(r4_arr),
    )

def main():
    ap = argparse.ArgumentParser(description="Build reduced piano key signatures from CAL_WIN log.")
    ap.add_argument("--log", required=True, help="Path to piano_calibration.log")
    ap.add_argument("--out", required=True, help="Output JSON path (model_reduced.json)")
    ap.add_argument("--gate-sigma", type=float, default=4.0, help="Noise gate in MAD sigmas (default 4.0)")

    ap.add_argument("--f0-min", type=float, default=27.0, help="Min f0 Hz (default 27)")
    ap.add_argument("--f0-max", type=float, default=1200.0, help="Max f0 Hz (default 1200)")

    ap.add_argument("--tol-cents-f0", type=float, default=25.0, help="Cents tolerance used in f0 harmonic scoring")
    ap.add_argument("--tol-cents-h", type=float, default=45.0, help="Cents tolerance for extracting harmonic mags")

    args = ap.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()

    header, rows = iter_cal_win_rows(log_path)
    dict_rows = rows_to_dicts(header, rows)

    sil_rows = detect_silence_rows(dict_rows)
    if not sil_rows:
        raise RuntimeError("No SILENCE rows detected (need key_idx=-1 or key='SILENCE').")
    noise = build_noise_model(sil_rows)

    # group by key_idx, key
    by_key: Dict[Tuple[int,str], List[Dict[str,str]]] = {}
    for d in dict_rows:
        idx_s = (d.get("key_idx","") or "").strip()
        key = (d.get("key","") or "").strip()
        if not idx_s or not is_number(idx_s):
            continue
        idx = int(float(idx_s))
        by_key.setdefault((idx,key), []).append(d)

    reduced: List[KeyReduced] = []
    for (idx,key), rlist in sorted(by_key.items(), key=lambda x: x[0][0]):
        if idx == -1 or key.strip().lower() in ("silence","sil","noise"):
            continue
        kr = build_reduced_for_key(
            key_idx=idx, key=key, rows=rlist,
            noise=noise,
            gate_sigma=args.gate_sigma,
            f0_min=args.f0_min, f0_max=args.f0_max,
            tol_cents_f0=args.tol_cents_f0,
            tol_cents_h=args.tol_cents_h
        )
        reduced.append(kr)

    out = {
        "noise": asdict(noise),
        "params": {
            "gate_sigma": args.gate_sigma,
            "f0_min": args.f0_min,
            "f0_max": args.f0_max,
            "tol_cents_f0": args.tol_cents_f0,
            "tol_cents_h": args.tol_cents_h,
            "note": "Reduced model: f0 median/MAD + harmonic ratios H2/H1,H3/H1,H4/H1"
        },
        "keys": [asdict(k) for k in reduced]
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("OK")
    print(f"Noise rms_med={noise.rms_ac_med:.2f} mad={noise.rms_ac_mad:.2f} | "
          f"tot_med={noise.spec_total_med:.2f} mad={noise.spec_total_mad:.2f} | "
          f"flat_med={noise.flatness_med:.4f} mad={noise.flatness_mad:.4f}")
    print(f"Wrote: {out_path}")
    print(f"Keys: {len(reduced)}")

if __name__ == "__main__":
    main()
