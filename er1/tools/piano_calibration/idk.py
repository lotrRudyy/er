import re, json
import pandas as pd
import numpy as np
from pathlib import Path

LOG = "piano_calibration.log"

text = Path(LOG).read_text(errors="ignore")

# ---- CAP_END summaries ----
cap_end = []
for m in re.finditer(r'CAP_END\s+(\{.*?\})', text):
    try:
        cap_end.append(json.loads(m.group(1)))
    except Exception:
        pass

cap_df = pd.DataFrame(cap_end)
if not cap_df.empty:
    print("\n=== CAP_END summaries (last 20) ===")
    print(cap_df.tail(20).to_string(index=False))

# ---- CSV / CAL_WIN parsing ----
hdr = None
for line in text.splitlines():
    if line.startswith("type,t_ms,key_idx,key,cap_phase"):
        hdr = line.split(",")
        break
if hdr is None:
    raise SystemExit("No CSV header found (missing CSV_BEGIN/header).")

rows = []
for line in text.splitlines():
    if line.startswith("CAL_WIN,"):
        rows.append(line.split(","))

df = pd.DataFrame(rows, columns=hdr)

# numeric conversion
for c in df.columns:
    if c in ("type", "key"):
        continue
    df[c] = pd.to_numeric(df[c], errors="coerce")

def health_for_key(k):
    d = df[df.key_idx == k]
    if d.empty:
        return None

    # window-level clipping (min/max hit int16 rails)
    clip_win_pct = (((d["min"] <= -32768) | (d["max"] >= 32767)).mean() * 100.0)

    ph0 = d[d.cap_phase == 0]
    press = d[d.cap_phase.isin([1,3,5])]

    rms0 = float(np.median(ph0.rms_ac)) if len(ph0) else np.nan
    rmsp = float(np.median(press.rms_ac)) if len(press) else np.nan
    sep = (rmsp / (rms0 + 1e-9)) if np.isfinite(rmsp) and np.isfinite(rms0) else np.nan

    # rumble domination check: does any of top-3 peaks land above 500 Hz?
    high_peak_pct = float(((press["p1_hz"] > 500) | (press["p2_hz"] > 500) | (press["p3_hz"] > 500)).mean() * 100.0) if len(press) else np.nan
    p1_mode = float(press["p1_hz"].mode().iloc[0]) if len(press) and not press["p1_hz"].mode().empty else np.nan

    return {
        "key_idx": int(k),
        "key": d.key.iloc[0],
        "rows": int(len(d)),
        "clip_win_pct": clip_win_pct,
        "baseline_rms_med": rms0,
        "press_rms_med": rmsp,
        "press/silence": sep,
        "spec_log_min": float(d.spec_total_log10.min()),
        "spec_log_max": float(d.spec_total_log10.max()),
        "high_peak_pct": high_peak_pct,
        "p1_mode_hz": p1_mode,
    }

keys = sorted(df.key_idx.dropna().unique().astype(int))
rep = [health_for_key(k) for k in keys]
rep = [r for r in rep if r is not None]
rep_df = pd.DataFrame(rep)

print("\n=== Per-key health ===")
print(rep_df.to_string(index=False))

print("\n=== Flags ===")
bad = rep_df[
    (rep_df["clip_win_pct"] > 0.5) |
    (rep_df["press/silence"] < 2.0) |
    (rep_df["high_peak_pct"] < 60.0)
]
if bad.empty:
    print("No obvious red flags.")
else:
    print(bad[["key_idx","key","clip_win_pct","press/silence","high_peak_pct","p1_mode_hz"]].to_string(index=False))
