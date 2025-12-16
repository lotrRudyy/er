#!/usr/bin/env python3
"""
Unified piano_riddle command line.

Usage:
    python -m er1.tools.piano_riddle build --log piano_calibration.log --out out_dir
"""
import argparse
from pathlib import Path
from typing import Optional

from . import export_c_header, reduce as reduce_builder, signatures


def _add_build_parser(sub):
    p = sub.add_parser("build", help="Build full signatures and ESP header from CAL_WIN log")
    p.add_argument("--log", required=True, help="Path to piano_calibration.log (serial capture)")
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument("--gate-sigma", type=float, default=4.0, help="Gate threshold in MAD sigmas above silence (default 4.0)")
    p.add_argument("--tol-cents", type=float, default=30.0, help="Peak clustering tolerance in cents (default 30)")
    p.add_argument("--min-cluster-count", type=int, default=12, help="Min observations for a peak cluster (default 12)")
    p.add_argument("--keep-clusters", type=int, default=3, help="Keep top N peak clusters per key (default 3)")
    return p

def _add_reduce_parser(sub):
    p = sub.add_parser("reduce", help="Build reduced model (f0 + harmonic ratios)")
    p.add_argument("--log", required=True, help="Path to piano_calibration.log (serial capture)")
    p.add_argument("--out", required=True, help="Output JSON path (model_reduced.json)")
    p.add_argument("--gate-sigma", type=float, default=4.0, help="Noise gate in MAD sigmas (default 4.0)")
    p.add_argument("--f0-min", type=float, default=27.0, help="Min f0 Hz (default 27)")
    p.add_argument("--f0-max", type=float, default=1200.0, help="Max f0 Hz (default 1200)")
    p.add_argument("--tol-cents-f0", type=float, default=25.0, help="Cents tolerance used in f0 harmonic scoring")
    p.add_argument("--tol-cents-h", type=float, default=45.0, help="Cents tolerance for extracting harmonic mags")
    return p

def _add_export_parser(sub):
    p = sub.add_parser("export-header", help="Export firmware header from JSON")
    p.add_argument("--compact", help="Path to model_compact.json")
    p.add_argument("--noise", help="Path to noise_model.json (used with --signatures)")
    p.add_argument("--signatures", help="Path to signatures.json (used with --noise)")
    p.add_argument("--keep-clusters", type=int, default=3, help="Cluster slots in the header (default 3)")
    p.add_argument("--out", required=True, help="Output header path (model_esp.h)")
    return p


def handle_build(args) -> None:
    log_path = Path(args.log).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    noise, sigs = signatures.build_signatures_model(
        log_path=log_path,
        gate_sigma=args.gate_sigma,
        tol_cents=args.tol_cents,
        min_cluster_count=args.min_cluster_count,
        keep_clusters=args.keep_clusters,
    )
    outputs = signatures.write_signature_outputs(out_dir, noise, sigs, args.keep_clusters)

    print("OK")
    print(f"Noise model:        {outputs['noise']}")
    print(f"Signatures (full):  {outputs['signatures']}")
    print(f"Compact model:      {outputs['compact']}")
    print(f"ESP header:         {outputs['header']}")

def handle_reduce(args) -> None:
    log_path = Path(args.log).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()

    model = reduce_builder.build_reduced_model(
        log_path=log_path,
        gate_sigma=args.gate_sigma,
        f0_min=args.f0_min,
        f0_max=args.f0_max,
        tol_cents_f0=args.tol_cents_f0,
        tol_cents_h=args.tol_cents_h,
    )
    reduce_builder.write_reduced_model(out_path, model)

    noise = model["noise"]
    print("OK")
    print(
        f"Noise rms_med={noise['rms_ac_med']:.2f} mad={noise['rms_ac_mad']:.2f} | "
        f"tot_med={noise['spec_total_med']:.2f} mad={noise['spec_total_mad']:.2f} | "
        f"flat_med={noise['flatness_med']:.4f} mad={noise['flatness_mad']:.4f}"
    )
    print(f"Wrote: {out_path}")
    print(f"Keys: {len(model['keys'])}")

def handle_export(args) -> None:
    out_path = Path(args.out).expanduser().resolve()
    compact = Path(args.compact).expanduser().resolve() if args.compact else None
    noise = Path(args.noise).expanduser().resolve() if args.noise else None
    sigs = Path(args.signatures).expanduser().resolve() if args.signatures else None

    if not compact and not (noise and sigs):
        raise SystemExit("Provide --compact or both --noise and --signatures.")

    export_c_header.export_header(
        out_path=out_path,
        keep_clusters=args.keep_clusters,
        compact_path=compact,
        noise_path=noise,
        signatures_path=sigs,
    )
    print(f"Wrote header: {out_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Piano analysis tools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_build_parser(sub).set_defaults(func=handle_build)
    _add_reduce_parser(sub).set_defaults(func=handle_reduce)
    _add_export_parser(sub).set_defaults(func=handle_export)
    return parser

def main(argv: Optional[list] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
