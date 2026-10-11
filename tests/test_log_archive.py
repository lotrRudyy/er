from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web" / "er1_dashboard"))

from log_archive import DashboardArchiveHandler, JsonlLogArchive, QueuedArchiveWriter


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class JsonlLogArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.clock = Clock(datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def record(index: int = 1) -> dict[str, object]:
        return {
            "source": "mqtt",
            "node": "lighting",
            "lv": "WRN",
            "msg": f"record {index}",
            "d": {"index": index},
        }

    def test_rotates_by_size_and_rome_day_and_closes_segments_after_restart(self) -> None:
        archive = JsonlLogArchive(
            self.root,
            max_segment_bytes=220,
            max_total_bytes=100_000,
            now_fn=self.clock.now,
        )
        self.assertTrue(archive.append(self.record(1)))
        first = archive.status()["active_segment_id"]
        self.assertTrue(archive.append(self.record(2)))
        second = archive.status()["active_segment_id"]
        self.assertNotEqual(first, second)

        self.clock.value += timedelta(days=1)
        self.assertTrue(archive.append(self.record(3)))
        third = archive.status()["active_segment_id"]
        self.assertNotEqual(second, third)

        restarted = JsonlLogArchive(self.root, max_total_bytes=100_000, now_fn=self.clock.now)
        self.assertIsNone(restarted.status()["active_segment_id"])
        with restarted.open_closed(third) as handle:
            self.assertIn(b'"msg":"record 3"', handle.read())

    def test_retention_and_size_cleanup_never_delete_active_segment(self) -> None:
        archive = JsonlLogArchive(
            self.root,
            max_segment_bytes=230,
            max_total_bytes=480,
            retention_days=30,
            now_fn=self.clock.now,
        )
        for index in range(6):
            self.assertTrue(archive.append(self.record(index)))
        status = archive.status()
        self.assertIn(status["active_segment_id"], {item["id"] for item in status["segments"]})
        self.assertLessEqual(status["total_bytes"], 480)

        active_id = status["active_segment_id"]
        active_path = self.root / f"{active_id}.jsonl"
        self.assertTrue(archive.search(text="record")["entries"])
        with archive.open_segment(active_id) as handle:
            self.assertIn(b'"source":"mqtt"', handle.read())
        old = (self.clock.value - timedelta(days=31)).timestamp()
        os.utime(active_path, (old, old))
        archive.cleanup()
        self.assertTrue(active_path.exists())
        with self.assertRaisesRegex(ValueError, "active segment"):
            archive.delete_closed(active_id)

    def test_search_is_bounded_and_segment_ids_cannot_traverse(self) -> None:
        archive = JsonlLogArchive(self.root, max_total_bytes=100_000, now_fn=self.clock.now)
        self.assertTrue(archive.append(self.record(1)))
        closed_id = archive.status()["active_segment_id"]
        restarted = JsonlLogArchive(self.root, max_total_bytes=100_000, now_fn=self.clock.now)
        path = self.root / f"{closed_id}.jsonl"
        with path.open("ab") as handle:
            handle.write(b"not-json\n\xff\n")

        result = restarted.search(text="record", limit=1)
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["segment_id"], closed_id)
        self.assertEqual(result["malformed_lines"], 2)
        with self.assertRaisesRegex(ValueError, "invalid segment"):
            restarted.open_closed("../secret")
        deleted = restarted.delete_closed(closed_id)
        self.assertEqual(deleted["id"], closed_id)
        self.assertFalse(path.exists())

    def test_concurrent_append_writes_complete_json_lines(self) -> None:
        archive = JsonlLogArchive(self.root, max_total_bytes=10_000_000, now_fn=self.clock.now)

        def writer(worker: int) -> None:
            for index in range(15):
                self.assertTrue(archive.append(self.record(worker * 100 + index)))

        threads = [threading.Thread(target=writer, args=(worker,)) for worker in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        segment_id = archive.status()["active_segment_id"]
        lines = (self.root / f"{segment_id}.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 60)
        self.assertTrue(all(json.loads(line)["schema"] == "er1.dashboard.log" for line in lines))

    def test_dashboard_handler_maps_warning_and_error_without_raising(self) -> None:
        archive = JsonlLogArchive(self.root, max_total_bytes=100_000, now_fn=self.clock.now)
        handler = DashboardArchiveHandler(archive)
        logger = logging.getLogger(f"archive-test-{id(self)}")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        logger.info("ignored")
        logger.warning("warning")
        logger.error("error")

        segment_id = archive.status()["active_segment_id"]
        entries = [json.loads(line) for line in (self.root / f"{segment_id}.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["lv"] for entry in entries], ["WRN", "ERR"])
        self.assertTrue(all(entry["source"] == "dashboard" for entry in entries))

    def test_search_scans_only_the_newest_bounded_segments(self) -> None:
        archive = JsonlLogArchive(
            self.root,
            max_segment_bytes=220,
            max_total_bytes=100_000,
            max_search_bytes=250,
            now_fn=self.clock.now,
        )
        for index in range(4):
            self.assertTrue(archive.append(self.record(index)))
        result = archive.search(text="record")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scanned_segments"], 1)
        self.assertEqual(result["entries"][0]["msg"], "record 3")

    def test_queued_writer_batches_without_blocking_the_caller(self) -> None:
        archive = JsonlLogArchive(self.root, max_total_bytes=10_000_000, now_fn=self.clock.now)
        writer = QueuedArchiveWriter(archive, capacity=200, batch_size=32)
        self.assertTrue(all(writer.append(self.record(index)) for index in range(100)))
        self.assertTrue(writer.flush())
        self.assertEqual(writer.status(), {"pending": 0, "capacity": 200, "dropped": 0, "failed_batches": 0})
        segment_id = archive.status()["active_segment_id"]
        lines = (self.root / f"{segment_id}.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 100)


if __name__ == "__main__":
    unittest.main()
