#!/usr/bin/env python3
"""
Convert compact signature JSON into the firmware C header.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .signatures import (
    KeySignature,
    NoiseModel,
    build_compact_model,
    noise_from_json,
    render_esp_header_from_compact,
    signatures_from_json,
)


def load_compact(compact_path: Path) -> Tuple[NoiseModel, List[Dict[str, object]], Optional[int]]:
    data = json.loads(compact_path.read_text())
    noise = NoiseModel(**data["noise"])
    keys = data.get("keys", [])
    keep_clusters = None
    params = data.get("params")
    if isinstance(params, dict) and "keep_clusters" in params:
        try:
            keep_clusters = int(params["keep_clusters"])
        except Exception:
            keep_clusters = None
    return noise, keys, keep_clusters


def export_header(
    out_path: Path,
    keep_clusters: int = 3,
    compact_path: Optional[Path] = None,
    noise_path: Optional[Path] = None,
    signatures_path: Optional[Path] = None,
) -> None:
    if compact_path:
        noise, keys, inferred = load_compact(compact_path)
        if inferred:
            keep_clusters = inferred
    else:
        if not (noise_path and signatures_path):
            raise ValueError("Provide either --compact or both --noise and --signatures.")
        noise = noise_from_json(noise_path)
        sigs = signatures_from_json(signatures_path)
        keys = build_compact_model(noise, sigs, keep_clusters)["keys"]

    header_text = render_esp_header_from_compact(noise, keys, keep_clusters)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header_text, encoding="utf-8")
