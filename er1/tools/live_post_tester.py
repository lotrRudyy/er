#!/usr/bin/env python3
"""
live_post_tester.py

Watches a captures root for new rep folders.
Waits until QC has produced aligned_i16.raw + aligned_meta.json,
then runs post-only classification and prints debug.

Matches new matcher.py (ALIGNED-ONLY):
- FeatConfig
- ExemplarMatcher
- load_feature_for_rep(rep_dir, cfg) -> (feat_vec, true_label)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from matcher import FeatConfig, ExemplarMatcher, load_feature_for_rep


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def rep_id(rep_dir: Path) -> str:
    return f"{rep_dir.parent.name}/{rep_dir.name}"


def find_rep_dirs(root: Path) -> List[Path]:
    out: List[Path] = []
    if not root.exists():
        return out
    for lbl_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        for rep_dir in sorted([p for p in lbl_dir.iterdir() if p.is_dir() and p.name.startswith("rep")]):
            out.append(rep_dir)
    return out


def load_templates_npz(npz_path: Path) -> Dict[str, np.ndarray]:
    data = np.load(npz_path, allow_pickle=False)
    templates: Dict[str, np.ndarray] = {}
    for k in data.files:
        if not k.startswith("tmpl__"):
            continue
        lbl = k[len("tmpl__") :].strip().lower()
        arr = data[k].astype(np.float32, copy=False)
        if arr.ndim != 2 or arr.shape[0] == 0:
            continue
        templates[lbl] = arr
    if not templates:
        raise RuntimeError(f"No templates found in {npz_path} (expected tmpl__<label> keys)")
    return templates


def qc_done(rep_dir: Path) -> bool:
    return (rep_dir / "aligned_i16.raw").exists() and (rep_dir / "aligned_meta.json").exists()


def wait_for_qc(rep_dir: Path, timeout_s: float, poll_s: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if qc_done(rep_dir):
            # extra: ensure aligned_meta is readable (writer finished)
            try:
                _ = load_json(rep_dir / "aligned_meta.json")
                return True
            except Exception:
                pass
        time.sleep(poll_s)
    return False


def read_qc_stats(rep_dir: Path) -> Dict[str, float]:
    am = rep_dir / "aligned_meta.json"
    if not am.exists():
        return {}
    j = load_json(am)
    qc = j.get("qc", {})
    aligned = j.get("aligned", {})
    out: Dict[str, float] = {}

    if isinstance(qc, dict):
        for k in ["fs", "peak", "clip", "rms_pre", "rms_post", "snr_proxy_db", "onset_minus_enter_ms"]:
            if k in qc:
                try:
                    out[k] = float(qc[k])
                except Exception:
                    pass

    if isinstance(aligned, dict):
        for k in ["onset_in_aligned_sample", "pre_align_ms", "post_align_ms"]:
            if k in aligned:
                try:
                    out[k] = float(aligned[k])
                except Exception:
                    pass

    # flags if present
    flags = j.get("flags", [])
    if isinstance(flags, list):
        out["_flags_count"] = float(len(flags))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", required=True, help="Same output folder you pass to receiver")
    ap.add_argument("--templates_npz", required=True)
    ap.add_argument("--poll_ms", type=int, default=100)
    ap.add_argument("--qc_timeout_s", type=float, default=8.0)

    # feature config (post-only)
    ap.add_argument("--seg_ms", type=int, default=200)
    ap.add_argument("--fft_n", type=int, default=4096)
    ap.add_argument("--hop", type=int, default=512)
    ap.add_argument("--fmin", type=float, default=50.0)
    ap.add_argument("--fmax", type=float, default=8000.0)
    ap.add_argument("--smooth_bins", type=int, default=3)
    ap.add_argument("--eps", type=float, default=1e-7)

    # decision thresholds (optional)
    ap.add_argument("--t_abs", type=float, default=None)
    ap.add_argument("--t_margin", type=float, default=None)

    args = ap.parse_args()

    root = Path(args.captures_root)
    templates = load_templates_npz(Path(args.templates_npz))
    matcher = ExemplarMatcher(templates)

    cfg = FeatConfig(
        feature="post",
        seg_ms=args.seg_ms,
        pre_ms=200,
        post_ms=200,
        fft_n=args.fft_n,
        hop=args.hop,
        fmin_hz=args.fmin,
        fmax_hz=args.fmax,
        smooth_bins=args.smooth_bins,
        eps=args.eps,
    )

    seen = set()

    print(f"[live_post_tester] watching: {root.resolve()}")
    print(f"[live_post_tester] templates: {Path(args.templates_npz).resolve()} labels={len(templates)}")
    print(f"[live_post_tester] feat_cfg: {cfg}")
    if args.t_abs is not None or args.t_margin is not None:
        print(f"[live_post_tester] thresholds: t_abs={args.t_abs}  t_margin={args.t_margin}")
    print("Press Ctrl+C to stop.\n")

    while True:
        for rep_dir in find_rep_dirs(root):
            rid = rep_id(rep_dir)
            if rid in seen:
                continue

            # Ensure the rep exists at least
            if not (rep_dir / "meta.json").exists():
                continue

            # Wait for receiver/QC to finish writing aligned files
            ok = wait_for_qc(rep_dir, timeout_s=args.qc_timeout_s, poll_s=max(0.01, args.poll_ms / 1000.0))
            if not ok:
                print("=" * 100)
                print(f"REP: {rid}")
                print("QC: timed out waiting for aligned outputs (aligned_i16.raw / aligned_meta.json)")
                seen.add(rid)
                continue

            try:
                q, true_label = load_feature_for_rep(rep_dir, cfg)
                pr = matcher.predict(q, t_abs=args.t_abs, t_margin=args.t_margin)
                qc = read_qc_stats(rep_dir)

                print("=" * 100)
                print(f"REP: {rid}")
                print(f"TRUE(label from meta): {true_label}")

                if qc:
                    fs = qc.get("fs", float("nan"))
                    peak = qc.get("peak", float("nan"))
                    clip = qc.get("clip", float("nan"))
                    rms_pre = qc.get("rms_pre", float("nan"))
                    rms_post = qc.get("rms_post", float("nan"))
                    snr = qc.get("snr_proxy_db", float("nan"))
                    omen = qc.get("onset_minus_enter_ms", float("nan"))
                    onset_in_aligned = qc.get("onset_in_aligned_sample", float("nan"))
                    pre_align_ms = qc.get("pre_align_ms", float("nan"))
                    post_align_ms = qc.get("post_align_ms", float("nan"))
                    print(
                        f"QC: fs={fs:.0f} peak={peak:.4f} clip={clip:.0f} "
                        f"rms_pre={rms_pre:.6f} rms_post={rms_post:.6f} "
                        f"snr={snr:.2f}dB onset-enter={omen:.1f}ms "
                        f"onset_in_aligned={onset_in_aligned:.0f} pre_align_ms={pre_align_ms:.0f} post_align_ms={post_align_ms:.0f}"
                    )
                else:
                    print("QC: (no qc/aligned fields found)")

                print(f"PRED: {pr.pred_label}  s1={pr.s1:.4f}  s2={pr.s2:.4f}  margin={pr.margin:.4f}  accepted={pr.accepted}")
                top = sorted(pr.scores.items(), key=lambda kv: kv[1], reverse=True)[:10]
                print("TOP10:", "  ".join([f"{k}:{v:.3f}" for k, v in top]))

            except Exception as e:
                print("=" * 100)
                print(f"REP: {rid}")
                print(f"ERROR: {e}")

            seen.add(rid)

        time.sleep(max(0.02, args.poll_ms / 1000.0))


if __name__ == "__main__":
    main()
