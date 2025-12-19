#!/usr/bin/env python3
"""
template_curve_eval.py

Evaluates deterministic set-of-exemplars matcher performance vs number of clean exemplars per key.

Assumes dataset layout:
  captures/<label>/repNNN_YYYYMMDD-HHMMSS/
    meta.json
    raw_audio_i16.raw
    (optional) aligned_i16.raw + aligned_meta.json

Process:
  - For each label, collect all reps that have usable features
  - For each N in Ns:
      Repeat K times:
        - For each label: sample N reps as exemplars (templates)
        - Remaining reps of that label are test queries
        - Classify each query using ExemplarMatcher
      Aggregate:
        - top-1 accuracy (always accept)
        - optionally thresholded accepted accuracy + coverage (if --sweep_thresholds or fixed thresholds)
  - Saves:
      out_dir/curve.csv
      out_dir/per_label.csv
      out_dir/accuracy_vs_N.png
      out_dir/coverage_vs_N.png (if thresholded)
      out_dir/accepted_accuracy_vs_N.png (if thresholded)

"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt

from matcher import FeatConfig, ExemplarMatcher, load_feature_for_rep, ensure_lower


@dataclass
class EvalStats:
    correct: int = 0
    total: int = 0
    accepted_correct: int = 0
    accepted_total: int = 0

    def add(self, y_true: str, y_pred: str, accepted: bool):
        self.total += 1
        if y_pred == y_true:
            self.correct += 1
        if accepted:
            self.accepted_total += 1
            if y_pred == y_true:
                self.accepted_correct += 1

    @property
    def acc(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def coverage(self) -> float:
        return self.accepted_total / self.total if self.total else 0.0

    @property
    def accepted_acc(self) -> float:
        return self.accepted_correct / self.accepted_total if self.accepted_total else 0.0


def find_rep_dirs(root: Path) -> List[Path]:
    rep_dirs: List[Path] = []
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            rep_dirs.append(rep_dir)
    return rep_dirs


def bootstrap_ci(values: List[float], alpha: float = 0.05) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    vs = sorted(values)
    lo_i = int(math.floor((alpha / 2) * (len(vs) - 1)))
    hi_i = int(math.floor((1 - alpha / 2) * (len(vs) - 1)))
    return float(vs[lo_i]), float(vs[hi_i])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--out_dir", default="template_curve_out")
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--Ns", default="2,3,5,7,10,15,20")
    ap.add_argument("--K", type=int, default=100, help="repeats per N")
    ap.add_argument("--min_reps_per_label", type=int, default=12,
                    help="labels with fewer reps are skipped (must exceed max N for good eval)")

    # feature config (must match matcher)
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--hop", type=int, default=512)
    ap.add_argument("--fmin", type=float, default=80.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)

    # thresholding
    ap.add_argument("--t_abs", type=float, default=None,
                    help="absolute similarity threshold (cosine-ish). If set, compute accepted_acc/coverage.")
    ap.add_argument("--t_margin", type=float, default=None,
                    help="margin threshold. If set, compute accepted_acc/coverage.")
    ap.add_argument("--sweep_thresholds", action="store_true",
                    help="sweep thresholds to find best accepted-acc @ coverage; writes threshold_sweep.json")
    ap.add_argument("--sweep_abs", default="0.50,0.55,0.60,0.65,0.70,0.75,0.80,0.85")
    ap.add_argument("--sweep_margin", default="0.00,0.01,0.02,0.03,0.04,0.05,0.06")

    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    Ns = [int(x.strip()) for x in args.Ns.split(",") if x.strip()]
    maxN = max(Ns) if Ns else 0

    cfg = FeatConfig(seg_ms=args.seg_ms, fft_n=args.fft_n, hop=args.hop, fmin_hz=args.fmin, fmax_hz=args.fmax, smooth_bins=args.smooth_bins)

    # Load all usable features per label
    root = Path(args.captures_root)
    all_by_label: Dict[str, List[Tuple[Path, np.ndarray]]] = {}
    for rep_dir in find_rep_dirs(root):
        f, lbl = load_feature_for_rep(rep_dir, cfg)
        if f is None or lbl is None:
            continue
        all_by_label.setdefault(lbl, []).append((rep_dir, f))

    # filter labels
    usable: Dict[str, List[np.ndarray]] = {}
    for lbl, items in all_by_label.items():
        if len(items) >= max(args.min_reps_per_label, maxN + 2):
            usable[lbl] = [f for _, f in items]

    labels = sorted(usable.keys())
    if len(labels) < 2:
        raise SystemExit(f"Not enough usable labels. Have {len(labels)} after filtering.")

    # optional threshold sweep space
    sweep_abs = [float(x.strip()) for x in args.sweep_abs.split(",") if x.strip()]
    sweep_margin = [float(x.strip()) for x in args.sweep_margin.split(",") if x.strip()]

    # Results storage
    curve_rows = []
    per_label_rows = []

    # For plotting
    plot_N = []
    plot_acc_mean = []
    plot_acc_ci_lo = []
    plot_acc_ci_hi = []

    plot_cov_mean = []
    plot_cov_ci_lo = []
    plot_cov_ci_hi = []

    plot_accA_mean = []
    plot_accA_ci_lo = []
    plot_accA_ci_hi = []

    # Threshold sweep results (optional)
    sweep_summary = {}

    for N in Ns:
        acc_samples = []
        cov_samples = []
        accA_samples = []

        # per-label accumulation over repeats
        per_lbl_correct = {lbl: 0 for lbl in labels}
        per_lbl_total = {lbl: 0 for lbl in labels}

        # If sweeping thresholds: find best accepted_acc at >= some coverage? We’ll store full grid.
        if args.sweep_thresholds:
            grid = {(ta, tm): EvalStats() for ta in sweep_abs for tm in sweep_margin}

        for k in range(args.K):
            # build templates by sampling N exemplars per label
            tmpl = {}
            test_sets = {}

            for lbl in labels:
                feats = usable[lbl]
                idx = np.random.permutation(len(feats))
                ex_idx = idx[:N]
                te_idx = idx[N:]
                tmpl[lbl] = np.stack([feats[i] for i in ex_idx], axis=0).astype(np.float32)
                test_sets[lbl] = [feats[i] for i in te_idx]

            matcher = ExemplarMatcher(tmpl)

            stats = EvalStats()

            # classify all test queries
            for y in labels:
                for q in test_sets[y]:
                    pr = matcher.predict(q, t_abs=args.t_abs, t_margin=args.t_margin)
                    stats.add(y, pr.pred_label, pr.accepted)

                    per_lbl_total[y] += 1
                    if pr.pred_label == y:
                        per_lbl_correct[y] += 1

                    if args.sweep_thresholds:
                        # score once; reuse scores to avoid recomputing
                        # We need S1/S2/margin; pr already has them if we call predict with None thresholds:
                        pr0 = matcher.predict(q, t_abs=None, t_margin=None)
                        for (ta, tm), gs in grid.items():
                            accepted = (pr0.s1 >= ta) and (pr0.margin >= tm)
                            gs.add(y, pr0.pred_label, accepted)

            acc_samples.append(stats.acc)

            if args.t_abs is not None or args.t_margin is not None:
                cov_samples.append(stats.coverage)
                accA_samples.append(stats.accepted_acc)

        # summarize
        acc_mean = float(np.mean(acc_samples))
        acc_lo, acc_hi = bootstrap_ci(acc_samples)

        row = {
            "N": N,
            "acc_mean": acc_mean,
            "acc_ci_lo": acc_lo,
            "acc_ci_hi": acc_hi,
        }

        if args.t_abs is not None or args.t_margin is not None:
            cov_mean = float(np.mean(cov_samples)) if cov_samples else 0.0
            cov_lo, cov_hi = bootstrap_ci(cov_samples) if cov_samples else (0.0, 0.0)
            accA_mean = float(np.mean(accA_samples)) if accA_samples else 0.0
            accA_lo, accA_hi = bootstrap_ci(accA_samples) if accA_samples else (0.0, 0.0)

            row.update({
                "coverage_mean": cov_mean,
                "coverage_ci_lo": cov_lo,
                "coverage_ci_hi": cov_hi,
                "accepted_acc_mean": accA_mean,
                "accepted_acc_ci_lo": accA_lo,
                "accepted_acc_ci_hi": accA_hi,
                "t_abs": args.t_abs,
                "t_margin": args.t_margin,
            })

        curve_rows.append(row)

        # per-label rows for this N (aggregate over all repeats)
        for lbl in labels:
            tot = per_lbl_total[lbl]
            cor = per_lbl_correct[lbl]
            per_label_rows.append({
                "N": N,
                "label": lbl,
                "acc": (cor / tot) if tot else 0.0,
                "total": tot,
            })

        # for plots
        plot_N.append(N)
        plot_acc_mean.append(acc_mean)
        plot_acc_ci_lo.append(acc_lo)
        plot_acc_ci_hi.append(acc_hi)

        if args.t_abs is not None or args.t_margin is not None:
            plot_cov_mean.append(row["coverage_mean"])
            plot_cov_ci_lo.append(row["coverage_ci_lo"])
            plot_cov_ci_hi.append(row["coverage_ci_hi"])
            plot_accA_mean.append(row["accepted_acc_mean"])
            plot_accA_ci_lo.append(row["accepted_acc_ci_lo"])
            plot_accA_ci_hi.append(row["accepted_acc_ci_hi"])

        # optional threshold sweep summary per N
        if args.sweep_thresholds:
            best = None
            best_key = None
            # choose best accepted_acc subject to having non-trivial coverage (>=0.3 default)
            min_cov = 0.30
            for (ta, tm), gs in grid.items():
                cov = gs.coverage
                aacc = gs.accepted_acc
                # prioritize accepted_acc, then coverage
                if cov >= min_cov:
                    key = (aacc, cov)
                    if best is None or key > best:
                        best = key
                        best_key = (ta, tm)

            sweep_summary[str(N)] = {
                "min_cov": min_cov,
                "best_t_abs": best_key[0] if best_key else None,
                "best_t_margin": best_key[1] if best_key else None,
                "best_accepted_acc": best[0] if best else None,
                "best_coverage": best[1] if best else None,
            }

    # write CSVs
    curve_csv = out_dir / "curve.csv"
    with curve_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(curve_rows[0].keys()))
        w.writeheader()
        w.writerows(curve_rows)

    per_label_csv = out_dir / "per_label.csv"
    with per_label_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["N", "label", "acc", "total"])
        w.writeheader()
        w.writerows(per_label_rows)

    # write config json
    (out_dir / "config.json").write_text(json.dumps({
        "seed": args.seed,
        "Ns": Ns,
        "K": args.K,
        "labels_used": labels,
        "feat_cfg": cfg.__dict__,
        "thresholds": {"t_abs": args.t_abs, "t_margin": args.t_margin},
        "sweep_thresholds": args.sweep_thresholds,
    }, indent=2), encoding="utf-8")

    if args.sweep_thresholds:
        (out_dir / "threshold_sweep.json").write_text(json.dumps(sweep_summary, indent=2), encoding="utf-8")

    # ---------------- plots ----------------

    # Accuracy vs N
    plt.figure()
    plt.plot(plot_N, plot_acc_mean)
    plt.fill_between(plot_N, plot_acc_ci_lo, plot_acc_ci_hi, alpha=0.2)
    plt.xlabel("Exemplars per label (N)")
    plt.ylabel("Top-1 accuracy (always accept)")
    plt.title("Accuracy vs exemplars per label")
    plt.grid(True, alpha=0.3)
    plt.savefig(out_dir / "accuracy_vs_N.png", dpi=160, bbox_inches="tight")
    plt.close()

    # If thresholding requested, plot coverage and accepted accuracy
    if args.t_abs is not None or args.t_margin is not None:
        plt.figure()
        plt.plot(plot_N, plot_cov_mean)
        plt.fill_between(plot_N, plot_cov_ci_lo, plot_cov_ci_hi, alpha=0.2)
        plt.xlabel("Exemplars per label (N)")
        plt.ylabel("Coverage (accepted fraction)")
        plt.title("Coverage vs exemplars per label")
        plt.grid(True, alpha=0.3)
        plt.savefig(out_dir / "coverage_vs_N.png", dpi=160, bbox_inches="tight")
        plt.close()

        plt.figure()
        plt.plot(plot_N, plot_accA_mean)
        plt.fill_between(plot_N, plot_accA_ci_lo, plot_accA_ci_hi, alpha=0.2)
        plt.xlabel("Exemplars per label (N)")
        plt.ylabel("Accepted accuracy (among accepted)")
        plt.title("Accepted accuracy vs exemplars per label")
        plt.grid(True, alpha=0.3)
        plt.savefig(out_dir / "accepted_accuracy_vs_N.png", dpi=160, bbox_inches="tight")
        plt.close()

    print(f"Wrote: {curve_csv}")
    print(f"Wrote: {per_label_csv}")
    print(f"Plots in: {out_dir}")

if __name__ == "__main__":
    main()
