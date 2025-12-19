#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Optional sklearn (nice-to-have for eval & PCA)
try:
    from sklearn.model_selection import GroupShuffleSplit
    from sklearn.metrics import confusion_matrix, classification_report
    from sklearn.decomposition import PCA
    SKLEARN_OK = True
except Exception:
    SKLEARN_OK = False

BEGIN_RE = re.compile(r"^-{5}\s*BEGIN\s+(\S+\.csv)\s*\(.*\)\s*-{5}\s*$")
END_RE   = re.compile(r"^-{5}\s*END\s+(\S+\.csv)\s*-{5}\s*$")
META_BEGIN_RE = re.compile(r"^#BEGIN\s+(\{.*\})\s*$")
META_END_RE   = re.compile(r"^#END\s+(\{.*\})\s*$")
META_LEGACY_RE = re.compile(r"^#meta,(.*)$")

FNAME_KEY_RE = re.compile(r"(?:/)?raw_(?:(clean|noise)_)?([A-G](?:#)?\d)_r(\d{2})_", re.IGNORECASE)
FNAME_SEG_RE = re.compile(r"(?:/)?raw_seg_(noise|silence)_", re.IGNORECASE)

REJECT_LABEL = "REJECT"
EPS = 1e-9


@dataclass
class Event:
    path: str
    meta: Dict
    df: pd.DataFrame


def _infer_from_path(path: str) -> Dict:
    out: Dict = {"path": path}
    m = FNAME_KEY_RE.search(path)
    if m:
        out["type"] = "keypress"
        if m.group(1):
            out["mode"] = m.group(1).lower()
        out["key"] = m.group(2).upper()
        out["rep"] = int(m.group(3))
        return out
    m2 = FNAME_SEG_RE.search(path)
    if m2:
        out["type"] = "segment"
        out["label"] = m2.group(1).lower()
    return out


def parse_serial_dump(txt_path: Path) -> List[Event]:
    lines = txt_path.read_text(errors="ignore").splitlines()
    events: List[Event] = []

    cur_path: Optional[str] = None
    cur_lines: List[str] = []
    cur_meta: Dict = {}
    in_block = False

    for line in lines:
        b = BEGIN_RE.match(line)
        if b:
            cur_path = b.group(1)
            cur_lines = []
            cur_meta = _infer_from_path(cur_path)
            in_block = True
            continue

        if not in_block:
            continue

        mb = META_BEGIN_RE.match(line)
        if mb:
            try:
                cur_meta.update(json.loads(mb.group(1)))
            except Exception:
                cur_meta["meta_parse_error"] = True
            continue

        ml = META_LEGACY_RE.match(line)
        if ml:
            try:
                payload = ml.group(1)
                parts = [p.strip() for p in payload.split(",") if p.strip()]
                kv = {}
                for p in parts:
                    if "=" not in p:
                        continue
                    k, v = p.split("=", 1)
                    kv[k.strip()] = v.strip()
                if "type" not in kv and "kind" in kv:
                    kv["type"] = kv["kind"]
                if "t_ms" not in kv and "ts_ms" in kv:
                    kv["t_ms"] = kv["ts_ms"]
                cur_meta.update(kv)
            except Exception:
                cur_meta["meta_parse_error"] = True
            continue

        me = META_END_RE.match(line)
        if me:
            try:
                cur_meta["end_meta"] = json.loads(me.group(1))
            except Exception:
                cur_meta["end_meta_parse_error"] = True
            continue

        e = END_RE.match(line)
        if e:
            csv_text = "\n".join([l for l in cur_lines if not l.startswith("#")]).strip()
            if not csv_text:
                in_block = False
                continue
            try:
                df = pd.read_csv(StringIO(csv_text))
            except Exception as ex:
                cur_meta["csv_parse_error"] = str(ex)
                df = pd.DataFrame()
            events.append(Event(path=cur_meta.get("path", cur_path or ""), meta=cur_meta, df=df))
            in_block = False
            continue

        cur_lines.append(line)

    return events


def norm_label(x: object) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if not s:
        return ""
    u = s.upper()
    if u.startswith("REJ"):
        return REJECT_LABEL
    return u


def _time_col(df: pd.DataFrame) -> str:
    for c in ("ms_from_event", "seg_ms", "ms"):
        if c in df.columns:
            return c
    return ""


def _get_endmeta(meta: Dict) -> Dict:
    em = meta.get("end_meta", None)
    return em if isinstance(em, dict) else {}


def _get_onset_ms(meta: Dict) -> Optional[int]:
    # prefer END meta; firmware writes onset there
    em = _get_endmeta(meta)
    v = em.get("onset_ms", None)
    if v is None:
        v = meta.get("onset_ms", None)
    try:
        if v is None:
            return None
        iv = int(v)
        return iv if iv >= 0 else None
    except Exception:
        return None


def key_to_midi_name(label: str) -> str:
    # labels are already like C4, A#3. We keep them as-is.
    return label.strip().upper()


def band_cols_32(df: pd.DataFrame) -> List[str]:
    cols = [f"b{str(i).zfill(2)}" for i in range(32)]
    if all(c in df.columns for c in cols):
        return cols
    # allow older names b0..b31 if user used that
    cols2 = [f"b{i}" for i in range(32)]
    if all(c in df.columns for c in cols2):
        return cols2
    return []


def vectorize_window(df: pd.DataFrame, meta: Dict, t0_ms: int, t1_ms: int, use_onset: bool) -> Optional[np.ndarray]:
    """
    Build a 32-dim deterministic vector:
      - mean of log-band energies over frames in window
      - per-vector standardization then L2 normalize
    """
    if df is None or df.empty:
        return None

    tcol = _time_col(df)
    if not tcol:
        return None

    bcols = band_cols_32(df)
    if not bcols:
        return None

    times = df[tcol].to_numpy(dtype=np.float32)

    onset_ms = _get_onset_ms(meta) if use_onset else None
    if onset_ms is not None:
        rel = times - float(onset_ms)
    else:
        rel = times

    mask = (rel >= float(t0_ms)) & (rel <= float(t1_ms))
    w = df.loc[mask, bcols]
    if w.empty:
        return None

    v = w.to_numpy(dtype=np.float32)
    x = np.mean(v, axis=0)

    # per-vector standardize to reduce loudness/talk differences
    mu = float(np.mean(x))
    sd = float(np.std(x))
    if sd < 1e-6:
        sd = 1.0
    x = (x - mu) / sd

    # L2 normalize for cosine similarity
    nrm = float(np.linalg.norm(x))
    if nrm < 1e-8:
        return None
    x = x / nrm
    return x.astype(np.float32)


def make_reject_windows(ev: Event, t0_ms: int, t1_ms: int, stride_ms: int) -> List[np.ndarray]:
    """
    From a segment event (noise/silence), generate many vectors by sliding window.
    Window is [t0_ms..t1_ms] relative to the segment time axis (no onset).
    """
    df = ev.df
    if df is None or df.empty:
        return []
    tcol = _time_col(df)
    if not tcol:
        return []
    bcols = band_cols_32(df)
    if not bcols:
        return []

    times = df[tcol].to_numpy(dtype=np.float32)
    if len(times) == 0:
        return []

    win_len = float(t1_ms - t0_ms)
    if win_len <= 0:
        return []

    start_min = float(np.min(times))
    start_max = float(np.max(times)) - win_len
    if start_max <= start_min:
        return []

    out: List[np.ndarray] = []
    s = start_min
    while s <= start_max + 1e-6:
        mask = (times >= s + float(t0_ms)) & (times <= s + float(t1_ms))
        w = df.loc[mask, bcols]
        if not w.empty:
            v = w.to_numpy(dtype=np.float32)
            x = np.mean(v, axis=0)
            mu = float(np.mean(x)); sd = float(np.std(x))
            if sd < 1e-6: sd = 1.0
            x = (x - mu) / sd
            nrm = float(np.linalg.norm(x))
            if nrm >= 1e-8:
                out.append((x / nrm).astype(np.float32))
        s += float(stride_ms)
    return out


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both are L2-normalized


def build_templates(train_vectors: pd.DataFrame) -> Dict[str, np.ndarray]:
    """
    train_vectors: rows with columns label and vec (np.ndarray)
    Template per label: median of vectors (then normalize).
    """
    templates: Dict[str, np.ndarray] = {}
    for lab, g in train_vectors.groupby("label"):
        mats = np.stack(g["vec"].to_list(), axis=0)  # (n,32)
        med = np.median(mats, axis=0)
        nrm = float(np.linalg.norm(med))
        if nrm < 1e-8:
            continue
        templates[lab] = (med / nrm).astype(np.float32)
    return templates


def predict_one(x: np.ndarray, templates: Dict[str, np.ndarray]) -> Tuple[str, float, float]:
    """
    Returns: best_label, best_score, margin(best-second)
    """
    best_lab = ""
    best = -1e9
    second = -1e9
    for lab, t in templates.items():
        s = cosine_sim(x, t)
        if s > best:
            second = best
            best = s
            best_lab = lab
        elif s > second:
            second = s
    margin = best - second
    return best_lab, float(best), float(margin)


def apply_thresholds(x: np.ndarray, templates: Dict[str, np.ndarray], abs_thr: float, margin_thr: float) -> Tuple[str, float, float]:
    lab, best, margin = predict_one(x, templates)
    if best < abs_thr or margin < margin_thr:
        return REJECT_LABEL, best, margin
    return lab, best, margin


def group_holdout(df: pd.DataFrame, test_size: float, seed: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df.copy(), df.copy()
    if SKLEARN_OK:
        y = df["label"].to_numpy()
        groups = df["path"].astype(str).to_numpy()
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        tr_idx, te_idx = next(gss.split(np.zeros((len(df), 1), dtype=np.int8), y, groups=groups))
        return df.iloc[tr_idx].copy(), df.iloc[te_idx].copy()

    # fallback hash split
    paths = df["path"].astype(str).to_numpy()
    h = np.array([abs(hash(p)) % 1000 for p in paths])
    thr = int(test_size * 1000)
    te_mask = h < thr
    return df[~te_mask].copy(), df[te_mask].copy()


def tune_thresholds(keys_val: pd.DataFrame, rej_vecs: List[np.ndarray], templates: Dict[str, np.ndarray],
                    target_fa: float) -> Tuple[float, float, Dict]:
    # Build grids based on key score distributions
    key_best = []
    key_margin = []
    for x in keys_val["vec"].to_list():
        _, b, m = predict_one(x, templates)
        key_best.append(b)
        key_margin.append(m)
    key_best = np.array(key_best, dtype=np.float32)
    key_margin = np.array(key_margin, dtype=np.float32)

    abs_grid = sorted(set([float(np.quantile(key_best, q)) for q in (0.05, 0.10, 0.20, 0.30, 0.40, 0.50)]))
    # margin tends to be small; include some fixed
    margin_grid = sorted(set([0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10] +
                             [float(np.quantile(key_margin, q)) for q in (0.10, 0.25, 0.50, 0.75)]))

    best_choice = None
    best_metrics = None
    best_meets = False

    # Precompute reject best+margin quickly
    rej_stats = []
    for x in rej_vecs:
        _, b, m = predict_one(x, templates)
        rej_stats.append((b, m))
    rej_stats = np.array(rej_stats, dtype=np.float32) if rej_stats else np.zeros((0,2), dtype=np.float32)

    for abs_thr in abs_grid:
        for margin_thr in margin_grid:
            # key predictions
            y_true = keys_val["label"].to_numpy()
            y_pred = []
            rej_count = 0
            for x in keys_val["vec"].to_list():
                lab, _, _ = apply_thresholds(x, templates, abs_thr, margin_thr)
                y_pred.append(lab)
                if lab == REJECT_LABEL:
                    rej_count += 1
            y_pred = np.array(y_pred, dtype=object)
            key_acc = float((y_true == y_pred).mean())
            key_rej_rate = float(rej_count / max(1, len(y_true)))

            # reject false accepts
            if len(rej_stats) > 0:
                fa = float(np.mean((rej_stats[:,0] >= abs_thr) & (rej_stats[:,1] >= margin_thr)))
            else:
                fa = 0.0

            meets = fa <= target_fa

            metrics = {
                "key_acc": key_acc,
                "key_rej_rate": key_rej_rate,
                "rej_fa": fa,
                "rej_acc": 1.0 - fa,
            }

            if best_choice is None:
                best_choice = (abs_thr, margin_thr)
                best_metrics = metrics
                best_meets = meets
                continue

            if meets and not best_meets:
                best_choice = (abs_thr, margin_thr)
                best_metrics = metrics
                best_meets = True
                continue

            if meets == best_meets:
                # maximize key_acc, then minimize key_rej_rate, then minimize fa
                if metrics["key_acc"] > best_metrics["key_acc"] + 1e-12:
                    best_choice = (abs_thr, margin_thr)
                    best_metrics = metrics
                elif abs(metrics["key_acc"] - best_metrics["key_acc"]) <= 1e-12:
                    if metrics["key_rej_rate"] < best_metrics["key_rej_rate"] - 1e-12:
                        best_choice = (abs_thr, margin_thr)
                        best_metrics = metrics
                    elif abs(metrics["key_rej_rate"] - best_metrics["key_rej_rate"]) <= 1e-12:
                        if metrics["rej_fa"] < best_metrics["rej_fa"] - 1e-12:
                            best_choice = (abs_thr, margin_thr)
                            best_metrics = metrics

    assert best_choice is not None and best_metrics is not None
    return float(best_choice[0]), float(best_choice[1]), best_metrics


def eval_and_report(name: str, df: pd.DataFrame, templates: Dict[str, np.ndarray], abs_thr: float, margin_thr: float, out_dir: Path):
    if df.empty:
        print(f"[{name}] no samples")
        return

    y_true = df["label"].map(norm_label).to_numpy()
    preds = []
    bests = []
    margins = []
    for x in df["vec"].to_list():
        lab, b, m = apply_thresholds(x, templates, abs_thr, margin_thr)
        preds.append(lab); bests.append(b); margins.append(m)
    y_pred = np.array(preds, dtype=object)
    acc = float((y_true == y_pred).mean())
    print(f"\n[{name}] samples={len(df)}  accuracy={acc:.4f}  abs_thr={abs_thr:.3f}  margin_thr={margin_thr:.3f}")

    if SKLEARN_OK:
        print(classification_report(y_true, y_pred, digits=4, zero_division=0))
        print("Confusion matrix:\n", confusion_matrix(y_true, y_pred))

    out = df[["path","label"]].copy()
    out["pred"] = y_pred
    out["best_cos"] = np.array(bests, dtype=np.float32)
    out["margin"] = np.array(margins, dtype=np.float32)
    out.to_csv(out_dir / f"pred_{name}.csv", index=False)
    print(f"[{name}] wrote -> {out_dir / f'pred_{name}.csv'}")


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--train_logs", type=Path, nargs="+", required=True)
    ap.add_argument("--reject_logs", type=Path, nargs="+", required=True)
    ap.add_argument("--testB_logs", type=Path, nargs="+", required=True)

    ap.add_argument("--t0", type=int, default=10)
    ap.add_argument("--t1", type=int, default=180)
    ap.add_argument("--agg", type=str, default="bands32",
                    help="Feature aggregation. Use 'bands32' for deterministic templates. (legacy values will map to bands32).")
    ap.add_argument("--reject_stride_ms", type=int, default=50)

    ap.add_argument("--testA_size", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=0)

    ap.add_argument("--reject_fa_target", type=float, default=0.01,
                    help="Target max false-accept rate on reject segments during threshold tuning (default 1%%).")

    ap.add_argument("--out_dir", type=Path, default=Path("out_v8"))

    args = ap.parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    agg = (args.agg or "").strip().lower()
    if agg != "bands32":
        # keep CLI compatibility: treat legacy agg as alias
        print(f"[WARN] --agg {args.agg!r} is legacy; using 'bands32' template model instead.")
        agg = "bands32"

    # Load events
    events_train: List[Event] = []
    for p in args.train_logs:
        events_train.extend(parse_serial_dump(p))
    events_rej: List[Event] = []
    for p in args.reject_logs:
        events_rej.extend(parse_serial_dump(p))
    events_testB: List[Event] = []
    for p in args.testB_logs:
        events_testB.extend(parse_serial_dump(p))

    # Build key vectors
    rows_train = []
    for ev in events_train:
        meta = ev.meta or {}
        if str(meta.get("type","")).lower() != "keypress":
            continue
        key = meta.get("key", meta.get("label", ""))
        key = key_to_midi_name(str(key))
        vec = vectorize_window(ev.df, meta, args.t0, args.t1, use_onset=True)
        if vec is None:
            continue
        rows_train.append({"path": meta.get("path", ev.path), "label": key, "vec": vec, "mode": str(meta.get("mode","")).lower()})

    df_train = pd.DataFrame(rows_train)
    if df_train.empty:
        raise SystemExit("No keypress vectors found in train logs. (Do your CSVs include b00..b31 columns?)")

    # Split clean holdout (Test A) from train logs (assume clean)
    df_tr, df_teA = group_holdout(df_train, test_size=args.testA_size, seed=args.seed)

    # Templates from training split
    templates = build_templates(df_tr)

    # Reject vectors from segment logs
    rej_vecs: List[np.ndarray] = []
    for ev in events_rej:
        meta = ev.meta or {}
        et = str(meta.get("type","")).lower()
        if et != "segment":
            continue
        rej_vecs.extend(make_reject_windows(ev, args.t0, args.t1, stride_ms=args.reject_stride_ms))

    print(f"Saved datasets -> {out_dir}")
    print("Train labels:", sorted(df_train["label"].unique()))
    print("Reject samples:", len(rej_vecs))

    # Noisy testB vectors
    rows_B = []
    for ev in events_testB:
        meta = ev.meta or {}
        if str(meta.get("type","")).lower() != "keypress":
            continue
        key = meta.get("key", meta.get("label", ""))
        key = key_to_midi_name(str(key))
        vec = vectorize_window(ev.df, meta, args.t0, args.t1, use_onset=True)
        if vec is None:
            continue
        rows_B.append({"path": meta.get("path", ev.path), "label": key, "vec": vec, "mode": str(meta.get("mode","")).lower()})
    df_testB = pd.DataFrame(rows_B)
    print("TestB labels:", sorted(df_testB["label"].unique()) if not df_testB.empty else [])

    # Tune thresholds using clean holdout (A) and reject windows
    abs_thr, margin_thr, metrics = tune_thresholds(df_teA, rej_vecs, templates, target_fa=args.reject_fa_target)
    print("\n[TUNING] thresholds:", json.dumps({"thr": {"abs_thr": abs_thr, "margin_thr": margin_thr}, "metrics": metrics}, indent=2))

    # Evaluate
    eval_and_report("testA_clean_holdout", df_teA, templates, abs_thr, margin_thr, out_dir)
    if not df_testB.empty:
        eval_and_report("testB_noisy_holdout", df_testB, templates, abs_thr, margin_thr, out_dir)

    # Reject eval: predict on reject windows
    if rej_vecs:
        rej_preds = []
        for x in rej_vecs:
            lab, _, _ = apply_thresholds(x, templates, abs_thr, margin_thr)
            rej_preds.append(lab)
        rej_fa = float(np.mean(np.array(rej_preds, dtype=object) != REJECT_LABEL))
        print(f"\n[reject_segments] samples={len(rej_vecs)}  rej_fa={rej_fa:.4f}  rej_acc={1.0-rej_fa:.4f}")

    # Export ESP32-friendly model
    # We export:
    # - band spec (n=32, f_lo/f_hi) from metadata if present
    # - templates (label->vector)
    # - thresholds
    model = {
        "ver": 1,
        "model": "template_cosine_bands32",
        "window_ms": {"t0": args.t0, "t1": args.t1, "use_onset": True},
        "thresholds": {"abs_thr": abs_thr, "margin_thr": margin_thr, "tuned_metrics": metrics},
        "labels": sorted(list(templates.keys())),
        "templates": {lab: templates[lab].tolist() for lab in templates.keys()},
        "reject_label": REJECT_LABEL,
        "bands32": {"n": 32, "cols": [f"b{str(i).zfill(2)}" for i in range(32)]},
    }
    (out_dir / "template_model_esp32.json").write_text(json.dumps(model, indent=2))
    print(f"\nWrote -> {out_dir/'template_model_esp32.json'}")

    # Optional PCA plot on training vectors
    if SKLEARN_OK and len(df_tr) >= 5:
        X = np.stack(df_tr["vec"].to_list(), axis=0)
        y = df_tr["label"].to_numpy()
        pca = PCA(n_components=2, random_state=0)
        Z = pca.fit_transform(X)
        plt.figure()
        plt.scatter(Z[:,0], Z[:,1])
        plt.title("PCA of bands32 vectors (train split)")
        plt.savefig(out_dir / "pca.png", dpi=160)
        plt.close()
        print(f"Wrote -> {out_dir/'pca.png'}")


if __name__ == "__main__":
    main()
