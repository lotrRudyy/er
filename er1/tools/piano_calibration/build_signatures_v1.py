#!/usr/bin/env python3
import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np


# ----------------------------
# Helpers
# ----------------------------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def is_number(x) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False

def cents_diff(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0:
        return float("inf")
    return abs(1200.0 * math.log2(a_hz / b_hz))

def robust_mad(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    m = np.median(x)
    mad = np.median(np.abs(x - m))
    return float(mad * 1.4826)

def safe_float(d: Dict[str, str], k: str, default: float = float("nan")) -> float:
    v = d.get(k, "")
    return float(v) if is_number(v) else default


# ----------------------------
# Parsing your mixed serial log
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

BAND_FIELDS = [
    "band1_20_80",
    "band2_80_160",
    "band3_160_320",
    "band4_320_640",
    "band5_640_1280",
    "band6_1280_4000",
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
                    raise RuntimeError(
                        "Found CAL_WIN lines but did not find CSV header.\n"
                        "Make sure the sketch prints the header line."
                    )
                rows.append(next(csv.reader([line])))

    if header is None:
        raise RuntimeError("No CSV header found (expected 'type,t_ms,key_idx,key,').")
    if not rows:
        raise RuntimeError("No CAL_WIN rows found.")
    return header, rows

def rows_to_dicts(header: List[str], rows: List[List[str]]) -> List[Dict[str, str]]:
    out = []
    hlen = len(header)
    for r in rows:
        if len(r) < hlen:
            r = r + [""] * (hlen - len(r))
        out.append({header[i]: r[i] for i in range(hlen)})
    return out

def detect_silence_rows(dict_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    sil = []
    for d in dict_rows:
        key = d.get("key", "").strip().lower()
        idx = d.get("key_idx", "").strip()
        if key in ("silence", "sil", "noise"):
            sil.append(d)
            continue
        if idx == "-1":
            sil.append(d)
    return sil


# ----------------------------
# Noise model (used for live gating, and mild sanity checks)
# ----------------------------

@dataclass
class NoiseModel:
    rms_ac_med: float
    rms_ac_mad: float
    spec_total_med: float
    spec_total_mad: float
    flatness_med: float
    flatness_mad: float

    # extra (helps tuning/debug)
    p1mag_med: float
    p1mag_mad: float

def build_noise_model(sil_rows: List[Dict[str, str]]) -> NoiseModel:
    def col(name: str) -> np.ndarray:
        vals = []
        for d in sil_rows:
            v = d.get(name, "")
            if is_number(v):
                vals.append(float(v))
        return np.asarray(vals, dtype=np.float64)

    rms = col("rms_ac")
    tot = col("spec_total")
    flat = col("flatness")
    p1m = col("p1_mag")

    return NoiseModel(
        rms_ac_med=float(np.median(rms)) if rms.size else 0.0,
        rms_ac_mad=robust_mad(rms) if rms.size else 0.0,
        spec_total_med=float(np.median(tot)) if tot.size else 0.0,
        spec_total_mad=robust_mad(tot) if tot.size else 0.0,
        flatness_med=float(np.median(flat)) if flat.size else 0.0,
        flatness_mad=robust_mad(flat) if flat.size else 0.0,
        p1mag_med=float(np.median(p1m)) if p1m.size else 0.0,
        p1mag_mad=robust_mad(p1m) if p1m.size else 0.0,
    )


# ----------------------------
# Peak clustering (Strategy 3)
# ----------------------------

@dataclass
class PeakCluster:
    center_hz: float
    spread_hz: float
    count: int
    weight: float

def cluster_peaks_1d(freqs: np.ndarray, mags: np.ndarray, tol_cents: float, min_count: int) -> List[PeakCluster]:
    if freqs.size == 0:
        return []

    idx = np.argsort(freqs)
    freqs = freqs[idx]
    mags = mags[idx]

    clusters_idx: List[List[int]] = []
    cur: List[int] = [0]
    for i in range(1, freqs.size):
        if cents_diff(freqs[i], freqs[cur[-1]]) <= tol_cents:
            cur.append(i)
        else:
            clusters_idx.append(cur)
            cur = [i]
    clusters_idx.append(cur)

    out: List[PeakCluster] = []
    for c in clusters_idx:
        cf = freqs[c]
        cm = mags[c]
        if cf.size < min_count:
            continue
        wsum = float(np.sum(cm))
        if wsum <= 0:
            continue
        center = float(np.sum(cf * cm) / wsum)
        spread = float(np.std(cf))
        out.append(PeakCluster(center_hz=center, spread_hz=spread, count=int(cf.size), weight=wsum))

    out.sort(key=lambda x: x.weight, reverse=True)
    return out


# ----------------------------
# Signature building
#   Key idea: select "best" windows *within the key* (robust against room noise)
# ----------------------------

@dataclass
class KeySignature:
    key_idx: int
    key: str

    windows_total: int
    windows_used: int

    # Peak clusters (freq kept as feature, not label)
    peak_clusters: List[PeakCluster]

    # Normalized band shape
    band_vec_median: List[float]
    band_vec_mad: List[float]

    centroid_med: float
    bandwidth_med: float
    rolloff85_med: float
    flatness_med: float

    rms_ac_med: float
    spec_total_med: float

def window_tonality_score(d: Dict[str, str], noise: NoiseModel) -> float:
    """
    Higher is "more key-like / tonal".
    Uses:
      - peak dominance (p1_mag / (sum of p1..p8 + eps))
      - low flatness preference
      - mild penalty if flatness is very noise-like
    """
    mags = []
    for _, mname in PEAK_FIELDS:
        m = safe_float(d, mname, 0.0)
        if np.isfinite(m) and m > 0:
            mags.append(m)
    if not mags:
        return 0.0

    p1 = safe_float(d, "p1_mag", 0.0)
    s = float(np.sum(mags)) + 1e-9
    dominance = float(p1 / s)  # 0..1

    flat = safe_float(d, "flatness", float("nan"))
    if not np.isfinite(flat):
        flat = noise.flatness_med

    # prefer lower flatness (more peaky/tonal). clamp
    tonal = max(0.0, 1.0 - float(flat))

    # if flatness is extremely "noise-like", downweight hard
    if noise.flatness_mad > 0:
        bad_thr = noise.flatness_med + 6.0 * noise.flatness_mad
        if flat >= bad_thr:
            tonal *= 0.1

    return dominance * tonal

def build_key_signature(
    key_idx: int,
    key: str,
    rows: List[Dict[str, str]],
    noise: NoiseModel,
    tol_cents: float,
    min_cluster_count: int,
    keep_clusters: int,
    keep_frac: float,
) -> KeySignature:

    # Rank windows within this key by tonality score, keep the best fraction
    scores = np.array([window_tonality_score(d, noise) for d in rows], dtype=np.float64)
    n_total = len(rows)
    if n_total == 0:
        raise RuntimeError(f"No rows for key {key_idx}:{key}")

    n_keep = max(10, int(round(keep_frac * n_total)))
    keep_idx = np.argsort(scores)[-n_keep:]
    used = [rows[i] for i in keep_idx]

    def get_col(ds: List[Dict[str, str]], name: str) -> np.ndarray:
        vals = []
        for d in ds:
            v = d.get(name, "")
            if is_number(v):
                vals.append(float(v))
        return np.asarray(vals, dtype=np.float64)

    # Collect peaks from used windows
    pfreqs = []
    pmags = []
    for d in used:
        for fhz_name, fmag_name in PEAK_FIELDS:
            f = safe_float(d, fhz_name, float("nan"))
            m = safe_float(d, fmag_name, float("nan"))
            if np.isfinite(f) and np.isfinite(m) and f > 0 and m > 0:
                pfreqs.append(f)
                pmags.append(m)
    pfreqs = np.asarray(pfreqs, dtype=np.float64)
    pmags = np.asarray(pmags, dtype=np.float64)

    clusters = cluster_peaks_1d(
        freqs=pfreqs,
        mags=pmags,
        tol_cents=tol_cents,
        min_count=min_cluster_count,
    )[:keep_clusters]

    # Band shape (normalized by spec_total)
    bands = []
    for b in BAND_FIELDS:
        bands.append(get_col(used, b))
    bands = np.stack(bands, axis=1) if used else np.zeros((0, 6), dtype=np.float64)

    spec_total = get_col(used, "spec_total")
    if bands.shape[0] and spec_total.size == bands.shape[0]:
        denom = np.maximum(spec_total, 1e-9)[:, None]
        band_norm = bands / denom
    else:
        band_norm = np.zeros((0, 6), dtype=np.float64)

    band_med = np.median(band_norm, axis=0) if band_norm.shape[0] else np.zeros(6)
    band_mad = np.array([robust_mad(band_norm[:, i]) for i in range(6)]) if band_norm.shape[0] else np.zeros(6)

    centroid = get_col(used, "centroid_hz")
    bandwidth = get_col(used, "bandwidth_hz")
    rolloff85 = get_col(used, "rolloff85_hz")
    flatness = get_col(used, "flatness")
    rms_ac = get_col(used, "rms_ac")

    return KeySignature(
        key_idx=key_idx,
        key=key,
        windows_total=len(rows),
        windows_used=len(used),
        peak_clusters=clusters,
        band_vec_median=[float(x) for x in band_med.tolist()],
        band_vec_mad=[float(x) for x in band_mad.tolist()],
        centroid_med=float(np.median(centroid)) if centroid.size else 0.0,
        bandwidth_med=float(np.median(bandwidth)) if bandwidth.size else 0.0,
        rolloff85_med=float(np.median(rolloff85)) if rolloff85.size else 0.0,
        flatness_med=float(np.median(flatness)) if flatness.size else 0.0,
        rms_ac_med=float(np.median(rms_ac)) if rms_ac.size else 0.0,
        spec_total_med=float(np.median(spec_total)) if spec_total.size else 0.0,
    )


def main():
    ap = argparse.ArgumentParser(description="Build piano key signatures from CAL_WIN CSV logs (robust, per-key selection).")
    ap.add_argument("--log", required=True, help="Path to piano_calibration.log (serial capture)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--tol-cents", type=float, default=30.0, help="Peak clustering tolerance in cents (default 30)")
    ap.add_argument("--min-cluster-count", type=int, default=10, help="Min observations for a peak cluster (default 10)")
    ap.add_argument("--keep-clusters", type=int, default=3, help="Keep top N peak clusters per key (default 3)")
    ap.add_argument("--keep-frac", type=float, default=0.35, help="Keep best fraction of windows within each key (default 0.35)")
    args = ap.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    ensure_dir(out_dir)

    header, rows = iter_cal_win_rows(log_path)
    dict_rows = rows_to_dicts(header, rows)

    # Group by key_idx/key
    by_key: Dict[Tuple[int, str], List[Dict[str, str]]] = {}
    for d in dict_rows:
        idx_s = d.get("key_idx", "").strip()
        key = d.get("key", "").strip()
        if not idx_s or not is_number(idx_s):
            continue
        idx = int(float(idx_s))
        by_key.setdefault((idx, key), []).append(d)

    # Noise model (from SILENCE)
    sil_rows = detect_silence_rows(dict_rows)
    if not sil_rows:
        raise RuntimeError(
            "No SILENCE rows detected.\n"
            "Your sketch must log ~10s of silence as key=SILENCE/SIL/NOISE or key_idx=-1."
        )
    noise = build_noise_model(sil_rows)
    (out_dir / "noise_model.json").write_text(json.dumps(asdict(noise), indent=2), encoding="utf-8")

    # Signatures
    signatures: List[KeySignature] = []
    for (idx, key), rlist in sorted(by_key.items(), key=lambda x: x[0][0]):
        k_lower = key.strip().lower()
        if idx == -1 or k_lower in ("silence", "sil", "noise"):
            continue
        sig = build_key_signature(
            key_idx=idx,
            key=key,
            rows=rlist,
            noise=noise,
            tol_cents=args.tol_cents,
            min_cluster_count=args.min_cluster_count,
            keep_clusters=args.keep_clusters,
            keep_frac=args.keep_frac,
        )
        signatures.append(sig)

    # Write signatures.json (full)
    def sig_to_dict(s: KeySignature) -> dict:
        d = asdict(s)
        d["peak_clusters"] = [asdict(c) for c in s.peak_clusters]
        return d

    (out_dir / "signatures.json").write_text(
        json.dumps([sig_to_dict(s) for s in signatures], indent=2),
        encoding="utf-8"
    )

    print("OK")
    print(f"Noise model:       {out_dir / 'noise_model.json'}")
    print(f"Signatures (full): {out_dir / 'signatures.json'}")

if __name__ == "__main__":
    main()
