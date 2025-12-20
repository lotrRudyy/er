#!/usr/bin/env python3
"""
select_best_captures.py

Select the best captures per label and copy/move them to a new root.

Assumes dataset layout:
  captures/<label>/repNNN_YYYYMMDD-HHMMSS/
    meta.json
    raw_audio_i16.raw
    (optional) aligned_meta.json
    (optional) aligned_i16.raw

Selection intent:
- Prefer captures with:
  - no clipping
  - low pre RMS
  - high post RMS
  - high SNR proxy = 20*log10(rms_post / (rms_pre + eps))
  - (optional) onset available (aligned_meta preferred)

Default action is COPY (safe). Use --move to move.

Outputs:
  <out_root>/selection_summary.csv
  <out_root>/<label>/rep...  (selected reps)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class RepScore:
    label: str
    rep_dir: Path
    fs: int
    onset_sample: int
    rms_pre: float
    rms_post: float
    peak: float
    clipped: int
    snr_db: float
    note: str  # diagnostics


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def read_i16_raw(p: Path) -> np.ndarray:
    return np.fromfile(str(p), dtype=np.int16)


def to_float(x_i16: np.ndarray) -> np.ndarray:
    return x_i16.astype(np.float32) / 32768.0


def is_rep_dir(p: Path) -> bool:
    return p.is_dir() and p.name.lower().startswith("rep")


def iter_rep_dirs(root: Path) -> List[Path]:
    reps: List[Path] = []
    if not root.exists():
        return reps
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir()]):
            if is_rep_dir(rep_dir):
                reps.append(rep_dir)
    return reps


def get_onset_sample(rep_dir: Path, meta: dict) -> Tuple[Optional[int], str]:
    """
    Priority:
    1) aligned_meta.json: aligned.onset_sample (absolute in RAW indexing)
    2) meta.json: enter_sample (approx)
    """
    am = rep_dir / "aligned_meta.json"
    if am.exists():
        j = load_json(am)
        onset = j.get("aligned", {}).get("onset_sample", None)
        if isinstance(onset, (int, float)):
            return int(onset), "aligned_meta.onset_sample"
    onset = meta.get("enter_sample", None)
    if isinstance(onset, (int, float)):
        return int(onset), "meta.enter_sample"
    return None, "no_onset"


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


def score_rep(
    rep_dir: Path,
    pre_ms: int,
    post_ms: int,
    eps: float,
    clip_level: float,
) -> Optional[RepScore]:
    meta_path = rep_dir / "meta.json"
    raw_path = rep_dir / "raw_audio_i16.raw"
    if not meta_path.exists() or not raw_path.exists():
        return None

    meta = load_json(meta_path)
    label = str(meta.get("label", rep_dir.parent.name)).strip().lower()
    fs = int(meta.get("fs", 48000))

    onset, onset_src = get_onset_sample(rep_dir, meta)
    if onset is None:
        return RepScore(
            label=label,
            rep_dir=rep_dir,
            fs=fs,
            onset_sample=-1,
            rms_pre=0.0,
            rms_post=0.0,
            peak=0.0,
            clipped=1,
            snr_db=-1e9,
            note="missing onset",
        )

    x = to_float(read_i16_raw(raw_path))
    if x.size == 0:
        return None

    peak = float(np.max(np.abs(x)))
    clipped = 1 if peak >= clip_level else 0

    pre_len = int(round(fs * (pre_ms / 1000.0)))
    post_len = int(round(fs * (post_ms / 1000.0)))

    # pre: [onset-pre_len, onset)
    p0 = max(0, onset - pre_len)
    p1 = max(0, onset)
    pre_seg = x[p0:p1]
    if pre_seg.size < pre_len:
        pre_seg = np.pad(pre_seg, (pre_len - pre_seg.size, 0), mode="constant")

    # post: [onset, onset+post_len)
    q0 = max(0, onset)
    q1 = min(x.size, onset + post_len)
    post_seg = x[q0:q1]
    if post_seg.size < post_len:
        post_seg = np.pad(post_seg, (0, post_len - post_seg.size), mode="constant")

    rms_pre = rms(pre_seg)
    rms_post = rms(post_seg)

    snr_db = 20.0 * math.log10((rms_post + eps) / (rms_pre + eps))

    note = onset_src
    # Light penalties in note string for debugging
    if clipped:
        note += ";clipped"
    if rms_post < 0.003:
        note += ";very_low_post"

    return RepScore(
        label=label,
        rep_dir=rep_dir,
        fs=fs,
        onset_sample=int(onset),
        rms_pre=rms_pre,
        rms_post=rms_post,
        peak=peak,
        clipped=clipped,
        snr_db=snr_db,
        note=note,
    )


def safe_copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        # Avoid overwriting; append suffix
        i = 1
        while True:
            cand = Path(str(dst) + f"_dup{i}")
            if not cand.exists():
                dst = cand
                break
            i += 1
    shutil.copytree(src, dst)


def safe_move(src: Path, dst: Path) -> None:
    if dst.exists():
        i = 1
        while True:
            cand = Path(str(dst) + f"_dup{i}")
            if not cand.exists():
                dst = cand
                break
            i += 1
    shutil.move(str(src), str(dst))


def is_eligible(sc: RepScore, allow_clipped: bool, min_snr_db: float) -> bool:
    if sc.onset_sample < 0:
        return False
    if (not allow_clipped) and sc.clipped:
        return False
    if sc.snr_db < min_snr_db:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in_root", default="captures", help="input captures root")
    ap.add_argument("--out_root", default="captures_gold", help="output root for selected reps")
    ap.add_argument("--k", type=int, default=3, help="number of reps to select per label")

    ap.add_argument("--pre_ms", type=int, default=200, help="pre window length for scoring")
    ap.add_argument("--post_ms", type=int, default=200, help="post window length for scoring")

    ap.add_argument("--eps", type=float, default=1e-7, help="epsilon for snr calculation")
    ap.add_argument("--clip_level", type=float, default=0.98, help="treat peak>=this as clipped/bad")

    ap.add_argument("--min_snr_db", type=float, default=6.0, help="optional filter; keep only reps with snr>=this")
    ap.add_argument("--allow_clipped", action="store_true", help="allow clipped reps to be selected (not recommended)")

    ap.add_argument("--move", action="store_true", help="MOVE instead of copy (default is copy)")
    ap.add_argument("--dry_run", action="store_true", help="don’t copy/move; just write report")

    args = ap.parse_args()

    in_root = Path(args.in_root)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # Score everything
    scores: List[RepScore] = []
    for rep_dir in iter_rep_dirs(in_root):
        sc = score_rep(
            rep_dir=rep_dir,
            pre_ms=args.pre_ms,
            post_ms=args.post_ms,
            eps=args.eps,
            clip_level=args.clip_level,
        )
        if sc is not None:
            scores.append(sc)

    # Group by label
    by: Dict[str, List[RepScore]] = {}
    for sc in scores:
        by.setdefault(sc.label, []).append(sc)

    # --- WARN if label has <k reps total (requested) ---
    for lbl, lst in sorted(by.items()):
        total = len(lst)
        if total < args.k:
            print(f"WARNING: label '{lbl}' has only {total} reps total (<k={args.k}).")

    # Select best per label
    selected: List[RepScore] = []
    for lbl, lst in sorted(by.items()):
        # Filter eligible
        filt = [sc for sc in lst if is_eligible(sc, args.allow_clipped, args.min_snr_db)]

        # Additional warning: eligible reps < k (practically what matters for selection)
        if len(filt) < args.k:
            print(
                f"WARNING: label '{lbl}' has only {len(filt)} eligible reps after filters "
                f"(<k={args.k}). (allow_clipped={bool(args.allow_clipped)}, min_snr_db={args.min_snr_db})"
            )

        # Sort: highest SNR, then higher post RMS, then lower pre RMS, then lower peak
        filt.sort(
            key=lambda s: (
                s.snr_db,
                s.rms_post,
                -s.rms_pre,
                -s.peak,
            ),
            reverse=True,
        )

        picks = filt[: args.k]
        selected.extend(picks)

    # Write report
    report = out_root / "selection_summary.csv"
    with report.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "label",
                "rep_dir",
                "selected",
                "fs",
                "onset_sample",
                "rms_pre",
                "rms_post",
                "snr_db",
                "peak",
                "clipped",
                "note",
            ]
        )
        selected_set = {s.rep_dir.resolve() for s in selected}
        for sc in sorted(scores, key=lambda x: (x.label, x.rep_dir.name)):
            w.writerow(
                [
                    sc.label,
                    str(sc.rep_dir),
                    1 if sc.rep_dir.resolve() in selected_set else 0,
                    sc.fs,
                    sc.onset_sample,
                    f"{sc.rms_pre:.8f}",
                    f"{sc.rms_post:.8f}",
                    f"{sc.snr_db:.3f}",
                    f"{sc.peak:.5f}",
                    sc.clipped,
                    sc.note,
                ]
            )

    # --- summary diagnostics ---
    labels_total = len(by)
    labels_with_any = sum(1 for _, lst in by.items() if len(lst) > 0)

    missing_onset = sum(1 for s in scores if s.onset_sample < 0)
    clipped_cnt = sum(1 for s in scores if s.clipped)
    snr_fail = sum(
        1
        for s in scores
        if (s.onset_sample >= 0 and (not args.allow_clipped) and (not s.clipped) and s.snr_db < args.min_snr_db)
    )

    zero_eligible_labels = 0
    for lbl, lst in by.items():
        elig = [s for s in lst if is_eligible(s, args.allow_clipped, args.min_snr_db)]
        if len(elig) == 0:
            zero_eligible_labels += 1

    print(f"Labels total: {labels_total} (with any reps: {labels_with_any})")
    print(f"Total reps scored: {len(scores)}")
    print(f"Reps missing onset: {missing_onset}")
    print(f"Reps clipped (peak>={args.clip_level}): {clipped_cnt}")
    print(f"Reps failing SNR filter (snr<{args.min_snr_db}): {snr_fail}")
    print(f"Labels with 0 eligible reps after filters: {zero_eligible_labels}")

    print(f"Wrote report: {report}")

    # Copy/move selections
    if args.dry_run:
        print("Dry run: not copying/moving.")
        return

    n_done = 0
    for sc in selected:
        src = sc.rep_dir
        dst = out_root / sc.label / src.name
        (out_root / sc.label).mkdir(parents=True, exist_ok=True)

        if args.move:
            safe_move(src, dst)
        else:
            safe_copytree(src, dst)
        n_done += 1

    action = "Moved" if args.move else "Copied"
    print(f"{action} {n_done} rep folders into {out_root}")


if __name__ == "__main__":
    main()
