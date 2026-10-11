from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable
from zoneinfo import ZoneInfo


SEGMENT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$")
SCHEMA = "er1.dashboard.log"
SCHEMA_VERSION = 1
DEFAULT_SEGMENT_BYTES = 25 * 1024 * 1024
DEFAULT_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 30
MAX_SEARCH_LIMIT = 500
MAX_SEARCH_TEXT_CHARS = 256
DEFAULT_SEARCH_BYTES = 32 * 1024 * 1024


class JsonlLogArchive:
    def __init__(
        self,
        root: Path,
        *,
        max_segment_bytes: int = DEFAULT_SEGMENT_BYTES,
        max_total_bytes: int = DEFAULT_TOTAL_BYTES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_search_bytes: int = DEFAULT_SEARCH_BYTES,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if max_segment_bytes <= 0 or max_total_bytes <= 0 or retention_days <= 0 or max_search_bytes <= 0:
            raise ValueError("archive limits must be positive")
        self.root = Path(root).expanduser()
        self.max_segment_bytes = int(max_segment_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.retention_days = int(retention_days)
        self.max_search_bytes = int(max_search_bytes)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._rome = ZoneInfo("Europe/Rome")
        self._lock = threading.RLock()
        self._active_id = ""
        self._active_day = ""
        self._last_error = ""
        self._last_cleanup_at = ""
        try:
            self._ensure_root_locked()
            with self._lock:
                self._cleanup_locked(self._now())
        except Exception as exc:
            self._set_error(exc)

    @staticmethod
    def valid_segment_id(value: Any) -> bool:
        return isinstance(value, str) and bool(SEGMENT_ID_RE.fullmatch(value))

    def _now(self) -> datetime:
        value = self._now_fn()
        if not isinstance(value, datetime):
            raise TypeError("archive clock must return datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _set_error(self, exc: Exception) -> None:
        self._last_error = f"{type(exc).__name__}: {exc}"[:1000]

    def _segment_paths_locked(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        paths = []
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix != ".jsonl":
                continue
            if self.valid_segment_id(path.stem):
                paths.append(path)
        return paths

    @staticmethod
    def _fsync_path(path: Path) -> None:
        if os.name != "posix":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_directory_locked(self) -> None:
        self._fsync_path(self.root)

    def _ensure_root_locked(self) -> None:
        missing: list[Path] = []
        current = self.root
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        for path in reversed(missing):
            path.mkdir(mode=0o700)
            if os.name == "posix":
                path.chmod(0o700)
                self._fsync_path(path.parent)
        if self.root.is_dir() and os.name == "posix":
            self.root.chmod(0o700)

    def _new_segment_locked(self, now: datetime) -> Path:
        for _attempt in range(5):
            segment_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
            path = self.root / f"{segment_id}.jsonl"
            try:
                with path.open("xb"):
                    pass
            except FileExistsError:
                continue
            if os.name == "posix":
                path.chmod(0o600)
            self._fsync_directory_locked()
            self._active_id = segment_id
            self._active_day = now.astimezone(self._rome).date().isoformat()
            return path
        raise RuntimeError("could not allocate a unique archive segment")

    def _active_path_locked(self, now: datetime, line_bytes: int) -> Path:
        day = now.astimezone(self._rome).date().isoformat()
        path = self.root / f"{self._active_id}.jsonl" if self._active_id else None
        rotate = (
            path is None
            or path.is_symlink()
            or not path.is_file()
            or day != self._active_day
            or (path.stat().st_size > 0 and path.stat().st_size + line_bytes > self.max_segment_bytes)
        )
        return self._new_segment_locked(now) if rotate else path

    def append(self, record: dict[str, Any]) -> bool:
        return self.append_many([record])

    def append_many(self, records: Iterable[dict[str, Any]]) -> bool:
        try:
            now = self._now()
            encoded_records = []
            for record in records:
                envelope = dict(record)
                envelope["schema"] = SCHEMA
                envelope["version"] = SCHEMA_VERSION
                envelope.setdefault("recorded_at", now.isoformat(timespec="milliseconds").replace("+00:00", "Z"))
                encoded_records.append((json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
            if not encoded_records:
                return True
            with self._lock:
                self._ensure_root_locked()
                handles: dict[Path, BinaryIO] = {}
                try:
                    for encoded in encoded_records:
                        path = self._active_path_locked(now, len(encoded))
                        handle = handles.get(path)
                        if handle is None:
                            handle = path.open("ab", buffering=0)
                            handles[path] = handle
                        handle.write(encoded)
                    for handle in handles.values():
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    for handle in handles.values():
                        handle.close()
                self._cleanup_locked(now)
                self._last_error = ""
            return True
        except Exception as exc:
            self._set_error(exc)
            return False

    def _cleanup_locked(self, now: datetime) -> list[str]:
        paths = self._segment_paths_locked()
        active_name = f"{self._active_id}.jsonl" if self._active_id else ""
        closed = [path for path in paths if path.name != active_name]
        removed: list[str] = []
        cutoff = now.timestamp() - timedelta(days=self.retention_days).total_seconds()
        for path in sorted(closed, key=lambda item: (item.stat().st_mtime, item.name)):
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed.append(path.stem)

        paths = self._segment_paths_locked()
        total = sum(path.stat().st_size for path in paths)
        closed = [path for path in paths if path.name != active_name]
        for path in sorted(closed, key=lambda item: (item.stat().st_mtime, item.name)):
            if total <= self.max_total_bytes:
                break
            size = path.stat().st_size
            path.unlink()
            total -= size
            removed.append(path.stem)
        if removed:
            self._fsync_directory_locked()
        self._last_cleanup_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        return removed

    def cleanup(self) -> list[str]:
        try:
            with self._lock:
                removed = self._cleanup_locked(self._now())
                self._last_error = ""
                return removed
        except Exception as exc:
            self._set_error(exc)
            return []

    def _metadata_locked(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "id": path.stem,
            "bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "active": path.stem == self._active_id,
        }

    def status(self) -> dict[str, Any]:
        try:
            with self._lock:
                segments = [self._metadata_locked(path) for path in self._segment_paths_locked()]
                segments.sort(key=lambda item: (item["modified_at"], item["id"]), reverse=True)
                total = sum(item["bytes"] for item in segments)
                return {
                    "ok": not bool(self._last_error),
                    "error": self._last_error,
                    "active_segment_id": self._active_id or None,
                    "segments": segments,
                    "total_bytes": total,
                    "max_total_bytes": self.max_total_bytes,
                    "max_segment_bytes": self.max_segment_bytes,
                    "retention_days": self.retention_days,
                    "max_search_bytes": self.max_search_bytes,
                    "last_cleanup_at": self._last_cleanup_at or None,
                }
        except Exception as exc:
            self._set_error(exc)
            return {
                "ok": False,
                "error": self._last_error,
                "active_segment_id": self._active_id or None,
                "segments": [],
                "total_bytes": 0,
                "max_total_bytes": self.max_total_bytes,
                "max_segment_bytes": self.max_segment_bytes,
                "retention_days": self.retention_days,
                "max_search_bytes": self.max_search_bytes,
                "last_cleanup_at": self._last_cleanup_at or None,
            }

    def _closed_path_locked(self, segment_id: str) -> Path:
        if not self.valid_segment_id(segment_id):
            raise ValueError("invalid segment id")
        if segment_id == self._active_id:
            raise ValueError("active segment is not available for this action")
        by_id = {path.stem: path for path in self._segment_paths_locked()}
        path = by_id.get(segment_id)
        if path is None:
            raise FileNotFoundError("archive segment not found")
        return path

    def open_segment(self, segment_id: str) -> BinaryIO:
        if not self.valid_segment_id(segment_id):
            raise ValueError("invalid segment id")
        with self._lock:
            by_id = {path.stem: path for path in self._segment_paths_locked()}
            path = by_id.get(segment_id)
            if path is None:
                raise FileNotFoundError("archive segment not found")
            return path.open("rb")

    def open_closed(self, segment_id: str) -> BinaryIO:
        with self._lock:
            return self._closed_path_locked(segment_id).open("rb")

    def delete_closed(self, segment_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._closed_path_locked(segment_id)
            metadata = self._metadata_locked(path)
            path.unlink()
            self._fsync_directory_locked()
            return metadata

    def search(
        self,
        *,
        segment_ids: Iterable[str] | None = None,
        sources: set[str] | frozenset[str] | None = None,
        nodes: set[str] | frozenset[str] | None = None,
        levels: set[str] | frozenset[str] | None = None,
        text: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        needle = str(text or "").strip().casefold()
        if len(needle) > MAX_SEARCH_TEXT_CHARS:
            raise ValueError("search text is too long")
        requested = list(segment_ids) if segment_ids is not None else None
        with self._lock:
            by_id = {path.stem: path for path in self._segment_paths_locked()}
            if requested is None:
                paths = sorted(by_id.values(), key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
            else:
                if not requested or len(requested) > 100 or any(not self.valid_segment_id(item) for item in requested):
                    raise ValueError("invalid segment selection")
                missing = [item for item in requested if item not in by_id]
                if missing:
                    raise FileNotFoundError("archive segment not found")
                paths = [by_id[item] for item in requested]

            selected_paths: list[Path] = []
            scanned_bytes = 0
            truncated = False
            for path in paths:
                size = path.stat().st_size
                if size > self.max_search_bytes:
                    truncated = True
                    continue
                if scanned_bytes + size > self.max_search_bytes:
                    truncated = True
                    break
                selected_paths.append(path)
                scanned_bytes += size
                if scanned_bytes >= self.max_search_bytes:
                    truncated = len(selected_paths) < len(paths)
                    break
            paths = list(reversed(selected_paths))
            read_limits = {path: path.stat().st_size for path in paths}

            opened: list[tuple[Path, BinaryIO]] = []
            try:
                for path in paths:
                    opened.append((path, path.open("rb")))
            except Exception:
                for _path, handle in opened:
                    handle.close()
                raise

        matches: deque[dict[str, Any]] = deque(maxlen=limit)
        malformed = 0
        try:
            for path, handle in opened:
                try:
                    remaining = read_limits[path]
                    while remaining > 0:
                        encoded_line = handle.readline(remaining + 1)
                        if not encoded_line:
                            break
                        if len(encoded_line) > remaining:
                            truncated = True
                            break
                        remaining -= len(encoded_line)
                        try:
                            raw_line = encoded_line.decode("utf-8")
                            entry = json.loads(raw_line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            malformed += 1
                            continue
                        if not isinstance(entry, dict):
                            malformed += 1
                            continue
                        if sources is not None and entry.get("source") not in sources:
                            continue
                        if nodes is not None and entry.get("node") not in nodes:
                            continue
                        if levels is not None and entry.get("lv") not in levels:
                            continue
                        if needle and needle not in raw_line.casefold():
                            continue
                        result = dict(entry)
                        result["segment_id"] = path.stem
                        matches.append(result)
                finally:
                    handle.close()
        finally:
            for _path, handle in opened:
                if not handle.closed:
                    handle.close()
        return {
            "entries": list(reversed(matches)),
            "malformed_lines": malformed,
            "scanned_segments": len(paths),
            "scanned_bytes": scanned_bytes,
            "truncated": truncated,
            "limit": limit,
        }


class DashboardArchiveHandler(logging.Handler):
    _LEVELS = {
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "ERR",
    }

    def __init__(self, archive: JsonlLogArchive) -> None:
        super().__init__(level=logging.WARNING)
        self.archive = archive

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = "ERR" if record.levelno >= logging.ERROR else "WRN"
            detail: dict[str, Any] = {
                "logger": str(record.name)[:128],
                "python_level": str(record.levelname)[:32],
            }
            if record.exc_info:
                detail["exception"] = self.formatter.formatException(record.exc_info)[:8192] if self.formatter else logging.Formatter().formatException(record.exc_info)[:8192]
            self.archive.append({
                "source": "dashboard",
                "node": "Dashboard",
                "lv": level,
                "msg": record.getMessage()[:4096],
                "d": detail,
                "d_type": "python_log",
            })
        except Exception:
            pass


class QueuedArchiveWriter:
    def __init__(self, archive: JsonlLogArchive, *, capacity: int = 5000, batch_size: int = 128) -> None:
        if capacity <= 0 or batch_size <= 0:
            raise ValueError("queue limits must be positive")
        self.archive = archive
        self.batch_size = int(batch_size)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=int(capacity))
        self._lock = threading.Lock()
        self._dropped = 0
        self._failed_batches = 0
        self._thread = threading.Thread(target=self._run, name="dashboard-log-archive", daemon=True)
        self._thread.start()

    def append(self, record: dict[str, Any]) -> bool:
        queued = dict(record)
        queued.setdefault("recorded_at", datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        try:
            self._queue.put_nowait(queued)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            batch = [first]
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                if not self.archive.append_many(batch):
                    with self._lock:
                        self._failed_batches += 1
            finally:
                for _item in batch:
                    self._queue.task_done()

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "pending": self._queue.qsize(),
                "capacity": self._queue.maxsize,
                "dropped": self._dropped,
                "failed_batches": self._failed_batches,
            }

    def flush(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        return self._queue.unfinished_tasks == 0
