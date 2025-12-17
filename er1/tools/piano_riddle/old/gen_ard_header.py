#!/usr/bin/env python3
import argparse
import pandas as pd
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_in")
    ap.add_argument("--out_h", default="key_table_goertzel.h")
    args = ap.parse_args()

    df = pd.read_csv(args.csv_in)
    df["key"] = df["key"].astype(str)

    # Extract silence freq if present
    sil = df[df["key"].str.upper() == "SILENCE"]
    silence_hz = float(sil["f0_hz"].iloc[0]) if len(sil) else 0.0

    # Remove SILENCE from candidates
    df = df[df["key"].str.upper() != "SILENCE"].copy()

    # Sort by frequency (handy)
    df = df.sort_values("f0_hz")

    with open(args.out_h, "w", encoding="utf-8") as f:
        f.write("// Auto-generated Goertzel key table (from learned_key_freqs.csv)\n")
        f.write("#pragma once\n\n")
        f.write("typedef struct { const char* key; float f0; } key_f0_t;\n\n")
        f.write(f"static const float NOISE_TONE_HZ = {silence_hz:.3f}f; // from SILENCE row\n\n")
        f.write(f"static const int KEY_COUNT = {len(df)};\n")
        f.write("static const key_f0_t KEY_TABLE[] = {\n")
        for _, r in df.iterrows():
            f.write(f'  {{"{r["key"]}", {float(r["f0_hz"]):.3f}f}},\n')
        f.write("};\n")

    print(f"Wrote {args.out_h}")
    print(f"NOISE_TONE_HZ = {silence_hz:.3f} Hz")
    print(df.head(10).to_string(index=False))

if __name__ == "__main__":
    main()
