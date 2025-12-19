#!/usr/bin/env python3
"""
make_record_plan.py

Scan existing captures/ dataset and generate record.txt commands to reach
a target number of reps per label (default 20) for a chosen subset of keys.

Folder structure expected:
  captures/<label>/repNNN_YYYYMMDD-HHMMSS/

By default, counts a rep if the rep folder exists AND contains raw_audio_i16.raw.
(You can relax this with --count_any_folder.)

Outputs:
  record.txt        -> commands to paste into your receiver/firmware pipeline
  record_plan.csv   -> per-label counts + missing

Usage:
  python make_record_plan.py --captures_root captures --out record.txt
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


REP_RE = re.compile(r"^rep(\d{3})_\d{8}-\d{6}$", re.IGNORECASE)


DEFAULT_SUBSET_12 = [
    # low
    "a0", "c1", "f1", "a1",
    # mid
    "c3", "d#3", "f#3", "a3",
    # high
    "c4", "e4", "g4", "c5",
]


def list_rep_dirs(label_dir: Path) -> List[Path]:
    if not label_dir.exists() or not label_dir.is_dir():
        return []
    out: List[Path] = []
    for p in label_dir.iterdir():
        if p.is_dir() and REP_RE.match(p.name):
            out.append(p)
    # sort by rep number then name
    def keyfn(p: Path) -> Tuple[int, str]:
        m = REP_RE.match(p.name)
        n = int(m.group(1)) if m else 0
        return (n, p.name)
    return sorted(out, key=keyfn)


def count_reps(label_dir: Path, require_raw: bool) -> int:
    reps = list_rep_dirs(label_dir)
    if not require_raw:
        return len(reps)
    n = 0
    for rep in reps:
        if (rep / "raw_audio_i16.raw").exists():
            n += 1
    return n


def normalize_label(lbl: str) -> str:
    return lbl.strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures_root", default="captures")
    ap.add_argument("--out", default="record.txt")
    ap.add_argument("--csv_out", default="record_plan.csv")

    ap.add_argument("--target", type=int, default=20, help="target reps per label")
    ap.add_argument("--mode", default="clean", choices=["clean", "stress"], help="mode appended to start commands")

    ap.add_argument(
        "--subset",
        default=",".join(DEFAULT_SUBSET_12),
        help="comma-separated labels to plan for (default: 12-key curve subset)",
    )
    ap.add_argument(
        "--include_all_existing_labels",
        action="store_true",
        help="ignore --subset and plan for every label already present under captures/",
    )

    ap.add_argument(
        "--count_any_folder",
        action="store_true",
        help="count a rep if rep folder exists (ignore raw_audio_i16.raw presence)",
    )

    ap.add_argument(
        "--skip_if_missing_label_dir",
        action="store_true",
        help="if a label directory doesn't exist yet, skip it (default is to include it with 0 reps)",
    )

    args = ap.parse_args()

    root = Path(args.captures_root)
    require_raw = not args.count_any_folder

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"captures_root not found or not a directory: {root}")

    if args.include_all_existing_labels:
        labels = sorted([p.name.lower() for p in root.iterdir() if p.is_dir()])
    else:
        labels = [normalize_label(x) for x in args.subset.split(",") if x.strip()]

    plan_rows: List[dict] = []
    commands: List[str] = []

    for lbl in labels:
        label_dir = root / lbl
        if not label_dir.exists():
            if args.skip_if_missing_label_dir:
                continue
            have = 0
        else:
            have = count_reps(label_dir, require_raw=require_raw)

        need = max(0, int(args.target) - int(have))
        plan_rows.append({
            "label": lbl,
            "have": have,
            "target": args.target,
            "need": need,
            "label_dir_exists": label_dir.exists(),
        })

        if need > 0:
            # single command per label: start <label> <need> <mode>
            commands.append(f"start {lbl} {need} {args.mode}")

    # Write record.txt (pasteable commands)
    out_path = Path(args.out)
    out_path.write_text("\n".join(commands) + ("\n" if commands else ""), encoding="utf-8")

    # Write CSV summary
    csv_path = Path(args.csv_out)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["label", "have", "target", "need", "label_dir_exists"])
        w.writeheader()
        w.writerows(plan_rows)

    # Console summary
    total_need = sum(r["need"] for r in plan_rows)
    missing_labels = [r["label"] for r in plan_rows if not r["label_dir_exists"]]
    print(f"Wrote {out_path} with {len(commands)} commands, total reps to record = {total_need}")
    print(f"Wrote {csv_path}")
    if missing_labels:
        print(f"Note: these label dirs do not exist yet (counted as 0): {', '.join(missing_labels)}")


if __name__ == "__main__":
    main()
