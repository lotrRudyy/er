from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import sync_hint_translations as sync


DEFAULT_HINTS_PATH = ROOT / "web" / "er1_dashboard" / "hint_templates.defaults.json"


class HintTranslationSyncTests(unittest.TestCase):
    def fixture(self) -> dict[str, object]:
        payload = json.loads(DEFAULT_HINTS_PATH.read_text(encoding="utf-8"))
        payload["templates"]["stars"]["de"] = [
            {
                "text": "Eine gute Strategie ist es, zuerst die Sterne zu zählen.",
                "offer_after_total_s": 3900,
                "offer_after_riddle_s": 180,
            },
            {
                "text": "Zuerst sind 7 Sterne, dann 13 und dann 10.",
                "offer_after_total_s": 4020,
                "offer_after_riddle_s": 300,
            },
        ]
        for riddle_index, riddle in enumerate(sync.RIDDLES):
            for ordinal, tip in enumerate(payload["templates"][riddle]["de"]):
                tip["offer_after_total_s"] = 60 * (riddle_index + ordinal + 1)
                tip["offer_after_riddle_s"] = 30 * (ordinal + 1)
        payload["templates"]["candles"]["de"][3]["offer_after_total_s"] = 60
        payload["templates"]["candles"]["de"][3]["offer_after_riddle_s"] = 5820
        return payload

    def test_synchronizes_only_english_and_italian_arrays(self) -> None:
        source = self.fixture()
        original = copy.deepcopy(source)
        candidate, changes = sync.synchronize(source)

        self.assertEqual(source, original)
        self.assertIsNot(candidate, source)
        for riddle in sync.RIDDLES:
            self.assertEqual(candidate["templates"][riddle]["de"], original["templates"][riddle]["de"])
            self.assertIsNot(candidate["templates"][riddle]["de"], source["templates"][riddle]["de"])
            for language in ("en", "it"):
                self.assertEqual(
                    len(candidate["templates"][riddle][language]),
                    len(candidate["templates"][riddle]["de"]),
                )
                for ordinal, tip in enumerate(candidate["templates"][riddle][language]):
                    german = candidate["templates"][riddle]["de"][ordinal]
                    self.assertEqual(tip["offer_after_total_s"], german["offer_after_total_s"])
                    self.assertEqual(tip["offer_after_riddle_s"], german["offer_after_riddle_s"])

        self.assertEqual(
            candidate["templates"]["chains"]["en"][0]["text"],
            "What could you do with the rope and the figures at its end?",
        )
        self.assertEqual(
            candidate["templates"]["stars"]["it"][1]["text"],
            "Prima ci sono 7 stelle, poi 13 e infine 10.",
        )
        self.assertEqual(
            candidate["templates"]["chess"]["en"][0]["text"],
            original["templates"]["chess"]["en"][0]["text"],
        )
        self.assertEqual(candidate["templates"]["tangram"], {"de": [], "en": [], "it": []})
        self.assertEqual(candidate["templates"]["sissi"], {"de": [], "en": [], "it": []})
        self.assertEqual(len(changes["text_replacements"]), 18)
        self.assertEqual(
            changes["source_cardinality_mismatches"],
            [
                {"riddle": "stars", "language": "en", "before": 1, "after": 2},
                {"riddle": "stars", "language": "it", "before": 1, "after": 2},
            ],
        )

    def test_strict_validation_rejects_shape_cardinality_and_limits(self) -> None:
        payload = self.fixture()
        sync.validate_templates(payload, require_aligned_cardinality=False)
        with self.assertRaisesRegex(sync.SyncError, "cardinality"):
            sync.validate_templates(payload, require_aligned_cardinality=True)

        invalid = copy.deepcopy(payload)
        invalid["version"] = 1
        with self.assertRaisesRegex(sync.SyncError, "version 2"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        del invalid["templates"]["wheel"]
        with self.assertRaisesRegex(sync.SyncError, "exactly 12 riddles"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        invalid["templates"]["wheel"]["fr"] = []
        with self.assertRaisesRegex(sync.SyncError, "exactly the languages"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        invalid["templates"]["images"]["de"][0]["text"] = "x" * 2001
        with self.assertRaisesRegex(sync.SyncError, "2000-character"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        invalid["templates"]["images"]["de"][0]["offer_after_total_s"] = True
        with self.assertRaisesRegex(sync.SyncError, "integer from 0"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        invalid["templates"]["images"]["de"] *= 21
        with self.assertRaisesRegex(sync.SyncError, "20-tip limit"):
            sync.validate_templates(invalid, require_aligned_cardinality=False)
        invalid = copy.deepcopy(payload)
        large_tip = {
            "text": "x" * 2000,
            "offer_after_total_s": None,
            "offer_after_riddle_s": None,
        }
        for riddle in sync.RIDDLES:
            for language in sync.LANGUAGES:
                invalid["templates"][riddle][language] = [copy.deepcopy(large_tip) for _ in range(20)]
        with self.assertRaisesRegex(sync.SyncError, "96 KiB"):
            sync.validate_templates(invalid, require_aligned_cardinality=True)

    def test_writes_complete_audit_set_and_refuses_overwrite(self) -> None:
        payload = self.fixture()
        source_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        expected_hash = hashlib.sha256(source_bytes).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.json"
            output_dir = root / "output"
            source_path.write_bytes(source_bytes)
            output_dir.mkdir()

            result = sync.run(source_path, output_dir, expected_source_sha256=expected_hash)
            self.assertEqual(result["source_sha256"], expected_hash)
            for filename in sync.OUTPUT_NAMES.values():
                self.assertTrue((output_dir / filename).is_file())
            manifest = json.loads((output_dir / sync.OUTPUT_NAMES["manifest"]).read_text(encoding="utf-8"))
            proposed = (output_dir / sync.OUTPUT_NAMES["candidate"]).read_bytes()
            self.assertEqual(manifest["candidate"]["sha256"], hashlib.sha256(proposed).hexdigest())
            self.assertLess(manifest["candidate"]["size_bytes"], 96 * 1024)
            self.assertEqual(manifest["warnings"][0]["offer_after_total_s"], 60)
            self.assertEqual(manifest["warnings"][0]["offer_after_riddle_s"], 5820)
            before = {path.name: path.read_bytes() for path in output_dir.iterdir()}

            with self.assertRaisesRegex(sync.SyncError, "Refusing to overwrite"):
                sync.run(source_path, output_dir, expected_source_sha256=expected_hash)
            self.assertEqual(before, {path.name: path.read_bytes() for path in output_dir.iterdir()})

    def test_expected_source_hash_is_enforced_before_generation(self) -> None:
        payload = self.fixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.json"
            output_dir = root / "output"
            source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output_dir.mkdir()
            with self.assertRaisesRegex(sync.SyncError, "SHA256 mismatch"):
                sync.run(source_path, output_dir, expected_source_sha256="0" * 64)
            self.assertEqual(list(output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
