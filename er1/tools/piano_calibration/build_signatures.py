#!/usr/bin/env python3
import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import median
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

def hz_to_cents_from_1hz(f_hz: float) -> float:
    # cents = 1200*log2(f/1Hz)
    if f_hz <= 0:
        return float("nan")
    return 1200.0 * math.log2(f_hz)

def cents_to_hz_from_1hz(cents: float) -> float:
    return 2.0 ** (cents / 1200.0)

def cents_diff_from_1hz(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0:
        return float("inf")
    return abs(hz_to_cents_from_1hz(a_hz) - hz_to_cents_from_1hz(b_hz))

def robust_mad(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    m = np.median(x)
    mad = np.median(np.abs(x - m))
    return float(mad * 1.4826)

def safe_median(vals: List[float], default: float = 0.0) -> float:
    vals = [v for v in vals if np.isfinite(v)]
    return float(median(vals)) if vals else default

def q15(x: float) -> int:
    # clamp to [0, 1.0] typical for normalized bands
    x = 0.0 if not math.isfinite(x) else x
    x = max(0.0, min(1.0, x))
    return int(round(x * 32767.0))

def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


# ----------------------------
# Clustering peaks (fixed)
# ----------------------------

@dataclass
class PeakCluster:
    center_hz: float
    spread_hz: float
    count: int
    weight: float

@dataclass
class PeakClusterCompact:
    # log2-scaled frequency to cents-from-1Hz (int)
    center_cents: int
    spread_cents: int
    count: int
    weight_q15: int  # relative weight within key [0..32767]

def cluster_peaks_cents_centered(
    freqs_hz: np.ndarray,
    mags: np.ndarray,
    tol_cents: float = 30.0,
    min_count: int = 8
) -> List[PeakCluster]:
    """
    Stable clustering in cents-space, comparing each point to the current cluster center
    (not the previous point). This avoids "chaining" across a wide range.
    """
    if freqs_hz.size == 0:
        return []

    # convert to cents-from-1Hz (monotonic with f)
    cents = np.array([hz_to_cents_from_1hz(f) for f in freqs_hz], dtype=np.float64)
    valid = np.isfinite(cents) & np.isfinite(mags) & (mags > 0)
    cents = cents[valid]
    mags = mags[valid]
    if cents.size == 0:
        return []

    # sort by cents
    idx = np.argsort(cents)
    cents = cents[idx]
    mags = mags[idx]

    clusters: List[List[int]] = []
    current = [0]
    # running weighted center in cents
    cur_wsum = float(mags[0])
    cur_center = float(cents[0])

    for i in range(1, cents.size):
        # compare to current cluster center
        if abs(cents[i] - cur_center) <= tol_cents:
            current.append(i)
            cur_wsum += float(mags[i])
            # update center (weighted mean in cents)
            cur_center = float((cur_center * (cur_wsum - mags[i]) + cents[i] * mags[i]) / cur_wsum)
        else:
            clusters.append(current)
            current = [i]
            cur_wsum = float(mags[i])
            cur_center = float(cents[i])
    clusters.append(current)

    out: List[PeakCluster] = []
    for cidx in clusters:
        cc = cents[cidx]
        cm = mags[cidx]
        if cc.size < min_count:
            continue
        wsum = float(np.sum(cm))
        if wsum <= 0:
            continue
        center_c = float(np.sum(cc * cm) / wsum)
        spread_c = float(np.std(cc))  # spread in cents
        center_hz = cents_to_hz_from_1hz(center_c)
        spread_hz = abs(cents_to_hz_from_1hz(center_c + spread_c) - center_hz)
        out.append(PeakCluster(
            center_hz=float(center_hz),
            spread_hz=float(spread_hz),
            count=int(cc.size),
            weight=wsum
        ))

    out.sort(key=lambda x: x.weight, reverse=True)
    return out


# ----------------------------
# Parsing your log
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
                    raise RuntimeError("Found CAL_WIN but no CSV header (type,t_ms,key_idx,key,...)")
                rows.append(next(csv.reader([line])))
    if header is None:
        raise RuntimeError("No CSV header found.")
    if not rows:
        raise RuntimeError("No CAL_WIN rows found.")
    return header, rows

def rows_to_dicts(header: List[str], rows: List[List[str]]) -> List[Dict[str, str]]:
    out = []
    hlen = len(header)
    for r in rows:
        if len(r) < hlen:
            r = r + [""] * (hlen - len(r))
        d = {header[i]: r[i] for i in range(hlen)}
        out.append(d)
    return out


# ----------------------------
# Models
# ----------------------------

@dataclass
class NoiseModel:
    rms_ac_med: float
    rms_ac_mad: float
    spec_total_med: float
    spec_total_mad: float
    flatness_med: float
    flatness_mad: float

@dataclass
class KeySignature:
    key_idx: int
    key: str
    windows_total: int
    windows_used: int

    peak_clusters: List[PeakCluster]

    band_vec_median: List[float]   # 6
    band_vec_mad: List[float]      # 6

    centroid_med: float
    rolloff85_med: float
    flatness_med: float

    rms_ac_med: float
    spec_total_med: float


def detect_silence_rows(dict_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    sil = []
    for d in dict_rows:
        key = d.get("key", "").strip().lower()
        key_idx = d.get("key_idx", "").strip()
        if key in ("silence", "sil", "noise") or key_idx == "-1":
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

    rms = col("rms_ac")
    tot = col("spec_total")
    flat = col("flatness")

    return NoiseModel(
        rms_ac_med=float(np.median(rms)) if rms.size else 0.0,
        rms_ac_mad=robust_mad(rms) if rms.size else 0.0,
        spec_total_med=float(np.median(tot)) if tot.size else 0.0,
        spec_total_mad=robust_mad(tot) if tot.size else 0.0,
        flatness_med=float(np.median(flat)) if flat.size else 0.0,
        flatness_mad=robust_mad(flat) if flat.size else 0.0,
    )

def passes_gate(d: Dict[str, str], noise: NoiseModel, gate_sigma: float) -> bool:
    def getf(name: str) -> float:
        v = d.get(name, "")
        return float(v) if is_number(v) else float("nan")

    rms = getf("rms_ac")
    tot = getf("spec_total")
    flat = getf("flatness")

    rms_thr = noise.rms_ac_med + gate_sigma * max(noise.rms_ac_mad, 1e-9)
    tot_thr = noise.spec_total_med + gate_sigma * max(noise.spec_total_mad, 1e-9)

    ok = True
    if np.isfinite(rms):
        ok &= (rms > rms_thr)
    if np.isfinite(tot):
        ok &= (tot > tot_thr)

    # reject very-noisy broadband windows (too flat)
    if np.isfinite(flat) and noise.flatness_mad > 0:
        flat_thr = noise.flatness_med + 6.0 * noise.flatness_mad
        ok &= (flat < flat_thr)

    return bool(ok)

def build_key_signature(
    key_idx: int,
    key: str,
    rows: List[Dict[str, str]],
    noise: NoiseModel,
    gate_sigma: float,
    tol_cents: float,
    min_cluster_count: int,
    keep_clusters: int
) -> KeySignature:

    used = [d for d in rows if passes_gate(d, noise, gate_sigma)]

    def get_col(ds: List[Dict[str, str]], name: str) -> np.ndarray:
        vals = []
        for d in ds:
            v = d.get(name, "")
            if is_number(v):
                vals.append(float(v))
        return np.asarray(vals, dtype=np.float64)

    # Collect peaks across used windows
    pfreqs = []
    pmags = []
    for d in used:
        for fhz_name, fmag_name in PEAK_FIELDS:
            fhz = d.get(fhz_name, "")
            fmag = d.get(fmag_name, "")
            if is_number(fhz) and is_number(fmag):
                f = float(fhz)
                m = float(fmag)
                if f > 0 and m > 0:
                    pfreqs.append(f)
                    pmags.append(m)
    pfreqs = np.asarray(pfreqs, dtype=np.float64)
    pmags = np.asarray(pmags, dtype=np.float64)

    clusters = cluster_peaks_cents_centered(
        freqs_hz=pfreqs,
        mags=pmags,
        tol_cents=tol_cents,
        min_count=min_cluster_count
    )[:keep_clusters]

    # Band shape: normalize by sum(bands) (more stable than spec_total)
    bands = []
    for b in BAND_FIELDS:
        bands.append(get_col(used, b))
    bands = np.stack(bands, axis=1) if used else np.zeros((0, 6), dtype=np.float64)

    if bands.shape[0]:
        denom = np.maximum(np.sum(bands, axis=1), 1e-9)[:, None]
        band_norm = bands / denom
    else:
        band_norm = np.zeros((0, 6), dtype=np.float64)

    band_med = np.median(band_norm, axis=0) if band_norm.shape[0] else np.zeros(6)
    band_mad = np.array([robust_mad(band_norm[:, i]) for i in range(6)]) if band_norm.shape[0] else np.zeros(6)

    centroid = get_col(used, "centroid_hz")
    rolloff85 = get_col(used, "rolloff85_hz")
    flatness = get_col(used, "flatness")
    rms_ac = get_col(used, "rms_ac")
    spec_total = get_col(used, "spec_total")

    return KeySignature(
        key_idx=key_idx,
        key=key,
        windows_total=len(rows),
        windows_used=len(used),
        peak_clusters=clusters,
        band_vec_median=[float(x) for x in band_med.tolist()],
        band_vec_mad=[float(x) for x in band_mad.tolist()],
        centroid_med=float(np.median(centroid)) if centroid.size else 0.0,
        rolloff85_med=float(np.median(rolloff85)) if rolloff85.size else 0.0,
        flatness_med=float(np.median(flatness)) if flatness.size else 0.0,
        rms_ac_med=float(np.median(rms_ac)) if rms_ac.size else 0.0,
        spec_total_med=float(np.median(spec_total)) if spec_total.size else 0.0,
    )


# ----------------------------
# ESP export
# ----------------------------

def compact_clusters(clusters: List[PeakCluster]) -> List[PeakClusterCompact]:
    if not clusters:
        return []
    wsum = sum(c.weight for c in clusters)
    wsum = wsum if wsum > 1e-9 else 1.0

    out: List[PeakClusterCompact] = []
    for c in clusters:
        center_c = int(round(hz_to_cents_from_1hz(c.center_hz)))
        # spread in cents around center (approx)
        spread_c = int(round(abs(hz_to_cents_from_1hz(c.center_hz + c.spread_hz) - hz_to_cents_from_1hz(c.center_hz))))
        out.append(PeakClusterCompact(
            center_cents=clamp_int(center_c, -200000, 200000),
            spread_cents=clamp_int(spread_c, 0, 6000),
            count=c.count,
            weight_q15=clamp_int(int(round((c.weight / wsum) * 32767.0)), 0, 32767)
        ))
    return out

def write_esp_header(out_dir: Path, noise: NoiseModel, sigs: List[KeySignature], keep_clusters: int) -> None:
    # Compact per-key: 3 clusters, 6 band medians (Q15)
    lines = []
    lines.append("// Auto-generated by build_signatures.py")
    lines.append("#pragma once")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define ER_NUM_BANDS 6")
    lines.append(f"#define ER_MAX_CLUSTERS {keep_clusters}")
    lines.append("")
    lines.append("typedef struct {")
    lines.append("  int32_t center_cents;")  # cents-from-1Hz
    lines.append("  uint16_t spread_cents;")
    lines.append("  uint16_t count;")
    lines.append("  uint16_t weight_q15;")
    lines.append("} er_peak_cluster_t;")
    lines.append("")
    lines.append("typedef struct {")
    lines.append("  int16_t key_idx;")
    lines.append("  uint16_t band_med_q15[ER_NUM_BANDS];")
    lines.append("  er_peak_cluster_t clusters[ER_MAX_CLUSTERS];")
    lines.append("} er_key_model_t;")
    lines.append("")
    lines.append("typedef struct {")
    lines.append("  float rms_med, rms_mad;")
    lines.append("  float tot_med, tot_mad;")
    lines.append("  float flat_med, flat_mad;")
    lines.append("} er_noise_model_t;")
    lines.append("")
    lines.append(f"static const er_noise_model_t ER_NOISE = " +
                 f"{{{noise.rms_ac_med:.6f}f,{noise.rms_ac_mad:.6f}f,{noise.spec_total_med:.6f}f,{noise.spec_total_mad:.6f}f,{noise.flatness_med:.6f}f,{noise.flatness_mad:.6f}f}};")
    lines.append("")

    lines.append(f"static const er_key_model_t ER_KEYS[{len(sigs)}] = {{")
    for s in sigs:
        cc = compact_clusters(s.peak_clusters)
        # pad clusters
        while len(cc) < keep_clusters:
            cc.append(PeakClusterCompact(0, 0, 0, 0))

        band_q15 = [q15(x) for x in s.band_vec_median]
        lines.append("  {")
        lines.append(f"    .key_idx = {s.key_idx},")
        lines.append("    .band_med_q15 = {" + ",".join(str(x) for x in band_q15) + "},")
        lines.append("    .clusters = {")
        for c in cc[:keep_clusters]:
            lines.append(f"      {{ .center_cents={c.center_cents}, .spread_cents={c.spread_cents}, .count={c.count}, .weight_q15={c.weight_q15} }},")
        lines.append("    }")
        lines.append("  },")
    lines.append("};")
    lines.append("")

    (out_dir / "model_esp.h").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Build piano key signatures from CAL_WIN CSV logs (robust + ESP export).")
    ap.add_argument("--log", required=True, help="Path to piano_calibration.log (serial capture)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--gate-sigma", type=float, default=4.0, help="Gate threshold in MAD sigmas above silence (default 4.0)")
    ap.add_argument("--tol-cents", type=float, default=30.0, help="Peak clustering tolerance in cents (default 30)")
    ap.add_argument("--min-cluster-count", type=int, default=12, help="Min observations for a peak cluster (default 12)")
    ap.add_argument("--keep-clusters", type=int, default=3, help="Keep top N peak clusters per key (default 3)")
    args = ap.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    ensure_dir(out_dir)

    header, rows = iter_cal_win_rows(log_path)
    dict_rows = rows_to_dicts(header, rows)

    # Group by key_idx/key
    by_key: Dict[Tuple[int, str], List[Dict[str, str]]] = {}
    for d in dict_rows:
        k = d.get("key", "").strip()
        idx_s = d.get("key_idx", "").strip()
        if not idx_s or not is_number(idx_s):
            continue
        idx = int(float(idx_s))
        by_key.setdefault((idx, k), []).append(d)

    # Noise model (requires SILENCE rows)
    sil_rows = detect_silence_rows(dict_rows)
    if not sil_rows:
        raise RuntimeError(
            "No SILENCE rows detected.\n"
            "Your sketch must log 10s of silence as key='SILENCE'/'SIL'/'NOISE' or key_idx=-1."
        )
    noise = build_noise_model(sil_rows)

    # Build signatures
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
            gate_sigma=args.gate_sigma,
            tol_cents=args.tol_cents,
            min_cluster_count=args.min_cluster_count,
            keep_clusters=args.keep_clusters
        )
        signatures.append(sig)

    # Full JSON (debug-friendly)
    (out_dir / "noise_model.json").write_text(json.dumps(asdict(noise), indent=2), encoding="utf-8")
    (out_dir / "signatures.json").write_text(json.dumps([asdict(s) for s in signatures], indent=2), encoding="utf-8")

    # Compact JSON (ESP-oriented)
    compact = {
        "noise": asdict(noise),
        "keys": [
            {
                "key_idx": s.key_idx,
                "clusters": [asdict(c) for c in compact_clusters(s.peak_clusters)],
                "band_med_q15": [q15(x) for x in s.band_vec_median],
            }
            for s in signatures
        ]
    }
    (out_dir / "model_compact.json").write_text(json.dumps(compact, indent=2), encoding="utf-8")

    # C header for ESP
    write_esp_header(out_dir, noise, signatures, args.keep_clusters)

    print("OK")
    print(f"Noise model:        {out_dir / 'noise_model.json'}")
    print(f"Signatures (full):  {out_dir / 'signatures.json'}")
    print(f"Compact model:      {out_dir / 'model_compact.json'}")
    print(f"ESP header:         {out_dir / 'model_esp.h'}")


if __name__ == "__main__":
    main()
