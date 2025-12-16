#!/usr/bin/env python3
"""
Reduced signature builder (f0 + harmonic ratios).
"""
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .signatures import (
    NoiseModel,
    PEAK_FIELDS,
    cents_diff_from_1hz,
    detect_silence_rows,
    is_number,
    iter_cal_win_rows,
    passes_gate,
    robust_mad,
    rows_to_dicts,
    build_noise_model,
)


@dataclass
class KeyReduced:
    key_idx: int
    key: str
    windows_total: int
    windows_used: int
    f0_med_hz: float
    f0_mad_hz: float
    h2_h1_med: float
    h2_h1_mad: float
    h3_h1_med: float
    h3_h1_mad: float
    h4_h1_med: float
    h4_h1_mad: float


def safe_mad(x: np.ndarray) -> float:
    return robust_mad(x) if x.size else 0.0

def safe_median(x: np.ndarray, default: float = 0.0) -> float:
    return float(np.median(x)) if x.size else default

def get_peaks(d: Dict[str, str]) -> List[Tuple[float, float]]:
    peaks = []
    for fhz_name, fmag_name in PEAK_FIELDS:
        fhz = d.get(fhz_name, "")
        fmag = d.get(fmag_name, "")
        if is_number(fhz) and is_number(fmag):
            f = float(fhz)
            m = float(fmag)
            if f > 0 and m > 0:
                peaks.append((f, m))
    peaks.sort(key=lambda x: x[1], reverse=True)
    return peaks

def harmonic_score_f0(peaks: List[Tuple[float, float]], f0: float, tol_cents: float, max_harm: int) -> Tuple[float, int]:
    score = 0.0
    matches = 0
    for f, m in peaks:
        k = int(round(f / f0))
        if k < 1 or k > max_harm:
            continue
        if cents_diff_from_1hz(f, f0 * k) <= tol_cents:
            cd = cents_diff_from_1hz(f, f0 * k)
            w = 1.0 - (cd / tol_cents)
            score += m * max(0.0, w)
            matches += 1
    return score, matches

def pick_f0_from_peaks(
    peaks: List[Tuple[float, float]],
    f0_min: float,
    f0_max: float,
    tol_cents: float = 25.0,
    max_div: int = 6,
    max_harm: int = 10
) -> Optional[float]:
    if not peaks:
        return None

    best = None
    best_score = -1.0
    best_matches = 0

    for pf, pm in peaks:
        for div in range(1, max_div + 1):
            f0 = pf / float(div)
            if f0 < f0_min or f0 > f0_max:
                continue
            sc, matches = harmonic_score_f0(peaks, f0, tol_cents=tol_cents, max_harm=max_harm)
            if matches < 3:
                continue
            sc2 = sc * (1.0 + 0.15 * matches)
            if sc2 > best_score:
                best_score = sc2
                best = f0
                best_matches = matches

    return best

def mag_near(peaks: List[Tuple[float, float]], target_hz: float, tol_cents: float) -> float:
    best_m = 0.0
    best_cd = 1e9
    for f, m in peaks:
        cd = cents_diff_from_1hz(f, target_hz)
        if cd <= tol_cents and cd < best_cd:
            best_cd = cd
            best_m = m
    return best_m

def build_reduced_for_key(
    key_idx: int,
    key: str,
    rows: List[Dict[str, str]],
    noise: NoiseModel,
    gate_sigma: float,
    f0_min: float,
    f0_max: float,
    tol_cents_f0: float,
    tol_cents_h: float
) -> KeyReduced:
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

        m2 = mag_near(peaks, 2.0 * f0, tol_cents=tol_cents_h)
        m3 = mag_near(peaks, 3.0 * f0, tol_cents=tol_cents_h)
        m4 = mag_near(peaks, 4.0 * f0, tol_cents=tol_cents_h)

        present = sum(1 for m in (m2, m3, m4) if m > 0)
        if present < 2:
            continue

        f0s.append(f0)
        if m2 > 0:
            r2.append(m2 / m1)
        if m3 > 0:
            r3.append(m3 / m1)
        if m4 > 0:
            r4.append(m4 / m1)

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

def build_reduced_model(
    log_path: Path,
    gate_sigma: float = 4.0,
    f0_min: float = 27.0,
    f0_max: float = 1200.0,
    tol_cents_f0: float = 25.0,
    tol_cents_h: float = 45.0
) -> Dict[str, object]:
    header, rows = iter_cal_win_rows(log_path)
    dict_rows = rows_to_dicts(header, rows)

    sil_rows = detect_silence_rows(dict_rows)
    if not sil_rows:
        raise RuntimeError("No SILENCE rows detected (need key_idx=-1 or key='SILENCE').")
    noise = build_noise_model(sil_rows)

    by_key: Dict[Tuple[int, str], List[Dict[str, str]]] = {}
    for d in dict_rows:
        idx_s = (d.get("key_idx", "") or "").strip()
        key = (d.get("key", "") or "").strip()
        if not idx_s or not is_number(idx_s):
            continue
        idx = int(float(idx_s))
        by_key.setdefault((idx, key), []).append(d)

    reduced: List[KeyReduced] = []
    for (idx, key), rlist in sorted(by_key.items(), key=lambda x: x[0][0]):
        if idx == -1 or key.strip().lower() in ("silence", "sil", "noise"):
            continue
        reduced.append(
            build_reduced_for_key(
                key_idx=idx,
                key=key,
                rows=rlist,
                noise=noise,
                gate_sigma=gate_sigma,
                f0_min=f0_min,
                f0_max=f0_max,
                tol_cents_f0=tol_cents_f0,
                tol_cents_h=tol_cents_h,
            )
        )

    return {
        "noise": asdict(noise),
        "params": {
            "gate_sigma": gate_sigma,
            "f0_min": f0_min,
            "f0_max": f0_max,
            "tol_cents_f0": tol_cents_f0,
            "tol_cents_h": tol_cents_h,
            "note": "Reduced model: f0 median/MAD + harmonic ratios H2/H1,H3/H1,H4/H1",
        },
        "keys": [asdict(k) for k in reduced],
    }

def write_reduced_model(out_path: Path, model: Dict[str, object]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
