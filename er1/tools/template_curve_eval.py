#!/usr/bin/env python3
"""
template_curve_eval.py

Evaluates deterministic set-of-exemplars matcher performance vs number of exemplars per key.

Dataset layout (for each root):
  <root>/<label>/repNNN_YYYYMMDD-HHMMSS/
    meta.json
    raw_audio_i16.raw
    (optional) aligned_i16.raw + aligned_meta.json

Modes:
  1) Single-root (default):
     - templates and queries come from --captures_root
     - for each label and each repeat: sample N exemplars, test on remaining reps

  2) Two-root (recommended for noisy stress eval):
     - templates come from --templates_root (e.g. clean captures)
     - queries come from --captures_root (e.g. noisy captures_stress)
     - for each repeat: sample N exemplars per label from templates_root
       and test on ALL query reps for that label from captures_root

Filtering:
  A label is usable if it meets:
    templates_count >= N
    queries_count   >= min_queries_per_label

  In single-root mode, queries_count is the same dataset, and additionally
  we require total_count >= N + min_test_per_label to ensure at least some test.

Outputs:
  out_dir/curve.csv
  out_dir/per_label.csv
  out_dir/accuracy_vs_N.png
  out_dir/coverage_vs_N.png (if thresholded)
  out_dir/accepted_accuracy_vs_N.png (if thresholded)
  out_dir/bestN.json
  out_dir/confusion_bestN_<N>.csv + .png
  out_dir/confusion_bestN_<N>_accepted.csv + .png (if thresholded)
  out_dir/config.json
  out_dir/threshold_sweep.json (if --sweep_thresholds)

"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import matplotlib.pyplot as plt
import numpy as np

from matcher import FeatConfig, ExemplarMatcher, load_feature_for_rep


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
    if not root.exists():
        return rep_dirs
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            rep_dirs.append(rep_dir)
    return rep_dirs


def percentile_ci(values: List[float], alpha: float = 0.05) -> Tuple[float, float]:
    """
    Percentile CI over repeated runs (NOT a resampling bootstrap).
    Good enough for seeing stability across K repeats.
    """
    if not values:
        return 0.0, 0.0
    vs = sorted(values)
    lo_i = int(math.floor((alpha / 2) * (len(vs) - 1)))
    hi_i = int(math.floor((1 - alpha / 2) * (len(vs) - 1)))
    return float(vs[lo_i]), float(vs[hi_i])


def load_all_features_by_label(root: Path, cfg: FeatConfig) -> Dict[str, List[np.ndarray]]:
    """
    Loads usable features under root, keyed by label.
    """
    out: Dict[str, List[np.ndarray]] = {}
    for rep_dir in find_rep_dirs(root):
        f, lbl = load_feature_for_rep(rep_dir, cfg)
        if f is None or lbl is None:
            continue
        out.setdefault(lbl, []).append(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures", help="Query root (and template root in single-root mode)")
    ap.add_argument("--templates_root", default=None, help="Optional separate template root (e.g. clean captures)")
    ap.add_argument("--out_dir", default="template_curve_out")
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--Ns", default="1,2,3,5,7,10", help="comma-separated list of exemplar counts")
    ap.add_argument("--K", type=int, default=100, help="repeats per N (resample exemplars each repeat)")

    # Filtering controls
    ap.add_argument(
        "--min_templates_per_label",
        type=int,
        default=3,
        help="labels with fewer template reps are skipped (applies to template root)",
    )
    ap.add_argument(
        "--min_queries_per_label",
        type=int,
        default=1,
        help="labels with fewer query reps are skipped (applies to query root)",
    )
    ap.add_argument(
        "--min_test_per_label",
        type=int,
        default=2,
        help="single-root mode only: require at least this many test reps per label (total >= N + min_test)",
    )

    # best-N selection (used for bestN.json + confusion matrix outputs)
    ap.add_argument(
        "--target_acc",
        type=float,
        default=0.95,
        help="Pick the smallest N with mean accuracy >= target; otherwise pick max mean accuracy",
    )

    # feature config (must match matcher)
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--hop", type=int, default=512)
    ap.add_argument("--fmin", type=float, default=80.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)

    # thresholding
    ap.add_argument(
        "--t_abs",
        type=float,
        default=None,
        help="absolute similarity threshold (cosine-ish). If set, compute accepted_acc/coverage.",
    )
    ap.add_argument(
        "--t_margin",
        type=float,
        default=None,
        help="margin threshold. If set, compute accepted_acc/coverage.",
    )
    ap.add_argument(
        "--sweep_thresholds",
        action="store_true",
        help="sweep thresholds; writes threshold_sweep.json",
    )
    ap.add_argument("--sweep_abs", default="0.50,0.55,0.60,0.65,0.70,0.75,0.80,0.85")
    ap.add_argument("--sweep_margin", default="0.00,0.01,0.02,0.03,0.04,0.05,0.06")
    ap.add_argument(
        "--sweep_min_cov",
        type=float,
        default=0.30,
        help="threshold sweep: only consider settings with coverage >= this",
    )

    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    Ns = [int(x.strip()) for x in args.Ns.split(",") if x.strip()]
    if not Ns:
        raise SystemExit("Ns is empty.")
    maxN = max(Ns)

    cfg = FeatConfig(
        seg_ms=args.seg_ms,
        fft_n=args.fft_n,
        hop=args.hop,
        fmin_hz=args.fmin,
        fmax_hz=args.fmax,
        smooth_bins=args.smooth_bins,
    )

    query_root = Path(args.captures_root)
    template_root = Path(args.templates_root) if args.templates_root else query_root
    two_root = (args.templates_root is not None)

    # Load features for templates and queries
    tmpl_by_label_all = load_all_features_by_label(template_root, cfg)
    qry_by_label_all = load_all_features_by_label(query_root, cfg)

    # Determine usable label set (keep consistent across all N)
    # Requirements:
    #   templates_count >= maxN AND >= min_templates_per_label
    #   queries_count   >= min_queries_per_label
    # In single-root mode: total_count >= maxN + min_test_per_label
    usable_labels: List[str] = []
    for lbl in sorted(set(tmpl_by_label_all.keys()) & set(qry_by_label_all.keys())):
        tcnt = len(tmpl_by_label_all.get(lbl, []))
        qcnt = len(qry_by_label_all.get(lbl, []))

        if tcnt < max(args.min_templates_per_label, maxN):
            continue
        if qcnt < args.min_queries_per_label:
            continue
        if not two_root:
            # same pool; ensure enough tests remain even at maxN
            if tcnt < (maxN + args.min_test_per_label):
                continue

        usable_labels.append(lbl)

    if len(usable_labels) < 2:
        raise SystemExit(f"Not enough usable labels. Have {len(usable_labels)} after filtering.")

    labels = usable_labels

    # Prepare filtered arrays
    tmpl_feats: Dict[str, List[np.ndarray]] = {lbl: tmpl_by_label_all[lbl] for lbl in labels}
    qry_feats: Dict[str, List[np.ndarray]] = {lbl: qry_by_label_all[lbl] for lbl in labels}

    # optional threshold sweep space
    sweep_abs = [float(x.strip()) for x in args.sweep_abs.split(",") if x.strip()]
    sweep_margin = [float(x.strip()) for x in args.sweep_margin.split(",") if x.strip()]

    # Results storage
    curve_rows = []
    per_label_rows = []

    # Confusion matrices accumulated over all repeats, per N
    label_to_idx = {lbl: i for i, lbl in enumerate(labels)}
    conf_by_N: Dict[int, np.ndarray] = {}
    confA_by_N: Dict[int, np.ndarray] = {}

    # For plotting
    plot_N: List[int] = []
    plot_acc_mean: List[float] = []
    plot_acc_ci_lo: List[float] = []
    plot_acc_ci_hi: List[float] = []

    plot_cov_mean: List[float] = []
    plot_cov_ci_lo: List[float] = []
    plot_cov_ci_hi: List[float] = []

    plot_accA_mean: List[float] = []
    plot_accA_ci_lo: List[float] = []
    plot_accA_ci_hi: List[float] = []

    sweep_summary = {}

    for N in Ns:
        if N <= 0:
            raise SystemExit(f"Invalid N={N}; must be >=1")
        if N > maxN:
            raise SystemExit(f"Internal error: N={N} > maxN={maxN}")

        # Sanity per label: ensure enough templates
        for lbl in labels:
            if len(tmpl_feats[lbl]) < N:
                raise SystemExit(f"Label {lbl} has only {len(tmpl_feats[lbl])} templates but N={N}")

        acc_samples: List[float] = []
        cov_samples: List[float] = []
        accA_samples: List[float] = []

        per_lbl_correct = {lbl: 0 for lbl in labels}
        per_lbl_total = {lbl: 0 for lbl in labels}

        if args.sweep_thresholds:
            grid = {(ta, tm): EvalStats() for ta in sweep_abs for tm in sweep_margin}

        conf = np.zeros((len(labels), len(labels)), dtype=np.int64)
        confA = np.zeros((len(labels), len(labels)), dtype=np.int64)

        for _k in range(args.K):
            # build templates by sampling N exemplars per label from template set
            tmpl = {}
            for lbl in labels:
                featsT = tmpl_feats[lbl]
                idx = np.random.permutation(len(featsT))
                ex_idx = idx[:N]
                tmpl[lbl] = np.stack([featsT[i] for i in ex_idx], axis=0).astype(np.float32)

            matcher = ExemplarMatcher(tmpl)
            stats = EvalStats()

            # Build query sets for this repeat
            # - two-root: use all query reps every repeat
            # - single-root: exclude the sampled exemplars by resampling from the same pool
            if two_root:
                test_sets = qry_feats
            else:
                test_sets = {}
                for lbl in labels:
                    feats = tmpl_feats[lbl]
                    idx = np.random.permutation(len(feats))
                    te_idx = idx[N:]  # remaining as tests
                    test_sets[lbl] = [feats[i] for i in te_idx]

            # classify all queries
            for y in labels:
                for q in test_sets[y]:
                    pr = matcher.predict(q, t_abs=args.t_abs, t_margin=args.t_margin)
                    stats.add(y, pr.pred_label, pr.accepted)

                    i = label_to_idx[y]
                    j = label_to_idx.get(pr.pred_label, None)
                    if j is not None:
                        conf[i, j] += 1
                        if pr.accepted:
                            confA[i, j] += 1

                    per_lbl_total[y] += 1
                    if pr.pred_label == y:
                        per_lbl_correct[y] += 1

                    if args.sweep_thresholds:
                        pr0 = matcher.predict(q, t_abs=None, t_margin=None)
                        for (ta, tm), gs in grid.items():
                            accepted = (pr0.s1 >= ta) and (pr0.margin >= tm)
                            gs.add(y, pr0.pred_label, accepted)

            acc_samples.append(stats.acc)
            if args.t_abs is not None or args.t_margin is not None:
                cov_samples.append(stats.coverage)
                accA_samples.append(stats.accepted_acc)

        conf_by_N[N] = conf
        confA_by_N[N] = confA

        acc_mean = float(np.mean(acc_samples))
        acc_lo, acc_hi = percentile_ci(acc_samples)

        row = {
            "N": N,
            "acc_mean": acc_mean,
            "acc_ci_lo": acc_lo,
            "acc_ci_hi": acc_hi,
        }

        if args.t_abs is not None or args.t_margin is not None:
            cov_mean = float(np.mean(cov_samples)) if cov_samples else 0.0
            cov_lo, cov_hi = percentile_ci(cov_samples) if cov_samples else (0.0, 0.0)
            accA_mean = float(np.mean(accA_samples)) if accA_samples else 0.0
            accA_lo, accA_hi = percentile_ci(accA_samples) if accA_samples else (0.0, 0.0)
            row.update(
                {
                    "coverage_mean": cov_mean,
                    "coverage_ci_lo": cov_lo,
                    "coverage_ci_hi": cov_hi,
                    "accepted_acc_mean": accA_mean,
                    "accepted_acc_ci_lo": accA_lo,
                    "accepted_acc_ci_hi": accA_hi,
                    "t_abs": args.t_abs,
                    "t_margin": args.t_margin,
                }
            )

        curve_rows.append(row)

        # per-label rows for this N (aggregate over all repeats)
        for lbl in labels:
            tot = per_lbl_total[lbl]
            cor = per_lbl_correct[lbl]
            per_label_rows.append(
                {
                    "N": N,
                    "label": lbl,
                    "acc": (cor / tot) if tot else 0.0,
                    "total": tot,
                }
            )

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

        if args.sweep_thresholds:
            best = None
            best_key = None
            min_cov = float(args.sweep_min_cov)
            for (ta, tm), gs in grid.items():
                cov = gs.coverage
                aacc = gs.accepted_acc
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
    (out_dir / "config.json").write_text(
        json.dumps(
            {
                "seed": args.seed,
                "Ns": Ns,
                "K": args.K,
                "mode": "two_root" if two_root else "single_root",
                "captures_root": str(query_root),
                "templates_root": str(template_root) if two_root else None,
                "labels_used": labels,
                "counts": {
                    "templates_per_label": {lbl: len(tmpl_feats[lbl]) for lbl in labels},
                    "queries_per_label": {lbl: len(qry_feats[lbl]) for lbl in labels},
                },
                "filters": {
                    "min_templates_per_label": args.min_templates_per_label,
                    "min_queries_per_label": args.min_queries_per_label,
                    "min_test_per_label": args.min_test_per_label,
                    "maxN": maxN,
                },
                "feat_cfg": cfg.__dict__,
                "thresholds": {"t_abs": args.t_abs, "t_margin": args.t_margin},
                "sweep_thresholds": args.sweep_thresholds,
                "sweep_min_cov": args.sweep_min_cov,
                "target_acc": args.target_acc,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.sweep_thresholds:
        (out_dir / "threshold_sweep.json").write_text(json.dumps(sweep_summary, indent=2), encoding="utf-8")

    # ---------------- best-N selection + confusion matrix outputs ----------------

    bestN = None
    curve_by_N = {int(r["N"]): r for r in curve_rows}
    for N in sorted(Ns):
        if curve_by_N[N]["acc_mean"] >= args.target_acc:
            bestN = N
            break
    if bestN is None:
        bestN = max(sorted(Ns), key=lambda n: curve_by_N[n]["acc_mean"])

    per_label_best = {
        r["label"]: {"acc": float(r["acc"]), "total": int(r["total"])}
        for r in per_label_rows
        if int(r["N"]) == int(bestN)
    }

    best_info = {
        "bestN": int(bestN),
        "selection": {
            "target_acc": float(args.target_acc),
            "picked_smallest_meeting_target": bool(curve_by_N[int(bestN)]["acc_mean"] >= args.target_acc),
        },
        "top1": {
            "acc_mean": float(curve_by_N[int(bestN)]["acc_mean"]),
            "acc_ci_lo": float(curve_by_N[int(bestN)]["acc_ci_lo"]),
            "acc_ci_hi": float(curve_by_N[int(bestN)]["acc_ci_hi"]),
        },
        "labels_used": labels,
        "per_label": per_label_best,
        "thresholds": {"t_abs": args.t_abs, "t_margin": args.t_margin},
        "feat_cfg": cfg.__dict__,
        "K": int(args.K),
        "seed": int(args.seed),
        "mode": "two_root" if two_root else "single_root",
        "captures_root": str(query_root),
        "templates_root": str(template_root) if two_root else None,
    }
    (out_dir / "bestN.json").write_text(json.dumps(best_info, indent=2), encoding="utf-8")

    def _write_confusion(conf_mat: np.ndarray, stem: str):
        csv_path = out_dir / f"{stem}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["true\\pred"] + labels)
            for i, lbl in enumerate(labels):
                w.writerow([lbl] + [int(x) for x in conf_mat[i, :].tolist()])

        plt.figure(figsize=(max(6, 0.18 * len(labels)), max(6, 0.18 * len(labels))))
        plt.imshow(conf_mat)
        plt.title(stem)
        plt.xlabel("Predicted")
        plt.ylabel("True")
        plt.xticks(range(len(labels)), labels, rotation=90, fontsize=6)
        plt.yticks(range(len(labels)), labels, fontsize=6)
        plt.tight_layout()
        plt.savefig(out_dir / f"{stem}.png", dpi=200, bbox_inches="tight")
        plt.close()

    _write_confusion(conf_by_N[int(bestN)], f"confusion_bestN_{int(bestN)}")
    if args.t_abs is not None or args.t_margin is not None:
        _write_confusion(confA_by_N[int(bestN)], f"confusion_bestN_{int(bestN)}_accepted")

    # ---------------- plots ----------------

    plt.figure()
    plt.plot(plot_N, plot_acc_mean)
    plt.fill_between(plot_N, plot_acc_ci_lo, plot_acc_ci_hi, alpha=0.2)
    plt.xlabel("Exemplars per label (N)")
    plt.ylabel("Top-1 accuracy (always accept)")
    plt.title("Accuracy vs exemplars per label")
    plt.grid(True, alpha=0.3)
    plt.savefig(out_dir / "accuracy_vs_N.png", dpi=160, bbox_inches="tight")
    plt.close()

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
    print(f"Wrote: {out_dir / 'bestN.json'}")
    print(f"Wrote: {out_dir / f'confusion_bestN_{int(bestN)}.csv'}")
    print(f"Wrote: {out_dir / f'confusion_bestN_{int(bestN)}.png'}")
    if args.t_abs is not None or args.t_margin is not None:
        print(f"Wrote: {out_dir / f'confusion_bestN_{int(bestN)}_accepted.csv'}")
        print(f"Wrote: {out_dir / f'confusion_bestN_{int(bestN)}_accepted.png'}")
    print(f"Plots in: {out_dir}")


if __name__ == "__main__":
    main()
