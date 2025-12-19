#!/usr/bin/env python3
"""
freq_delta_viz.py

Improved frequency visualization for "pre vs post onset" + delta spectrum,
with harmonic-ladder-friendly views.

Exports:
- PNG plots + index.html
- Per-rep data exports (npz/csv/segments) for deeper analysis/debugging.

Onset source priority:
  1) aligned_meta.json -> aligned.onset_sample (QC spectral-flux onset)
  2) fallback RMS onset around enter_sample computed from meta.json

Important:
- qc_captures.py commonly writes aligned exports with pre_align_ms default 0,
  so aligned_i16.raw often contains NO pre-onset context.
- Therefore this script uses raw_audio_i16.raw for pre/post windows (and uses
  aligned_meta.json only for onset_sample).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt

REP_DIR_RE = re.compile(r"^rep\d{3}_\d{8}-\d{6}$", re.IGNORECASE)
KEY_RE = re.compile(r"^([a-g])(#?)([0-7])$", re.IGNORECASE)

NOTE_TO_SEMITONE = {
    "c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11
}


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def read_i16_raw(p: Path) -> np.ndarray:
    return np.fromfile(str(p), dtype=np.int16)


def to_float(x_i16: np.ndarray) -> np.ndarray:
    return x_i16.astype(np.float32) / 32768.0


def find_rep_dirs(root: Path) -> List[Path]:
    reps: List[Path] = []
    if not root.exists():
        return reps
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep in sorted([p for p in lbl_dir.iterdir() if p.is_dir()]):
            if REP_DIR_RE.match(rep.name):
                reps.append(rep)
    return reps


def pick_reps(root: Path, labels: Optional[List[str]], n_per_label: int, rep_glob: Optional[str]) -> List[Path]:
    reps = find_rep_dirs(root)
    if labels:
        lblset = {s.lower() for s in labels}
        reps = [r for r in reps if r.parent.name.lower() in lblset]
    if rep_glob:
        reps = [r for r in reps if r.match(rep_glob) or r.name == rep_glob]

    if n_per_label <= 0:
        return reps

    out: List[Path] = []
    by: Dict[str, List[Path]] = {}
    for r in reps:
        by.setdefault(r.parent.name.lower(), []).append(r)
    for _, lst in by.items():
        out.extend(lst[:n_per_label])
    return out


def detect_onset_rms(x: np.ndarray, fs: int, enter_sample: int, hop: int = 256) -> Optional[int]:
    """
    Fallback onset: find first region where RMS rises above baseline + 6*sigma.
    """
    n = x.size
    if n < hop * 8:
        return None
    win = hop * 4

    start = max(0, enter_sample - int(round(fs * 0.08)))
    stop = min(n, enter_sample + int(round(fs * 0.2)))

    b1 = max(0, min(n, enter_sample))
    b0 = max(0, b1 - int(round(fs * 0.06)))

    def frame_rms(s: int) -> float:
        seg = x[s:s + win]
        if seg.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(seg * seg)))

    base = []
    for s in range(b0, max(b0, b1 - win) + 1, hop):
        base.append(frame_rms(s))
    if len(base) < 4:
        return None
    mu = float(np.mean(base))
    sd = float(np.std(base))
    thr = mu + 6.0 * sd if sd > 1e-9 else mu * 10.0 + 1e-9

    hit = 0
    for s in range(start, max(start, stop - win) + 1, hop):
        if frame_rms(s) > thr:
            hit += 1
            if hit >= 2:
                return int(max(0, s - hop))
        else:
            hit = 0
    return None


def hann_rfft_db(seg: np.ndarray, fs: int, nfft: int, eps: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    Single-window Hann * rFFT => magnitude => dB-like scale.
    """
    seg = seg.astype(np.float32, copy=False)
    if seg.size < nfft:
        seg = np.pad(seg, (0, nfft - seg.size), mode="constant")
    else:
        seg = seg[:nfft]

    w = np.hanning(seg.size).astype(np.float32)
    spec = np.fft.rfft(seg * w, n=nfft)
    mag = np.abs(spec).astype(np.float32)

    freqs = np.fft.rfftfreq(nfft, d=1.0 / float(fs))
    db = 20.0 * np.log10(eps + mag)
    return freqs, db


def select_band(freqs: np.ndarray, v: np.ndarray, fmin: float, fmax: float) -> Tuple[np.ndarray, np.ndarray]:
    m = (freqs >= fmin) & (freqs <= fmax)
    return freqs[m], v[m]


def smooth_ma(v: np.ndarray, k: int) -> np.ndarray:
    """
    Simple moving average smoothing (for visualization).
    k<=1 disables. Uses edge padding.
    """
    k = int(k)
    if k <= 1:
        return v.astype(np.float32, copy=False)
    if k % 2 == 0:
        k += 1
    pad = k // 2
    vp = np.pad(v, (pad, pad), mode="edge")
    kernel = np.ones((k,), dtype=np.float32) / float(k)
    return np.convolve(vp, kernel, mode="valid").astype(np.float32)


def label_to_f0_hz(label: str, a4_hz: float = 440.0) -> Optional[float]:
    """
    Convert labels like c4, f#3, a0 into frequency (equal temperament, A4=440).
    Assumes octave number follows scientific pitch notation where C4 is MIDI 60.
    """
    m = KEY_RE.match(label.strip().lower())
    if not m:
        return None
    note = m.group(1).lower()
    sharp = (m.group(2) == "#")
    octave = int(m.group(3))

    semi = NOTE_TO_SEMITONE[note] + (1 if sharp else 0)
    midi = (octave + 1) * 12 + semi  # C4=60
    f0 = float(a4_hz * (2.0 ** ((midi - 69) / 12.0)))
    return f0


def sample_at_harmonics(freqs: np.ndarray, v: np.ndarray, f0: float, fmax: float, max_harmonics: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Sample v at frequencies k*f0 using nearest-bin lookup.
    Returns (ks, vals).
    """
    ks = []
    vals = []
    for k in range(1, max_harmonics + 1):
        fk = k * f0
        if fk > fmax:
            break
        idx = int(np.argmin(np.abs(freqs - fk)))
        ks.append(k)
        vals.append(float(v[idx]))
    if not ks:
        return np.zeros((0,), dtype=np.int32), np.zeros((0,), dtype=np.float32)
    return np.asarray(ks, dtype=np.int32), np.asarray(vals, dtype=np.float32)


def write_csv(path: Path, freqs: np.ndarray, pre_db: np.ndarray, post_db: np.ndarray, delta_db: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "freq_hz,pre_db,post_db,delta_db\n"
    with path.open("w", encoding="utf-8") as f:
        f.write(header)
        for i in range(freqs.size):
            f.write(f"{float(freqs[i]):.6f},{float(pre_db[i]):.6f},{float(post_db[i]):.6f},{float(delta_db[i]):.6f}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--labels", default="", help="comma-separated labels (optional)")
    ap.add_argument("--n_per_label", type=int, default=3)
    ap.add_argument("--rep_glob", default=None, help="optional rep folder glob (e.g. 'rep005_*')")

    ap.add_argument("--pre_ms", type=int, default=200)
    ap.add_argument("--post_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--eps", type=float, default=1e-7)
    ap.add_argument("--fmin", type=float, default=50.0)
    ap.add_argument("--fmax", type=float, default=5000.0)

    # visualization improvements
    ap.add_argument("--log_x", action="store_true", help="use log frequency axis (recommended)")
    ap.add_argument("--no_log_x", action="store_true", help="disable log axis")
    ap.add_argument("--smooth_bins", type=int, default=9, help="moving-average smoothing bins for plots (viz only)")
    ap.add_argument("--harmonics", action="store_true", help="overlay harmonic guide lines based on label f0")
    ap.add_argument("--max_harmonics", type=int, default=30, help="max harmonics for overlay / ladder plot")
    ap.add_argument("--a4_hz", type=float, default=440.0, help="tuning reference for harmonic overlays")

    # exports
    ap.add_argument("--export_npz", action="store_true", help="write spectra.npz + meta.json (default ON)")
    ap.add_argument("--no_export_npz", action="store_true", help="disable npz export")
    ap.add_argument("--export_csv", dest="export_csv", action="store_false", help="write spectra.csv")
    ap.set_defaults(export_csv=True)
    ap.add_argument("--export_segments", action="store_true", help="write segments_i16.npz (pre/post int16)")

    ap.add_argument("--out_dir", default="viz_freq_delta")
    args = ap.parse_args()

    # defaults
    use_log_x = True
    if args.no_log_x:
        use_log_x = False
    if args.log_x:
        use_log_x = True

    export_npz = True
    if args.no_export_npz:
        export_npz = False
    if args.export_npz:
        export_npz = True

    root = Path(args.captures_root)
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    labels = [s.strip().lower() for s in args.labels.split(",") if s.strip()] or None
    reps = pick_reps(root, labels, args.n_per_label, args.rep_glob)

    rows: List[str] = ["<html><body><h1>freq_delta_viz</h1>"]

    for rep_dir in reps:
        meta_path = rep_dir / "meta.json"
        raw_path = rep_dir / "raw_audio_i16.raw"
        if not meta_path.exists() or not raw_path.exists():
            continue

        meta = load_json(meta_path)
        fs = int(meta.get("fs", 48000))
        pre_ms_fw = int(meta.get("pre_ms", 0))
        lead_ms_fw = int(meta.get("lead_ms", 0))
        enter_sample = int(meta.get("enter_sample", int(round(fs * (pre_ms_fw + lead_ms_fw) / 1000.0))))

        raw_i16 = read_i16_raw(raw_path)
        raw = to_float(raw_i16)

        # onset from QC if available
        onset = None
        am_path = rep_dir / "aligned_meta.json"
        onset_source = "none"
        if am_path.exists():
            am = load_json(am_path)
            onset = am.get("aligned", {}).get("onset_sample", None)
            if isinstance(onset, (float, int)):
                onset = int(onset)
                onset_source = "aligned_meta.json"
            else:
                onset = None

        if onset is None:
            onset = detect_onset_rms(raw, fs=fs, enter_sample=enter_sample, hop=256)
            if onset is not None:
                onset_source = "rms_fallback"

        if onset is None:
            continue

        pre_len = int(round(fs * (args.pre_ms / 1000.0)))
        post_len = int(round(fs * (args.post_ms / 1000.0)))

        # pre window ends at onset
        p0 = max(0, onset - pre_len)
        p1 = max(0, onset)
        pre_seg = raw[p0:p1]
        if pre_seg.size < pre_len:
            pre_seg = np.pad(pre_seg, (pre_len - pre_seg.size, 0), mode="constant")

        # post window starts at onset
        q0 = onset
        q1 = min(raw.size, onset + post_len)
        post_seg = raw[q0:q1]
        if post_seg.size < post_len:
            post_seg = np.pad(post_seg, (0, post_len - post_seg.size), mode="constant")

        freqs, pre_db_raw = hann_rfft_db(pre_seg, fs=fs, nfft=args.fft_n, eps=args.eps)
        _, post_db_raw = hann_rfft_db(post_seg, fs=fs, nfft=args.fft_n, eps=args.eps)
        delta_db_raw = (post_db_raw - pre_db_raw).astype(np.float32)

        freqs_b, pre_db_b_raw = select_band(freqs, pre_db_raw, args.fmin, args.fmax)
        _, post_db_b_raw = select_band(freqs, post_db_raw, args.fmin, args.fmax)
        _, delta_db_b_raw = select_band(freqs, delta_db_raw, args.fmin, args.fmax)

        # viz-only smoothing (but we export both raw and smoothed)
        pre_db_b = pre_db_b_raw
        post_db_b = post_db_b_raw
        delta_db_b = delta_db_b_raw
        if args.smooth_bins and args.smooth_bins > 1:
            pre_db_b = smooth_ma(pre_db_b_raw, args.smooth_bins)
            post_db_b = smooth_ma(post_db_b_raw, args.smooth_bins)
            delta_db_b = smooth_ma(delta_db_b_raw, args.smooth_bins)

        lbl = rep_dir.parent.name.lower()
        repname = rep_dir.name
        out_dir = out_root / lbl
        out_dir.mkdir(parents=True, exist_ok=True)

        out_png = out_dir / f"{repname}_delta_ladder.png"
        data_dir = out_dir / f"{repname}__data"
        data_dir.mkdir(parents=True, exist_ok=True)

        f0 = label_to_f0_hz(lbl, a4_hz=args.a4_hz) if args.harmonics else None

        # ---------- plotting ----------
        fig, axs = plt.subplots(4, 1, figsize=(13, 13), sharex=False)

        axs[0].plot(freqs_b, pre_db_b)
        axs[0].set_title(f"{lbl}/{repname} — PRE logmag (dB-like)  [-{args.pre_ms}ms..0]")
        axs[0].set_ylabel("dB")
        axs[0].grid(True, alpha=0.3)

        axs[1].plot(freqs_b, post_db_b)
        axs[1].set_title(f"POST logmag (dB-like)  [0..+{args.post_ms}ms]")
        axs[1].set_ylabel("dB")
        axs[1].grid(True, alpha=0.3)

        axs[2].plot(freqs_b, delta_db_b)
        axs[2].set_title("DELTA = POST - PRE (dB difference)  — harmonics should pop out")
        axs[2].set_ylabel("Δ dB")
        axs[2].grid(True, alpha=0.3)

        harm_k = np.zeros((0,), dtype=np.int32)
        harm_delta = np.zeros((0,), dtype=np.float32)

        if f0 is not None and f0 > 0:
            max_k = min(args.max_harmonics, int(args.fmax // f0))
            for k in range(1, max_k + 1):
                fk = k * f0
                for ax in (axs[0], axs[1], axs[2]):
                    ax.axvline(fk, alpha=0.15)

            axs[2].text(
                0.01, 0.92,
                f"f0≈{f0:.2f} Hz (A4={args.a4_hz})",
                transform=axs[2].transAxes,
                fontsize=10,
                verticalalignment="top",
            )

            harm_k, harm_delta = sample_at_harmonics(
                freqs_b, delta_db_b, f0=f0, fmax=args.fmax, max_harmonics=args.max_harmonics
            )

        for ax in (axs[0], axs[1], axs[2]):
            if use_log_x:
                ax.set_xscale("log")
            ax.set_xlim(args.fmin, args.fmax)
        axs[2].set_xlabel("Hz")

        axs[3].set_title("Harmonic ladder view: Δ(dB) sampled at k·f0 vs harmonic number k")
        axs[3].set_xlabel("harmonic k")
        axs[3].set_ylabel("Δ dB at k·f0")
        axs[3].grid(True, alpha=0.3)

        if f0 is None or f0 <= 0:
            axs[3].text(
                0.5, 0.5,
                "Enable --harmonics and use key labels like c4, f#3",
                transform=axs[3].transAxes, ha="center", va="center"
            )
        else:
            if harm_k.size == 0:
                axs[3].text(
                    0.5, 0.5,
                    "No harmonics in band / sampling failed",
                    transform=axs[3].transAxes, ha="center", va="center"
                )
            else:
                axs[3].plot(harm_k, harm_delta, marker="o")
                axs[3].set_xlim(1, int(harm_k[-1]))
                if float(np.max(harm_delta) - np.min(harm_delta)) < 5.0:
                    mid = float(np.mean(harm_delta))
                    axs[3].set_ylim(mid - 5.0, mid + 5.0)

        fig.tight_layout()
        fig.savefig(out_png, dpi=170)
        plt.close(fig)

        # ---------- exports ----------
        meta_out = {
            "label": lbl,
            "rep": repname,
            "fs": fs,
            "onset_sample": int(onset),
            "onset_source": onset_source,
            "enter_sample": int(enter_sample),
            "pre_ms": int(args.pre_ms),
            "post_ms": int(args.post_ms),
            "fft_n": int(args.fft_n),
            "eps": float(args.eps),
            "fmin": float(args.fmin),
            "fmax": float(args.fmax),
            "smooth_bins": int(args.smooth_bins),
            "log_x": bool(use_log_x),
            "harmonics_enabled": bool(args.harmonics),
            "a4_hz": float(args.a4_hz),
            "f0_hz": float(f0) if f0 is not None else None,
            "segments": {
                "pre_slice_raw": [int(p0), int(p1)],
                "post_slice_raw": [int(q0), int(q1)],
                "pre_padded_len": int(pre_seg.size),
                "post_padded_len": int(post_seg.size),
            },
        }
        (data_dir / "meta.json").write_text(json.dumps(meta_out, indent=2), encoding="utf-8")

        if export_npz:
            np.savez_compressed(
                data_dir / "spectra.npz",
                freqs_hz=freqs_b.astype(np.float32),
                pre_db=pre_db_b.astype(np.float32),
                post_db=post_db_b.astype(np.float32),
                delta_db=delta_db_b.astype(np.float32),
                pre_db_raw=pre_db_b_raw.astype(np.float32),
                post_db_raw=post_db_b_raw.astype(np.float32),
                delta_db_raw=delta_db_b_raw.astype(np.float32),
                harm_k=harm_k.astype(np.int32),
                harm_delta_db=harm_delta.astype(np.float32),
            )

        if args.export_csv:
            write_csv(
                data_dir / "spectra.csv",
                freqs=freqs_b,
                pre_db=pre_db_b,
                post_db=post_db_b,
                delta_db=delta_db_b,
            )

        if args.export_segments:
            # store int16 segments for exact reproduction
            pre_i16 = np.clip(pre_seg * 32768.0, -32768, 32767).astype(np.int16)
            post_i16 = np.clip(post_seg * 32768.0, -32768, 32767).astype(np.int16)
            np.savez_compressed(
                data_dir / "segments_i16.npz",
                pre_i16=pre_i16,
                post_i16=post_i16,
                fs=np.int32(fs),
                onset_sample=np.int32(onset),
                enter_sample=np.int32(enter_sample),
            )

        # html index
        rel_png = out_png.relative_to(out_root).as_posix()
        rel_data = data_dir.relative_to(out_root).as_posix()
        rows.append(
            f"<div><h3>{lbl}/{repname}</h3>"
            f"<img src='{rel_png}' style='max-width:1300px; width:100%;'/>"
            f"<p>Data: <code>{rel_data}</code> (meta.json, spectra.npz"
            f"{', spectra.csv' if args.export_csv else ''}"
            f"{', segments_i16.npz' if args.export_segments else ''})</p>"
            f"</div>"
        )

    rows.append("</body></html>")
    (out_root / "index.html").write_text("\n".join(rows), encoding="utf-8")
    print(f"Wrote plots + data to {out_root} (open index.html)")


if __name__ == "__main__":
    main()
