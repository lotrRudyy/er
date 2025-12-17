#!/usr/bin/env python3
import argparse, math
import pandas as pd
import numpy as np

A4 = 440.0

def note_freq_from_keyname(key: str) -> float:
    # expects like C4, C#4, Db4, A0... If your key labels differ, adapt here.
    # Map note name to semitone offset from C.
    m = {
        "C":0,"C#":1,"DB":1,"D":2,"D#":3,"EB":3,"E":4,"F":5,"F#":6,"GB":6,"G":7,"G#":8,"AB":8,"A":9,"A#":10,"BB":10,"B":11
    }
    key = key.strip().upper()
    # split note + octave
    # handle 2-char notes like C#, DB
    if len(key) < 2: raise ValueError(key)
    if key[1] in ["#", "B"]:
        note = key[:2]
        octv = int(key[2:])
    else:
        note = key[:1]
        octv = int(key[1:])
    semis_from_C0 = m[note] + 12*octv
    # C4 is MIDI 60, A4 is MIDI 69
    midi = semis_from_C0 + 12  # C0->MIDI12; so C4->60
    return A4 * (2 ** ((midi - 69) / 12))

def cents(a, b):
    if a <= 0 or b <= 0: return 1e9
    return abs(1200.0 * math.log2(a/b))

def score_window_from_peaks(row, target_f, harmonics=(1,2,3,4), cents_tol=30.0):
    # We only have top peaks in the log, so approximate Goertzel “energy”:
    # If a peak falls within cents_tol of target harmonic, add its relative magnitude.
    peaks = []
    for i in range(1, 7):
        hz = row.get(f"p{i}_hz", np.nan)
        mag = row.get(f"p{i}_mag", np.nan)
        if np.isfinite(hz) and np.isfinite(mag) and hz > 0 and mag > 0:
            peaks.append((hz, mag))
    if not peaks:
        return 0.0
    # normalize by loudness
    p1mag = peaks[0][1]
    peaks = [(hz, mag/p1mag) for hz, mag in peaks]
    s = 0.0
    for h in harmonics:
        thz = target_f * h
        best = 0.0
        for hz, rel in peaks:
            if cents(hz, thz) <= cents_tol:
                best = max(best, rel)
        # weight harmonics a bit less as they go up
        s += best * (1.0 / (h ** 0.7))
    return s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="out/cal_windows_parsed.csv from previous viz script")
    ap.add_argument("--cents_tol", type=float, default=30.0)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "key" not in df.columns:
        raise SystemExit("CSV must contain a 'key' column.")

    keys = sorted(df["key"].unique())
    key_freq = {k: note_freq_from_keyname(k) for k in keys}

    # For each window, predict which key has max score
    preds = []
    for _, row in df.iterrows():
        truek = row["key"]
        scores = []
        for k in keys:
            s = score_window_from_peaks(row, key_freq[k], cents_tol=args.cents_tol)
            scores.append(s)
        best_i = int(np.argmax(scores))
        predk = keys[best_i]
        conf = float(np.max(scores))
        preds.append((truek, predk, conf))
    res = pd.DataFrame(preds, columns=["true", "pred", "conf"])

    acc = (res["true"] == res["pred"]).mean()
    print(f"Sim-Goertzel accuracy: {acc*100:.1f}% over {len(res)} windows (cents_tol={args.cents_tol})")

    # Confusion pairs
    bad = res[res["true"] != res["pred"]]
    if len(bad) == 0:
        print("No confusions in this dataset.")
        return

    pairs = bad.groupby(["true","pred"]).size().reset_index(name="count").sort_values("count", ascending=False)
    print("\nTop confusions:")
    print(pairs.head(20).to_string(index=False))

if __name__ == "__main__":
    main()
