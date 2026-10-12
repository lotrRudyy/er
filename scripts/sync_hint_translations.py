#!/usr/bin/env python3
"""Create a local-only ER1 hint translation synchronization proposal."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = 2
LANGUAGES = ("de", "en", "it")
RIDDLES = (
    "images",
    "piano",
    "prison",
    "wheel",
    "chains",
    "tangram",
    "magnet",
    "chess",
    "knocking",
    "candles",
    "stars",
    "sissi",
)
MAX_BYTES = 96 * 1024
MAX_TIPS_PER_LANGUAGE = 20
MAX_TIP_CHARS = 2000
MAX_OFFER_AFTER_S = 86400
TIMING_FIELDS = ("offer_after_total_s", "offer_after_riddle_s")
OUTPUT_NAMES = {
    "source_hash": "hint_templates.source.sha256",
    "candidate": "hint_templates.proposed.json",
    "manifest": "manifest.json",
    "report": "report.md",
}

TRANSLATIONS = {
    ("chains", 0, "en"): "What could you do with the rope and the figures at its end?",
    ("chains", 0, "it"): "Che cosa potreste fare con la corda e le figure alla sua estremità?",
    ("chains", 1, "en"): "When you position the cube correctly, it shows you how to thread the rope.",
    ("chains", 1, "it"): "Quando posizionate correttamente il cubo, vi mostra come infilare la corda.",
    ("chains", 2, "en"): "Each tree trunk has halves of symbols that must be completed with the cube.",
    ("chains", 2, "it"): "Su ogni tronco ci sono metà di simboli che devono essere completate con il cubo.",
    ("chains", 3, "en"): (
        "If something is difficult to make out, you can trace the rope on the board. Important: always "
        "trace exactly from the first wooden disc to the last. Ignore the section from the reel to the "
        "first wooden disc."
    ),
    ("chains", 3, "it"): (
        "Se qualcosa è difficile da distinguere, potete riprodurre la corda sulla lavagna. Attenzione: "
        "sempre esattamente dal primo disco di legno all'ultimo. Ignorate il tratto dalla bobina al primo "
        "disco di legno."
    ),
    ("magnet", 0, "en"): "Have you already searched all the tables thoroughly?",
    ("magnet", 0, "it"): "Avete già controllato bene tutti i tavoli?",
    ("knocking", 1, "en"): "Then turn the discs over and count the symbols.",
    ("knocking", 1, "it"): "Poi girate i dischi e contate i simboli.",
    ("candles", 0, "en"): "How could the candles be extinguished?",
    ("candles", 0, "it"): "Come si potrebbero spegnere le candele?",
    ("stars", 0, "en"): "A good strategy is to count the stars first.",
    ("stars", 0, "it"): "Una buona strategia è contare prima le stelle.",
    ("stars", 1, "en"): "First there are 7 stars, then 13, and then 10.",
    ("stars", 1, "it"): "Prima ci sono 7 stelle, poi 13 e infine 10.",
}


class SyncError(ValueError):
    """Raised when input or output cannot be synchronized safely."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compact_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_templates(payload: Any, *, require_aligned_cardinality: bool) -> None:
    """Validate the strict v2 shape and the Dashboard's persisted-data limits."""
    if not isinstance(payload, dict):
        raise SyncError("Hint templates must be an object.")
    if payload.get("version") != VERSION or type(payload.get("version")) is not int:
        raise SyncError("Hint templates must use version 2.")

    templates = payload.get("templates")
    if not isinstance(templates, dict):
        raise SyncError("Hint templates are missing.")
    if set(templates) != set(RIDDLES):
        missing = sorted(set(RIDDLES) - set(templates))
        unknown = sorted(set(templates) - set(RIDDLES))
        raise SyncError(f"Hint templates must contain exactly 12 riddles; missing={missing}, unknown={unknown}.")

    for riddle in RIDDLES:
        translations = templates[riddle]
        if not isinstance(translations, dict) or set(translations) != set(LANGUAGES):
            found = sorted(translations) if isinstance(translations, dict) else type(translations).__name__
            raise SyncError(f"{riddle} must contain exactly the languages {LANGUAGES}; found={found}.")
        german_count = len(translations["de"]) if isinstance(translations["de"], list) else None
        for language in LANGUAGES:
            tips = translations[language]
            if not isinstance(tips, list):
                raise SyncError(f"{riddle}/{language} must be an array.")
            if len(tips) > MAX_TIPS_PER_LANGUAGE:
                raise SyncError(f"{riddle}/{language} exceeds the {MAX_TIPS_PER_LANGUAGE}-tip limit.")
            if require_aligned_cardinality and len(tips) != german_count:
                raise SyncError(
                    f"{riddle}/{language} cardinality {len(tips)} does not match German cardinality {german_count}."
                )
            for ordinal, tip in enumerate(tips, start=1):
                if not isinstance(tip, dict) or set(tip) != {"text", *TIMING_FIELDS}:
                    raise SyncError(f"{riddle}/{language}/{ordinal} must contain exactly text and both timings.")
                text = tip["text"]
                if not isinstance(text, str) or not text.strip():
                    raise SyncError(f"{riddle}/{language}/{ordinal} text must be a non-empty string.")
                if len(text) > MAX_TIP_CHARS:
                    raise SyncError(f"{riddle}/{language}/{ordinal} exceeds the {MAX_TIP_CHARS}-character limit.")
                for field in TIMING_FIELDS:
                    value = tip[field]
                    if value is not None and (type(value) is not int or not 0 <= value <= MAX_OFFER_AFTER_S):
                        raise SyncError(
                            f"{riddle}/{language}/{ordinal} {field} must be null or an integer from 0 to "
                            f"{MAX_OFFER_AFTER_S}."
                        )

    if len(_compact_bytes(payload)) > MAX_BYTES:
        raise SyncError("Hint templates exceed the Dashboard's 96 KiB encoded-size limit.")


def _cardinality_mismatches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches = []
    for riddle in RIDDLES:
        languages = payload["templates"][riddle]
        german_count = len(languages["de"])
        for language in ("en", "it"):
            if len(languages[language]) != german_count:
                mismatches.append({
                    "riddle": riddle,
                    "language": language,
                    "before": len(languages[language]),
                    "after": german_count,
                })
    return mismatches


def synchronize(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_templates(payload, require_aligned_cardinality=False)
    source = copy.deepcopy(payload)
    candidate = copy.deepcopy(payload)
    text_changes: list[dict[str, Any]] = []
    timing_changes: list[dict[str, Any]] = []
    changed_arrays: list[dict[str, Any]] = []
    used_translations: set[tuple[str, int, str]] = set()

    for riddle in RIDDLES:
        german = source["templates"][riddle]["de"]
        for language in ("en", "it"):
            before = source["templates"][riddle][language]
            after = []
            language_text_changes = 0
            language_timing_changes = 0
            for index, german_tip in enumerate(german):
                translation_key = (riddle, index, language)
                if index < len(before):
                    synchronized_tip = copy.deepcopy(before[index])
                    old_text = before[index]["text"]
                    old_timings = {field: before[index][field] for field in TIMING_FIELDS}
                else:
                    if translation_key not in TRANSLATIONS:
                        raise SyncError(f"Missing approved translation for {riddle}/{language}/{index + 1}.")
                    synchronized_tip = {
                        "text": TRANSLATIONS[translation_key],
                        "offer_after_total_s": german_tip["offer_after_total_s"],
                        "offer_after_riddle_s": german_tip["offer_after_riddle_s"],
                    }
                    old_text = None
                    old_timings = {field: None for field in TIMING_FIELDS}

                if translation_key in TRANSLATIONS:
                    synchronized_tip["text"] = TRANSLATIONS[translation_key]
                    used_translations.add(translation_key)
                if synchronized_tip["text"] != old_text:
                    text_changes.append({
                        "riddle": riddle,
                        "ordinal": index + 1,
                        "language": language,
                        "before": old_text,
                        "after": synchronized_tip["text"],
                    })
                    language_text_changes += 1

                for field in TIMING_FIELDS:
                    synchronized_tip[field] = german_tip[field]
                new_timings = {field: synchronized_tip[field] for field in TIMING_FIELDS}
                if new_timings != old_timings:
                    timing_changes.append({
                        "riddle": riddle,
                        "ordinal": index + 1,
                        "language": language,
                        "before": old_timings,
                        "after": new_timings,
                    })
                    language_timing_changes += 1
                after.append(synchronized_tip)

            candidate["templates"][riddle][language] = after
            if before != after:
                changed_arrays.append({
                    "riddle": riddle,
                    "language": language,
                    "before_count": len(before),
                    "after_count": len(after),
                    "text_changes": language_text_changes,
                    "timing_changes": language_timing_changes,
                })

    unused_translations = set(TRANSLATIONS) - used_translations
    if unused_translations:
        raise SyncError(f"Approved translations did not map to source ordinals: {sorted(unused_translations)}.")

    validate_templates(candidate, require_aligned_cardinality=True)
    if any(candidate["templates"][riddle]["de"] != source["templates"][riddle]["de"] for riddle in RIDDLES):
        raise AssertionError("German hint objects changed.")
    allowed_change_check = copy.deepcopy(source)
    for riddle in RIDDLES:
        for language in ("en", "it"):
            allowed_change_check["templates"][riddle][language] = candidate["templates"][riddle][language]
    if allowed_change_check != candidate:
        raise AssertionError("Data outside English and Italian arrays changed.")
    for riddle in ("tangram", "sissi"):
        if candidate["templates"][riddle] != {language: [] for language in LANGUAGES}:
            raise AssertionError(f"{riddle} must remain empty in all languages.")

    candles_4 = candidate["templates"]["candles"]["de"][3]
    if (candles_4["offer_after_total_s"], candles_4["offer_after_riddle_s"]) != (60, 5820):
        raise SyncError("Expected suspicious Candles 4 timings 60/5820 were not present; refusing to guess.")
    for language in LANGUAGES:
        tip = candidate["templates"]["candles"][language][3]
        if (tip["offer_after_total_s"], tip["offer_after_riddle_s"]) != (60, 5820):
            raise AssertionError(f"Candles 4 timings were not preserved for {language}.")

    details = {
        "source_cardinality_mismatches": _cardinality_mismatches(source),
        "changed_arrays": changed_arrays,
        "text_replacements": text_changes,
        "timing_updates": timing_changes,
    }
    return candidate, details


def _markdown_value(value: Any) -> str:
    if value is None:
        return "_missing_"
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_report(manifest: dict[str, Any]) -> str:
    changes = manifest["changes"]
    source = manifest["source"]
    candidate = manifest["candidate"]
    lines = [
        "# ER1 Hint Translation Synchronization",
        "",
        f"- Source: `{source['filename']}` ({source['sha256']}, {source['size_bytes']} bytes)",
        f"- Proposed: `{candidate['filename']}` ({candidate['sha256']}, {candidate['size_bytes']} bytes)",
        f"- Changed arrays: {len(changes['changed_arrays'])} (English/Italian only)",
        f"- Text replacements: {len(changes['text_replacements'])}",
        f"- Timing updates: {len(changes['timing_updates'])}",
        "- Validation: strict v2 shape and limits passed; German objects unchanged",
        "",
        "## Preserved Warning",
        "",
        "`candles/4` has suspicious timings `offer_after_total_s=60` and "
        "`offer_after_riddle_s=5820`. They were preserved exactly in German and copied unchanged to English/Italian.",
        "",
    ]
    if changes["source_cardinality_mismatches"]:
        lines.extend(["## Cardinality", ""])
        for item in changes["source_cardinality_mismatches"]:
            lines.append(
                f"- `{item['riddle']}/{item['language']}`: {item['before']} -> {item['after']} tips "
                "to match German ordinals"
            )
        lines.append("")
    lines.extend([
        "## Text Diff",
        "",
        "| Hint | Before | After |",
        "| --- | --- | --- |",
    ])
    for item in changes["text_replacements"]:
        path = f"{item['riddle']}/{item['language']}/{item['ordinal']}"
        lines.append(f"| `{path}` | {_markdown_value(item['before'])} | {_markdown_value(item['after'])} |")
    lines.extend([
        "",
        "All English and Italian timing fields now equal the corresponding German ordinal. "
        "See `manifest.json` for machine-readable per-tip timing changes.",
        "",
    ])
    return "\n".join(lines)


def _write_new(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise SyncError(f"Refusing to overwrite existing output: {path}") from exc


def run(source_path: Path, output_dir: Path, *, expected_source_sha256: str | None = None) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    if not source_path.is_file():
        raise SyncError(f"Source file does not exist: {source_path}")
    if not output_dir.is_dir():
        raise SyncError(f"Output directory does not exist: {output_dir}")

    output_paths = {name: output_dir / filename for name, filename in OUTPUT_NAMES.items()}
    collisions = [str(path) for path in output_paths.values() if path.exists()]
    if collisions:
        raise SyncError(f"Refusing to overwrite existing outputs: {', '.join(collisions)}")

    source_bytes = source_path.read_bytes()
    source_sha256 = sha256_bytes(source_bytes)
    if expected_source_sha256 is not None:
        expected = expected_source_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise SyncError("Expected source SHA256 must contain exactly 64 hexadecimal characters.")
        if source_sha256 != expected:
            raise SyncError(f"Source SHA256 mismatch: expected {expected}, got {source_sha256}.")
    try:
        source = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"Source is not valid UTF-8 JSON: {exc}") from exc

    candidate, changes = synchronize(source)
    candidate_bytes = _json_bytes(candidate)
    compact_size = len(_compact_bytes(candidate))
    if len(candidate_bytes) >= MAX_BYTES:
        raise SyncError("Pretty-printed candidate must be smaller than 96 KiB.")
    candidate_sha256 = sha256_bytes(candidate_bytes)
    manifest = {
        "schema_version": 1,
        "operation": "local_hint_translation_sync",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "path": str(source_path),
            "filename": source_path.name,
            "sha256": source_sha256,
            "expected_sha256": expected_source_sha256.lower() if expected_source_sha256 else None,
            "hash_verified": expected_source_sha256 is None or source_sha256 == expected_source_sha256.lower(),
            "size_bytes": len(source_bytes),
            "version": source["version"],
        },
        "candidate": {
            "filename": OUTPUT_NAMES["candidate"],
            "sha256": candidate_sha256,
            "size_bytes": len(candidate_bytes),
            "compact_size_bytes": compact_size,
            "maximum_size_bytes": MAX_BYTES,
        },
        "validation": {
            "riddles": list(RIDDLES),
            "languages": list(LANGUAGES),
            "aligned_cardinality": True,
            "german_objects_unchanged": True,
            "changes_limited_to_languages": ["en", "it"],
            "tangram_and_sissi_empty": True,
            "dashboard_equivalent_validation_passed": True,
        },
        "changes": changes,
        "warnings": [{
            "code": "suspicious_candles_4_timings",
            "path": "templates.candles.*[3]",
            "offer_after_total_s": 60,
            "offer_after_riddle_s": 5820,
            "preserved": True,
        }],
        "outputs": OUTPUT_NAMES,
    }
    manifest_bytes = _json_bytes(manifest)
    report_bytes = build_report(manifest).encode("utf-8")
    checksum_bytes = f"{source_sha256}  {source_path.name}\n".encode("ascii")

    _write_new(output_paths["source_hash"], checksum_bytes)
    _write_new(output_paths["candidate"], candidate_bytes)
    _write_new(output_paths["manifest"], manifest_bytes)
    _write_new(output_paths["report"], report_bytes)

    return {
        "source_sha256": source_sha256,
        "candidate_sha256": candidate_sha256,
        "output_dir": str(output_dir),
        "outputs": {name: str(path) for name, path in output_paths.items()},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="read-only version 2 source snapshot")
    parser.add_argument("output", type=Path, help="existing directory for create-new output files")
    parser.add_argument("--expected-source-sha256", help="optional required SHA256 for the source snapshot")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args.source, args.output, expected_source_sha256=args.expected_source_sha256)
    except (OSError, SyncError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
