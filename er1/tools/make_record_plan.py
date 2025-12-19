#!/usr/bin/env python3
"""
make_record_plan.py

Scan existing captures dataset and generate record.txt commands to reach
a target number of reps per label for a chosen subset of keys.

Folder structure expected:
  <captures_root>/<label>/repNNN_YYYYMMDD-HHMMSS/

By default, counts a rep if the rep folder exists AND contains raw_audio_i16.raw.
(You can relax this with --count_any_folder.)

Key-range command support (firmware must support it):
  start <start_key> <end_key> <reps> <mode>

Backward compatible single-key/non-key:
  start <label> <reps> <mode>

IMPORTANT MODE BEHAVIOR (new):
- If you do NOT specify --captures_root:
    --mode clean  -> captures/
    --mode stress -> captures_stress/
This lets you maintain separate datasets per mode without relying on meta.json.

Outputs:
  record.txt        -> commands to paste into your receiver/firmware pipeline
  record_plan.csv   -> per-label counts + missing

Usage examples:
  python make_record_plan.py --target 20 --mode clean
  python make_record_plan.py --target 5  --mode stress
  python make_record_plan.py --captures_root captures_stress --target 5 --mode stress
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import List, Tuple, Optional


REP_RE = re.compile(r"^rep(\d{3})_\d{8}-\d{6}$", re.IGNORECASE)

DEFAULT_SUBSET_12 = [
    "a0", "c1", "f1", "a1",
    "c3", "d#3", "f#3", "a3",
    "c4", "e4", "g4", "c5",
]

NOTE_SEQ = ["a", "a#", "b", "c", "c#", "d", "d#", "e", "f", "f#", "g", "g#"]
NOTE_COUNT = 12


def normalize_label(lbl: str) -> str:
    return lbl.strip().lower()


def parse_key_label(lbl: str) -> Optional[Tuple[int, int]]:
    """Return (note_i, octave) if lbl is a key like a0..g#7 else None."""
    lbl = normalize_label(lbl)
    if not lbl:
        return None
    n0 = lbl[0]
    if n0 < "a" or n0 > "g":
        return None
    sharp = (len(lbl) >= 2 and lbl[1] == "#")
    oct_part = lbl[2:] if sharp else lbl[1:]
    if len(oct_part) != 1 or oct_part < "0" or oct_part > "7":
        return None
    note = (n0 + "#") if sharp else n0
    if note not in NOTE_SEQ:
        return None
    return (NOTE_SEQ.index(note), int(oct_part))


def key_index(note_i: int, octave: int) -> int:
    return octave * NOTE_COUNT + note_i


def list_rep_dirs(label_dir: Path) -> List[Path]:
    if not label_dir.exists() or not label_dir.is_dir():
        return []
    out: List[Path] = []
    for p in label_dir.iterdir():
        if p.is_dir() and REP_RE.match(p.name):
            out.append(p)

    def keyfn(p: Path):
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


def main():
    ap = argparse.ArgumentParser()

    # NEW: captures_root is optional; if omitted we choose based on mode.
    ap.add_argument("--captures_root", default=None, help="Root captures dir. If omitted: clean->captures, stress->captures_stress")
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
        help="ignore --subset and plan for every label already present under captures_root/",
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

    # Choose captures root deterministically by mode if not provided.
    if args.captures_root is None:
        captures_root = "captures" if args.mode == "clean" else "captures_stress"
    else:
        captures_root = args.captures_root

    root = Path(captures_root)
    require_raw = not args.count_any_folder

    if not root.exists() or not root.is_dir():
        # For stress it’s common that captures_stress/ doesn't exist yet.
        # Create it so planning can still proceed (counts as 0).
        root.mkdir(parents=True, exist_ok=True)

    if args.include_all_existing_labels:
        labels = sorted([p.name.lower() for p in root.iterdir() if p.is_dir()])
    else:
        labels = [normalize_label(x) for x in args.subset.split(",") if x.strip()]

    plan_rows = []
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
            "target": int(args.target),
            "need": int(need),
            "label_dir_exists": bool(label_dir.exists()),
        })

    # Build commands:
    # - For key labels with missing reps, group contiguous chromatic ranges *only when need is identical*
    #   and emit: start <start_key> <end_key> <need> <mode>
    # - Otherwise fall back to: start <label> <need> <mode>
    key_items = []
    nonkey_cmds: List[str] = []

    for r in plan_rows:
        need = int(r["need"])
        if need <= 0:
            continue
        lbl = str(r["label"])
        pk = parse_key_label(lbl)
        if pk is None:
            nonkey_cmds.append(f"start {lbl} {need} {args.mode}")
        else:
            ni, oc = pk
            key_items.append((key_index(ni, oc), lbl, need))

    key_items.sort(key=lambda t: t[0])

    range_cmds: List[str] = []
    i = 0
    while i < len(key_items):
        start_idx, start_lbl, need = key_items[i]
        end_idx = start_idx
        end_lbl = start_lbl

        j = i + 1
        while j < len(key_items):
            idx2, lbl2, need2 = key_items[j]
            if need2 != need:
                break
            if idx2 != end_idx + 1:
                break
            end_idx = idx2
            end_lbl = lbl2
            j += 1

        if start_lbl == end_lbl:
            range_cmds.append(f"start {start_lbl} {need} {args.mode}")
        else:
            range_cmds.append(f"start {start_lbl} {end_lbl} {need} {args.mode}")

        i = j

    commands = range_cmds + nonkey_cmds

    out_path = Path(args.out)
    out_path.write_text("\n".join(commands) + ("\n" if commands else ""), encoding="utf-8")

    csv_path = Path(args.csv_out)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["label", "have", "target", "need", "label_dir_exists"])
        w.writeheader()
        w.writerows(plan_rows)

    total_need = sum(int(r["need"]) for r in plan_rows)
    print(f"captures_root = {root}")
    print(f"Wrote {out_path} with {len(commands)} commands, total reps to record = {total_need}")
    print(f"Wrote {csv_path}")

    missing_labels = [r["label"] for r in plan_rows if not r["label_dir_exists"]]
    if missing_labels:
        print(f"Note: these label dirs do not exist yet (counted as 0): {', '.join(missing_labels)}")


if __name__ == "__main__":
    main()
