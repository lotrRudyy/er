
from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("er1_dashboard")


def _candidate_env_paths() -> list[Path]:
    paths: list[Path] = []
    explicit = os.getenv("ER1_DASHBOARD_ENV", "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    # Most installs keep app.py and .env in the same er1_dashboard directory.
    paths.extend([
        BASE_DIR / ".env",
        BASE_DIR.parent / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "er1_dashboard" / ".env",
        Path.home() / "er1_dashboard" / ".env",
        Path.home() / "scripts" / "er1_dashboard" / ".env",
    ])
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _load_env_files() -> None:
    """Small .env loader so the Pi dashboard works without an extra dependency.

    Values from .env replace missing or empty process variables, but they do not
    override non-empty variables supplied by systemd/the shell. This fixes the
    common service-file case where ER1_WEBSITE_API_BASE exists but is blank.
    """
    for env_path in _candidate_env_paths():
        if not env_path.exists():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            current = os.environ.get(key)
            if current not in (None, ""):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value


def _normalise_url_base(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = f"http://{value}"
    return value.rstrip("/")


def website_api_config() -> tuple[str, str]:
    # Re-read the file on demand so changing .env and then restarting is safe,
    # and blank service variables can still be filled from disk.
    _load_env_files()
    base = _normalise_url_base(os.getenv("ER1_WEBSITE_API_BASE", ""))
    token = os.getenv("ER1_WEBSITE_API_TOKEN", "").strip()
    return base, token


def _read_int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except Exception:
        return fallback


def website_api_timeout(default: int = 20) -> int:
    return max(1, _read_int_env("ER1_WEBSITE_API_TIMEOUT", default))


def bookings_http_timeout() -> int:
    return max(1, _read_int_env("ER1_BOOKINGS_HTTP_TIMEOUT", 6))


def booking_source_mode() -> str:
    _load_env_files()
    # The Pi dashboard should get bookings by SSH-copying the website DB, not by calling the public/admin website API.
    mode = os.getenv("ER1_BOOKINGS_SOURCE", "ssh-copy").strip().lower()
    return mode if mode in {"ssh-copy", "ssh", "http"} else "ssh-copy"


def booking_ssh_config() -> dict[str, Any]:
    _load_env_files()
    local_default = BASE_DIR / "data" / "website_app_bookings.sqlite3"
    return {
        "host": os.getenv("ER1_WEBSITE_SSH_HOST", "192.168.0.111").strip(),
        "user": os.getenv("ER1_WEBSITE_SSH_USER", "rudyy").strip(),
        "port": str(_read_int_env("ER1_WEBSITE_SSH_PORT", 22)),
        "db_path": os.getenv("ER1_WEBSITE_DB_PATH", "/home/rudyy/escapeschenna/data/app.db").strip(),
        "app_path": os.getenv("ER1_WEBSITE_APP_PATH", "/home/rudyy/escapeschenna").strip(),
        "summary_script": os.getenv("ER1_WEBSITE_SUMMARY_SCRIPT", "scripts/send-game-summary-email.js").strip(),
        "local_db_path": Path(os.getenv("ER1_LOCAL_BOOKINGS_DB_PATH", str(local_default))).expanduser(),
        # Optional password support. When this is set, the dashboard uses Paramiko
        # instead of shelling out to ssh/scp, so password-based SSH works even when
        # openssh-client or sshpass is not installed on the Pi.
        "password": os.getenv("ER1_WEBSITE_SSH_PASSWORD", "").strip(),
        "backend": os.getenv("ER1_WEBSITE_SSH_BACKEND", "auto").strip().lower() or "auto",
        "remote_python": os.getenv("ER1_WEBSITE_REMOTE_PYTHON", "python3").strip() or "python3",
        "timeout": max(2, _read_int_env("ER1_WEBSITE_SSH_TIMEOUT", 8)),
    }


def _ssh_target(cfg: dict[str, Any]) -> str:
    return f"{cfg['user']}@{cfg['host']}"


def _ssh_base(cfg: dict[str, Any]) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={cfg['timeout']}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-p", str(cfg["port"]),
        _ssh_target(cfg),
    ]


def _ssh_backend(cfg: dict[str, Any]) -> str:
    """Return the SSH backend to use for website access.

    auto prefers Paramiko when a password is configured, otherwise it keeps the
    lightweight OpenSSH/scp path when those commands are available.
    """
    backend = str(cfg.get("backend") or "auto").strip().lower()
    if backend not in {"auto", "openssh", "paramiko"}:
        backend = "auto"
    if backend == "paramiko":
        return "paramiko"
    if backend == "openssh":
        return "openssh"
    if str(cfg.get("password") or ""):
        return "paramiko"
    if shutil.which("ssh") and shutil.which("scp"):
        return "openssh"
    return "paramiko"


def _import_paramiko():
    try:
        import paramiko  # type: ignore
        return paramiko
    except Exception as exc:
        raise RuntimeError(
            "Python package 'paramiko' is not installed. Run 'pip install -r requirements.txt' "
            "or install openssh-client and use passwordless SSH."
        ) from exc


def _paramiko_connect(cfg: dict[str, Any], *, timeout: int | None = None):
    paramiko = _import_paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    use_password = bool(str(cfg.get("password") or ""))
    try:
        client.connect(
            hostname=str(cfg["host"]),
            port=int(cfg.get("port") or 22),
            username=str(cfg["user"]),
            password=str(cfg.get("password") or "") or None,
            timeout=timeout or int(cfg.get("timeout") or 8),
            banner_timeout=timeout or int(cfg.get("timeout") or 8),
            auth_timeout=timeout or int(cfg.get("timeout") or 8),
            look_for_keys=not use_password,
            allow_agent=not use_password,
        )
    except Exception as exc:
        try:
            client.close()
        except Exception:
            pass
        raise RuntimeError(f"Die SSH-Verbindung zu {_ssh_target(cfg)} ist fehlgeschlagen: {exc}") from exc
    return client


def _paramiko_exec(client: Any, command: str, *, timeout: int, label: str) -> str:
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        try:
            stdin.close()
        except Exception:
            pass
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
    except Exception as exc:
        raise RuntimeError(f"{label} ist fehlgeschlagen: {exc}") from exc
    if exit_code != 0:
        detail = (err or out or "").strip()
        raise RuntimeError(f"{label} ist fehlgeschlagen: {detail or f'Exit-Code {exit_code}'}")
    return out


def _remote_sqlite_backup_command(cfg: dict[str, Any], remote_tmp: str) -> str:
    """Build a remote command that uses Python's sqlite3 module to back up app.db.

    This avoids requiring the sqlite3 command-line tool on the Debian website
    server and works reliably while the website is running.
    """
    py = "\n".join([
        "import os, sqlite3, sys",
        "src = sys.argv[1]",
        "dst = sys.argv[2]",
        "if not os.path.exists(src):",
        "    raise SystemExit(f'Website-Datenbank nicht gefunden: {src}')",
        "try:",
        "    os.remove(dst)",
        "except FileNotFoundError:",
        "    pass",
        "src_conn = sqlite3.connect(f'file:{src}?mode=ro', uri=True)",
        "dst_conn = sqlite3.connect(dst)",
        "try:",
        "    src_conn.backup(dst_conn)",
        "finally:",
        "    dst_conn.close()",
        "    src_conn.close()",
        "if not os.path.exists(dst) or os.path.getsize(dst) <= 0:",
        "    raise SystemExit('SQLite-Sicherung wurde nicht erstellt')",
    ])
    return " ".join([
        shlex.quote(str(cfg.get("remote_python") or "python3")),
        "-c",
        shlex.quote(py),
        shlex.quote(str(cfg["db_path"])),
        shlex.quote(remote_tmp),
    ])


def _parse_summary_script_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    # The website script should print only JSON, but parse the last JSON-looking
    # line too so harmless npm/node warnings do not break the dashboard.
    candidates = [line.strip() for line in text.splitlines() if line.strip()] or ["{}"]
    for candidate in reversed(candidates):
        if not candidate.startswith("{"):
            continue
        try:
            return json.loads(candidate)
        except Exception:
            pass
    raise RuntimeError(f"Das Website-Skript für die Spielzusammenfassung hat kein JSON zurückgegeben: {text[:500]}")


def _run_process(cmd: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        executable = cmd[0] if cmd else label
        raise RuntimeError(
            f"Der Befehl für {label} wurde auf diesem Gerät nicht gefunden ({executable}). "
            "Installiere openssh-client/scp oder setze ER1_WEBSITE_SSH_PASSWORD, damit das Paramiko-Backend verwendet werden kann."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Zeitüberschreitung bei {label}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"{label} ist fehlgeschlagen: {detail or exc}") from exc


_load_env_files()

BROKER_HOST = os.getenv("ER1_MQTT_HOST", "192.168.0.10")
BROKER_PORT = int(os.getenv("ER1_MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("ER1_DASHBOARD_CLIENT_ID", "er1_dashboard")
HINTS_PATH = BASE_DIR / "dashboard_hint_counts.json"
SELECTED_BOOKING_PATH = BASE_DIR / "dashboard_selected_booking.json"
START_ASSIGNMENT_PATH = Path(
    os.getenv("ER1_START_ASSIGNMENT_PATH", str(BASE_DIR / "data" / "start_assignment.json"))
).expanduser()
START_ASSIGNMENT_SCHEMA = "er1.dashboard.start_assignment"
START_ASSIGNMENT_VERSION = 2
# Persisted transitions: intent none -> authorized -> published -> terminal;
# candidate none -> selected -> published. MQTT is emitted only after the
# corresponding authorized/selected write passes a directory durability barrier.
START_INTENT_STATES = {"none", "authorized", "published", "terminal"}
START_CANDIDATE_STATES = {"none", "selected", "published"}
MAX_START_BOOKING_BYTES = 32768
START_CLICK_MAX_AGE_S = 5 * 60
START_CLICK_MAX_FUTURE_S = 60
_START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED = False


def _fsync_parent_directory(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except (AttributeError, OSError):
        if os.name == "posix":
            raise
        return
    try:
        os.fsync(fd)
    except OSError:
        if os.name == "posix":
            raise
    finally:
        os.close(fd)


def _ensure_dashboard_directory_durable(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(path)
        return
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if not cursor.exists() or not cursor.is_dir():
        raise NotADirectoryError(cursor)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
        _fsync_parent_directory(directory)


def _atomic_write_dashboard_json(path: Path, payload: dict[str, Any]) -> bool:
    """Replace ``path`` and return whether the final directory fsync succeeded.

    A false return is still a logical commit: ``os.replace`` already made the
    file visible. Crash durability is then fundamentally uncertain, so callers
    must keep memory aligned with the visible file instead of reporting failure.
    Exceptions are reserved for failures before the rename.
    """
    _ensure_dashboard_directory_durable(path.parent)
    _fsync_parent_directory(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            _fsync_parent_directory(path)
        except Exception:
            LOG.critical(
                "Dashboard JSON replace is visible but directory fsync failed; treating write as committed with crash-durability uncertainty path=%s",
                path,
                exc_info=True,
            )
            return False
        return True
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except OSError:
            pass


def _minimal_start_assignment(value: Any) -> dict[str, Any]:
    idle = _idle_start_assignment()
    if not isinstance(value, dict):
        return idle
    cleaned = dict(idle)
    cleaned["active"] = value.get("active") is True
    cleaned["cancel_requested"] = value.get("cancel_requested") is True
    cleaned["cancellation_durability_pending"] = value.get("cancellation_durability_pending") is True
    intent_state = str(value.get("intent_state") or "none").strip().lower()
    candidate_state = str(value.get("candidate_state") or "none").strip().lower()
    if intent_state not in START_INTENT_STATES:
        raise ValueError("invalid start intent state")
    if candidate_state not in START_CANDIDATE_STATES:
        raise ValueError("invalid booking candidate state")
    cleaned["intent_state"] = intent_state
    cleaned["candidate_state"] = candidate_state
    cleaned["start_published"] = intent_state == "published" or (
        intent_state == "terminal" and value.get("start_published") is True
    )
    string_fields = (
        ("status", 64),
        ("claim_id", 128),
        ("run_id", 256),
        ("message", 1000),
        ("requested_at", 64),
        ("booking_id", 256),
    )
    for key, limit in string_fields:
        raw = value.get(key)
        if isinstance(raw, str):
            cleaned[key] = raw[:limit]
    for key in ("reference_time", "start_clicked_at_ms", "state_revision"):
        raw = value.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)):
            cleaned[key] = raw
    raw_booking_snapshot = value.get("booking_snapshot")
    if isinstance(raw_booking_snapshot, dict):
        booking_snapshot = normalize_booking_selection(raw_booking_snapshot)
        if len(json.dumps(booking_snapshot, ensure_ascii=False).encode("utf-8")) > MAX_START_BOOKING_BYTES:
            raise ValueError("start-assignment booking snapshot exceeds its size limit")
        cleaned["booking_snapshot"] = json.loads(json.dumps(booking_snapshot, ensure_ascii=False))
        snapshot_id = str(booking_snapshot.get("id") or "").strip()
        if not snapshot_id:
            raise ValueError("start-assignment booking snapshot has no id")
        if cleaned.get("booking_id") and cleaned["booking_id"] != snapshot_id:
            raise ValueError("start-assignment booking snapshot id mismatch")
        cleaned["booking_id"] = snapshot_id
        if candidate_state == "none":
            raise ValueError("start-assignment booking snapshot lacks candidate state")
    elif candidate_state != "none":
        raise ValueError("start-assignment candidate state lacks a booking snapshot")
    if cleaned["active"] and (
        not cleaned["claim_id"]
        or not cleaned["run_id"]
        or "start_clicked_at_ms" not in cleaned
        or intent_state not in {"authorized", "published"}
    ):
        raise ValueError("active start assignment is not executable")
    if not cleaned["active"] and intent_state in {"authorized", "published"}:
        raise ValueError("inactive start assignment is not terminal")
    if candidate_state == "published" and intent_state not in {"published", "terminal"}:
        raise ValueError("published booking candidate has no published Start intent")
    if cleaned["cancellation_durability_pending"] and (
        cleaned["active"]
        or not cleaned["cancel_requested"]
        or intent_state != "terminal"
        or not cleaned["claim_id"]
        or not cleaned["run_id"]
    ):
        raise ValueError("pending cancellation is not a terminal automatic claim")
    return cleaned


def _terminal_start_assignment(status: str, message: str, *, claim_id: str = "", run_id: str = "") -> dict[str, Any]:
    return {
        **_idle_start_assignment(),
        "intent_state": "terminal",
        "status": str(status or "failed")[:64],
        "claim_id": str(claim_id or "")[:128],
        "run_id": str(run_id or "")[:256],
        "message": str(message or "")[:1000],
    }


def _quarantine_start_assignment_file(reason: str) -> bool:
    if not START_ASSIGNMENT_PATH.exists():
        return True
    quarantine = START_ASSIGNMENT_PATH.with_name(
        f"{START_ASSIGNMENT_PATH.name}.invalid.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{uuid.uuid4().hex[:8]}"
    )
    try:
        os.replace(START_ASSIGNMENT_PATH, quarantine)
        _fsync_parent_directory(quarantine)
    except Exception:
        LOG.critical(
            "Unsafe dashboard Start assignment could not be quarantined path=%s reason=%s",
            START_ASSIGNMENT_PATH,
            reason,
            exc_info=True,
        )
        return False
    LOG.error(
        "Dashboard Start assignment quarantined path=%s quarantine=%s reason=%s",
        START_ASSIGNMENT_PATH,
        quarantine,
        reason,
    )
    return True


def _invalidate_unsynced_start_assignment_file() -> None:
    try:
        with START_ASSIGNMENT_PATH.open("r+b") as handle:
            handle.seek(0)
            handle.write(b"!")
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError:
        return
    except Exception:
        LOG.critical("Unsynced Start intent could not be invalidated path=%s", START_ASSIGNMENT_PATH, exc_info=True)


def load_start_assignment() -> dict[str, Any]:
    global _START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED
    if not START_ASSIGNMENT_PATH.is_file():
        return _idle_start_assignment()
    try:
        payload = json.loads(START_ASSIGNMENT_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("assignment root is not an object")
        if (
            payload.get("schema") != START_ASSIGNMENT_SCHEMA
            or type(payload.get("version")) is not int
            or payload.get("version") != START_ASSIGNMENT_VERSION
        ):
            raise ValueError("unsupported assignment schema")
        assignment = _minimal_start_assignment(payload.get("assignment"))
    except Exception as exc:
        _START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED = True
        LOG.error("Dashboard start-assignment state is invalid and will not execute path=%s", START_ASSIGNMENT_PATH, exc_info=True)
        _quarantine_start_assignment_file(str(exc))
        return _terminal_start_assignment("quarantined", "Unsichere Startzuordnung wurde verworfen.")
    try:
        _fsync_parent_directory(START_ASSIGNMENT_PATH)
    except Exception:
        _START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED = True
        LOG.critical(
            "Dashboard Start assignment was not loaded because its directory durability barrier failed path=%s",
            START_ASSIGNMENT_PATH,
            exc_info=True,
        )
        _invalidate_unsynced_start_assignment_file()
        _quarantine_start_assignment_file("startup assignment directory barrier failed")
        failed = _terminal_start_assignment(
            "durability_failed",
            "Startzuordnung wurde wegen eines Speicherfehlers nicht ausgeführt.",
            claim_id=str(assignment.get("claim_id") or ""),
            run_id=str(assignment.get("run_id") or ""),
        )
        failed["_load_durability_failed"] = True
        return failed
    if assignment.get("active"):
        assignment["_restored_from_disk"] = True
        assignment["_directory_barrier_confirmed"] = True
    elif assignment.get("intent_state") == "terminal" and assignment.get("cancel_requested"):
        assignment["cancellation_durability_pending"] = False
    return assignment


def save_start_assignment(value: dict[str, Any]) -> bool:
    assignment = _minimal_start_assignment(value)
    if assignment == _idle_start_assignment():
        _ensure_dashboard_directory_durable(START_ASSIGNMENT_PATH.parent)
        _fsync_parent_directory(START_ASSIGNMENT_PATH)
        try:
            START_ASSIGNMENT_PATH.unlink()
        except FileNotFoundError:
            return True
        try:
            _fsync_parent_directory(START_ASSIGNMENT_PATH)
        except Exception:
            LOG.critical(
                "Start-assignment deletion is visible but directory fsync failed; treating deletion as committed with crash-durability uncertainty path=%s",
                START_ASSIGNMENT_PATH,
                exc_info=True,
            )
            return False
        return True
    return _atomic_write_dashboard_json(START_ASSIGNMENT_PATH, {
        "schema": START_ASSIGNMENT_SCHEMA,
        "version": START_ASSIGNMENT_VERSION,
        "assignment": assignment,
    })


def load_selected_booking() -> dict[str, Any]:
    try:
        if SELECTED_BOOKING_PATH.exists():
            return normalize_booking_selection(json.loads(SELECTED_BOOKING_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return dict(EMPTY_BOOKING_SELECTION)


def save_selected_booking(booking: dict[str, Any]) -> bool:
    return _atomic_write_dashboard_json(SELECTED_BOOKING_PATH, normalize_booking_selection(booking))
GAME_DB_PATH = Path(
    os.getenv(
        "ER1_GAME_DB_PATH",
        str(BASE_DIR.parent / "scripts" / "game_master" / "data" / "game_master.sqlite3")
    )
).resolve()

REMOVED_GAMES_DIR = GAME_DB_PATH.parent / "removed"
REMOVED_GAME_DB_PATH = REMOVED_GAMES_DIR / GAME_DB_PATH.name
RUN_JSON_DIR = GAME_DB_PATH.parent / "game_runs"

TEST_BOOKING_DEFAULT = {
    "id": "__test__",
    "kind": "test",
    "bookingCode": "",
    "date": "",
    "slot": "",
    "players": 2,
    "customerEmail": "rudolf.dosser@gmail.com",
    "customerName": "Testbuchung",
    "label": "Testbuchung",
}

EMPTY_BOOKING_SELECTION = {
    "id": "__empty__",
    "kind": "empty",
    "bookingCode": "",
    "date": "",
    "slot": "",
    "players": 0,
    "customerEmail": "",
    "customerName": "",
    "language": "de",
    "label": "Keine Buchung ausgewählt",
}

try:
    EUROPE_ROME_TZ = ZoneInfo("Europe/Rome")
except ZoneInfoNotFoundError:
    # Debian has system tzdata; this fallback keeps EU DST behavior on lean installs.
    EUROPE_ROME_TZ = None


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(str(value).strip() or str(fallback)))
    except Exception:
        return int(fallback)


def normalize_hint_language(value: Any) -> str:
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", str(value or "").strip().lower().replace("_", "-"))
        if not unicodedata.combining(char)
    )
    base = normalized.split("-", 1)[0]
    if base == "de" or normalized in {"deutsch", "german", "tedesco"}:
        return "de"
    if base == "en" or normalized in {"english", "englisch", "inglese"}:
        return "en"
    if base == "it" or normalized in {"italiano", "italian", "italienisch"}:
        return "it"
    return "de"


def normalize_booking_selection(raw: Any | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    raw_id = str(raw.get("id") or raw.get("bookingCode") or raw.get("booking_code") or "").strip()
    is_empty = kind == "empty" or raw_id == "__empty__"
    is_test = kind == "test" or raw_id == "__test__"
    players = 0 if is_empty else max(1, _safe_int(raw.get("players", raw.get("players_count", raw.get("playerCount", 2 if is_test else 1))), 2 if is_test else 1))
    booking_code = str(raw.get("bookingCode") or raw.get("booking_code") or "").strip()
    email = str(raw.get("customerEmail") or raw.get("customer_email") or raw.get("email") or "").strip()
    if is_test and not email:
        email = TEST_BOOKING_DEFAULT["customerEmail"]
    label = str(raw.get("label") or "").strip()
    if not label:
        if is_empty:
            label = "Keine Buchung ausgewählt"
        elif is_test:
            label = "Testbuchung"
        else:
            label = " · ".join(part for part in [str(raw.get("date") or "").strip(), str(raw.get("slot") or "").strip(), f"{players}P", email or booking_code] if part)
    return {
        "id": "__empty__" if is_empty else ("__test__" if is_test else str(raw.get("id") or booking_code or f"{raw.get('date','')}-{raw.get('slot','')}-{email}").strip()),
        "kind": "empty" if is_empty else ("test" if is_test else "booking"),
        "bookingCode": "" if is_empty or is_test else booking_code,
        "date": str(raw.get("date") or "").strip(),
        "slot": str(raw.get("slot") or "").strip(),
        "players": players,
        "customerEmail": email,
        "customerName": str(raw.get("customerName") or raw.get("customer_name") or raw.get("name") or "").strip(),
        "language": normalize_hint_language(raw.get("language")),
        "bookingStatus": str(raw.get("bookingStatus") or raw.get("booking_status") or "").strip(),
        "paymentStatus": str(raw.get("paymentStatus") or raw.get("payment_status") or "").strip(),
        "label": label,
    }


def _normalize_existing_games_db_schema(db_path: Path) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if "games" not in tables:
            return
        existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()]
        needs_rebuild = (
            "player_names_json" in existing_columns
            or "player_names" in existing_columns
            or ("players_count" not in existing_columns and "player_count" in existing_columns)
        )
        if not needs_rebuild:
            if "players_count" not in existing_columns:
                conn.execute("ALTER TABLE games ADD COLUMN players_count INTEGER NOT NULL DEFAULT 0")
            return

        players_expr = "COALESCE(players_count, player_count, 0)" if "player_count" in existing_columns else "COALESCE(players_count, 0)"
        hint_expr = "COALESCE(hint_count, 0)" if "hint_count" in existing_columns else "0"
        leaderboard_expr = "leaderboard_code" if "leaderboard_code" in existing_columns else "NULL"

        conn.executescript(f"""
            ALTER TABLE games RENAME TO games_old;

            CREATE TABLE games (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_s REAL,
                players_count INTEGER NOT NULL DEFAULT 0,
                hint_count INTEGER NOT NULL DEFAULT 0,
                leaderboard_code TEXT
            );

            INSERT INTO games (id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code)
            SELECT id, date, started_at, ended_at, duration_s, {players_expr}, {hint_expr}, {leaderboard_expr}
            FROM games_old;

            DROP TABLE games_old;
        """)



def _ensure_game_riddles_outcome_columns(db_path: Path) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if "game_riddles" not in tables:
            return
        columns = {row[1] for row in conn.execute("PRAGMA table_info(game_riddles)").fetchall()}
        if "skipped" not in columns:
            conn.execute("ALTER TABLE game_riddles ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0")
        if "not_solved" not in columns:
            conn.execute("ALTER TABLE game_riddles ADD COLUMN not_solved INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE game_riddles SET skipped = 0 WHERE skipped IS NULL")
        conn.execute("UPDATE game_riddles SET not_solved = 0 WHERE not_solved IS NULL")
        conn.execute("UPDATE game_riddles SET not_solved = 0 WHERE skipped = 1 AND not_solved = 1")


_normalize_existing_games_db_schema(GAME_DB_PATH)
_ensure_game_riddles_outcome_columns(GAME_DB_PATH)

TOPIC_GAME_STATE = "game/state"
TOPIC_DASHBOARD_STATE = "game/dashboard_state"
TOPIC_GAME_CMD = "game/cmd"
TOPIC_LIGHTING_CMD = "lighting/cmd"
TOPIC_MAGLOCK_CMD = "maglock/cmd"
TOPIC_STAR_SKY_CMD = "star_sky/cmd"

PHASE_META: dict[int, dict[str, Any]] = {
    0: {"name": "standby", "active": (), "solved": ()},
    1: {"name": "maintenance", "active": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider", "sissi"), "solved": ()},
    2: {"name": "prepare", "active": (), "solved": ()},
    3: {"name": "start", "active": ("images",), "solved": ()},
    4: {"name": "piano", "active": ("piano",), "solved": ("images",)},
    5: {"name": "prison", "active": ("open_prison",), "solved": ("images", "piano")},
    6: {"name": "wheel", "active": ("mount_wheel",), "solved": ("images", "piano", "open_prison")},
    7: {"name": "rope", "active": ("rope_paths",), "solved": ("images", "piano", "open_prison", "mount_wheel")},
    8: {"name": "tangram_magnet", "active": ("tangram", "magnet"), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths")},
    9: {"name": "chess", "active": ("chess",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet")},
    10: {"name": "knocking", "active": ("knocking", "candles"), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess")},
    11: {"name": "candles", "active": ("candles",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking")},
    12: {"name": "stars", "active": ("star_slider",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles")},
    13: {"name": "sissi", "active": ("sissi",), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider")},
    14: {"name": "finished", "active": (), "solved": ("images", "piano", "open_prison", "mount_wheel", "rope_paths", "tangram", "magnet", "chess", "knocking", "candles", "star_slider", "sissi")},
}

RIDDLES = [
    {"id": "images", "label": "Images", "node_id": "images_piano", "manual": False},
    {"id": "piano", "label": "Piano", "node_id": "images_piano", "manual": False},
    {"id": "open_prison", "label": "Prison", "node_id": None, "manual": True},
    {"id": "mount_wheel", "label": "Wheel", "node_id": None, "manual": True},
    {"id": "rope_paths", "label": "Rope", "node_id": None, "manual": True},
    {"id": "tangram", "label": "Tangram", "node_id": None, "manual": True},
    {"id": "magnet", "label": "Magnet", "node_id": None, "manual": True},
    {"id": "chess", "label": "Chess", "node_id": "chess", "manual": False},
    {"id": "knocking", "label": "Knocking", "node_id": "knocking", "manual": False},
    {"id": "candles", "label": "Candles", "node_id": "candles", "manual": False},
    {"id": "star_slider", "label": "Stars", "node_id": "star_slider", "manual": False},
    {"id": "sissi", "label": "Sissi", "node_id": None, "manual": True},
]

NODE_LABELS = [
    ("lighting", "Lighting Controller"),
    ("maglock", "Maglock Controller"),
    ("images_piano", "Images / Piano"),
    ("chess", "Chess"),
    ("knocking", "Knocking"),
    ("candles", "Candles"),
    ("star_slider", "Star Slider"),
    ("star_sky", "Star Sky"),
]

LOCKS = [
    {"id": "r2", "label": "r2", "kind": "toggle"},
    {"id": "r3", "label": "r3", "kind": "toggle"},
    {"id": "images", "label": "images", "kind": "pulse"},
    {"id": "knocking", "label": "knocking", "kind": "pulse"},
    {"id": "slider", "label": "slider", "kind": "pulse"},
]

LIGHT_GROUPS = {
    "entrance": {"label": "entrance", "lights": ["torch_stiege"], "dimmable": False},
    "r1": {"label": "r1", "lights": ["r1_stuen", "r1_bild"], "dimmable": False},
    "r2_main": {"label": "r2 chess + kostn", "lights": ["r2_chess", "r2_schronk"], "dimmable": False},
    "r2_torch": {"label": "r2 torch", "lights": ["torch_r2"], "dimmable": False},
    "r3_main": {"label": "r3 slider + cage", "lights": ["r3_slider", "r3_cage"], "dimmable": True},
    "r3_torch": {"label": "r2r3 torch", "lights": ["torch_r2r3"], "dimmable": False},
    "star_sky": {"label": "star sky", "lights": ["r3_uv"], "dimmable": False, "special": "star_sky"},
}

LIGHT_NAME_BY_ID = {
    "1": "r2_chess",
    "2": "r2_schronk",
    "3": "r1_bild",
    "4": "r1_stuen",
    "5": "r3_slider",
    "6": "r3_cage",
    "7": "torch_stiege",
    "8": "torch_r2r3",
    "9": "torch_r2",
    "10": "r3_uv",
}

LOG_NODE_LABELS = {
    "lighting": "Lighting Controller",
    "maglock": "Maglock Controller",
    "images_piano": "Images / Piano",
    "chess": "Chess",
    "knocking": "Knocking",
    "candles": "Candles",
    "star_slider": "Star Slider",
    "star_sky": "Star Sky",
    "stop_timer": "Stop Timer (optional / possibly offline)",
}
LOG_NODE_IDS = tuple(LOG_NODE_LABELS)
LOG_NODE_ID_SET = frozenset(LOG_NODE_IDS)
LOG_LEVELS = ("DBG", "INF", "WRN", "ERR")
LOG_LEVEL_SET = frozenset(LOG_LEVELS)
LOG_STREAM_ID = uuid.uuid4().hex
MAX_LOG_PAYLOAD_BYTES = 16 * 1024
MAX_LOG_MESSAGE_CHARS = 2048
MAX_LOG_DETAIL_STRING_CHARS = 2048
MAX_LOG_DETAIL_KEY_CHARS = 128
MAX_LOG_DETAIL_ITEMS = 40
MAX_LOG_DETAIL_DEPTH = 6
MAX_LOG_DETAIL_NODES = 300
LOG_BUFFER_CAPACITY = 1500
LOG_API_DEFAULT_LIMIT = 200
LOG_API_MAX_LIMIT = 500


def _bounded_log_text(value: str, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 3)] + "..."


def _sanitize_log_detail(value: Any, *, _depth: int = 0, _budget: list[int] | None = None) -> Any:
    if _budget is None:
        _budget = [MAX_LOG_DETAIL_NODES]
    if _budget[0] <= 0:
        return "[truncated]"
    _budget[0] -= 1

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _bounded_log_text(value, MAX_LOG_DETAIL_STRING_CHARS)
    if _depth >= MAX_LOG_DETAIL_DEPTH:
        return "[max depth]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_LOG_DETAIL_ITEMS or _budget[0] <= 0:
                break
            cleaned_key = _bounded_log_text(str(key), MAX_LOG_DETAIL_KEY_CHARS)
            if cleaned_key in cleaned:
                continue
            cleaned[cleaned_key] = _sanitize_log_detail(item, _depth=_depth + 1, _budget=_budget)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_log_detail(item, _depth=_depth + 1, _budget=_budget)
            for item in value[:MAX_LOG_DETAIL_ITEMS]
            if _budget[0] > 0
        ]
    return _bounded_log_text(str(value), MAX_LOG_DETAIL_STRING_CHARS)


def _sanitize_log_scalar(value: Any, limit: int) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite log metadata")
        return value
    if isinstance(value, str):
        return _bounded_log_text(value, limit)
    raise ValueError("structured log metadata")


def _reject_log_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def parse_node_log(topic: Any, payload: Any) -> dict[str, Any] | None:
    parts = str(topic or "").split("/")
    if len(parts) != 2 or parts[1] != "log" or parts[0] not in LOG_NODE_ID_SET:
        return None
    if not isinstance(payload, (bytes, bytearray)) or not payload or len(payload) > MAX_LOG_PAYLOAD_BYTES:
        return None
    try:
        data = json.loads(bytes(payload).decode("utf-8"), parse_constant=_reject_log_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None

    level = data.get("lv")
    message = data.get("msg")
    detail = data.get("d", {})
    if not isinstance(level, str) or level not in LOG_LEVEL_SET:
        return None
    if not isinstance(message, str):
        return None
    if "time_valid" in data and not isinstance(data["time_valid"], bool):
        return None
    try:
        device_type = _sanitize_log_scalar(data.get("t"), 128)
        device_timestamp = _sanitize_log_scalar(data.get("ts"), 128)
        detail_type = _sanitize_log_scalar(data.get("d_type"), 32)
    except ValueError:
        return None
    return {
        "node": parts[0],
        "t": device_type,
        "ts": device_timestamp,
        "time_valid": data.get("time_valid") if "time_valid" in data else None,
        "lv": level,
        "msg": _bounded_log_text(message, MAX_LOG_MESSAGE_CHARS),
        "d": _sanitize_log_detail(detail),
        "d_type": detail_type,
    }


class NodeLogBuffer:
    def __init__(self, capacity: int = LOG_BUFFER_CAPACITY) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("log buffer capacity must be positive")
        self.capacity = capacity
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._next_seq = 1
        self._dropped = 0

    def append(self, entry: dict[str, Any], *, received_at: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if received_at is None else float(received_at)
        if not math.isfinite(timestamp):
            timestamp = time.time()
        received = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self._lock:
            if len(self._entries) == self.capacity:
                self._dropped += 1
            stored = dict(entry)
            stored["seq"] = self._next_seq
            stored["received_at"] = received
            self._next_seq += 1
            self._entries.append(stored)
            return dict(stored)

    def query(
        self,
        *,
        after: int = 0,
        nodes: set[str] | frozenset[str] | None = None,
        levels: set[str] | frozenset[str] | None = None,
        limit: int = LOG_API_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= LOG_API_MAX_LIMIT:
            raise ValueError("limit is outside the allowed range")
        with self._lock:
            entries = list(self._entries)
            dropped = self._dropped
            newest_seq = self._next_seq - 1

        oldest_seq = entries[0]["seq"] if entries else None
        gap_reason = ""
        if oldest_seq is not None and after < oldest_seq - 1:
            gap_reason = "buffer_rollover"
        elif after > newest_seq:
            gap_reason = "cursor_ahead"
        reset = bool(gap_reason)
        effective_after = (oldest_seq - 1 if oldest_seq is not None else 0) if reset else after
        matched = [
            entry
            for entry in entries
            if entry["seq"] > effective_after
            and (nodes is None or entry["node"] in nodes)
            and (levels is None or entry["lv"] in levels)
        ]
        selected = matched[:limit]
        has_more = len(matched) > len(selected)
        next_after = selected[-1]["seq"] if has_more and selected else newest_seq
        return {
            "entries": [dict(entry) for entry in selected],
            "stream_id": LOG_STREAM_ID,
            "next_after": next_after,
            "oldest_seq": oldest_seq,
            "newest_seq": newest_seq,
            "reset": reset,
            "gap": reset,
            "gap_reason": gap_reason or None,
            "dropped": dropped,
            "buffer_size": len(entries),
            "buffer_capacity": self.capacity,
            "has_more": has_more,
        }


def _query_arg_values(args: Any, key: str) -> list[str]:
    if hasattr(args, "getlist"):
        values = list(args.getlist(key))
    else:
        value = args.get(key) if hasattr(args, "get") else None
        values = value if isinstance(value, list) else ([] if value is None else [value])
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"{key} must be text")
    return values


def _parse_log_filter(values: list[str], *, name: str, allowed: frozenset[str]) -> frozenset[str] | None:
    if not values:
        return None
    tokens: list[str] = []
    for value in values:
        pieces = value.split(",")
        if any(not piece.strip() for piece in pieces):
            raise ValueError(f"{name} contains an empty value")
        tokens.extend(piece.strip() for piece in pieces)
    unknown = sorted(set(tokens) - allowed)
    if unknown:
        raise ValueError(f"unknown {name}: {', '.join(unknown)}")
    return frozenset(tokens)


def parse_logs_query(args: Any) -> dict[str, Any]:
    allowed_keys = {"after", "node", "nodes", "level", "levels", "limit"}
    unknown_keys = sorted(set(args.keys()) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"unknown query parameter: {', '.join(unknown_keys)}")

    after_values = _query_arg_values(args, "after")
    limit_values = _query_arg_values(args, "limit")
    if len(after_values) > 1 or len(limit_values) > 1:
        raise ValueError("after and limit may only be provided once")
    after_text = after_values[0] if after_values else "0"
    limit_text = limit_values[0] if limit_values else str(LOG_API_DEFAULT_LIMIT)
    if not re.fullmatch(r"0|[1-9][0-9]*", after_text):
        raise ValueError("after must be a non-negative integer")
    if not re.fullmatch(r"[1-9][0-9]*", limit_text):
        raise ValueError("limit must be a positive integer")
    after = int(after_text)
    limit = int(limit_text)
    if after > 9_223_372_036_854_775_807:
        raise ValueError("after is too large")
    if limit > LOG_API_MAX_LIMIT:
        raise ValueError(f"limit must not exceed {LOG_API_MAX_LIMIT}")

    nodes = _parse_log_filter(
        _query_arg_values(args, "node") + _query_arg_values(args, "nodes"),
        name="node",
        allowed=LOG_NODE_ID_SET,
    )
    levels = _parse_log_filter(
        _query_arg_values(args, "level") + _query_arg_values(args, "levels"),
        name="level",
        allowed=LOG_LEVEL_SET,
    )
    return {"after": after, "nodes": nodes, "levels": levels, "limit": limit}


def validate_log_level_request(data: Any) -> tuple[str, str]:
    if not isinstance(data, dict) or set(data) != {"node", "level"}:
        raise ValueError("Genau node und level sind erforderlich.")
    node = data.get("node")
    level = data.get("level")
    if not isinstance(node, str) or node not in LOG_NODE_ID_SET:
        raise ValueError("Unbekannter Diagnose-Knoten.")
    if not isinstance(level, str) or level not in LOG_LEVEL_SET:
        raise ValueError("Die Log-Stufe muss DBG, INF, WRN oder ERR sein.")
    return node, level


def load_hint_store() -> dict[str, int]:
    if not HINTS_PATH.exists():
        return {}
    try:
        raw = json.loads(HINTS_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(value, list):
            out[name] = len(value)
        elif isinstance(value, dict):
            out[name] = max(0, int(value.get("count", 0) or 0))
        else:
            try:
                out[name] = max(0, int(value or 0))
            except Exception:
                out[name] = 0
    return out


def save_hint_store(data: dict[str, int]) -> None:
    cleaned = {str(k): max(0, int(v or 0)) for k, v in (data or {}).items()}
    HINTS_PATH.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2))


def pretty_phase_name(name: str) -> str:
    text = str(name or "").replace("_", " ").strip()
    if not text:
        return ""
    parts = text.split()
    return " ".join(p.capitalize() for p in parts)


def _idle_start_assignment() -> dict[str, Any]:
    return {
        "active": False,
        "cancel_requested": False,
        "cancellation_durability_pending": False,
        "intent_state": "none",
        "candidate_state": "none",
        "start_published": False,
        "status": "idle",
        "claim_id": "",
        "run_id": "",
        "message": "",
    }


@dataclass
class DashboardStore:
    lock: threading.RLock = field(default_factory=threading.RLock)
    game_state: dict[str, Any] = field(default_factory=lambda: {"phase": 0})
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    riddle_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_last_hb: dict[str, float] = field(default_factory=dict)
    locks: dict[str, dict[str, Any]] = field(default_factory=dict)
    lights: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_hint_counts: dict[str, int] = field(default_factory=load_hint_store)
    local_players_count_override: int | None = None
    selected_booking: dict[str, Any] = field(default_factory=load_selected_booking)
    game_state_revision: int = 0
    start_assignment: dict[str, Any] = field(default_factory=load_start_assignment)
    persistence_degraded: bool = field(default_factory=lambda: _START_ASSIGNMENT_LOAD_DURABILITY_DEGRADED)
    last_start_assignment_directory_synced: bool = True

    def _reset_selected_booking_locked(self) -> None:
        selected = normalize_booking_selection(EMPTY_BOOKING_SELECTION)
        try:
            directory_synced = save_selected_booking(selected)
        except Exception:
            self.persistence_degraded = True
            raise
        self.selected_booking = selected
        self.local_players_count_override = None
        if not directory_synced:
            self.persistence_degraded = True

    def _reset_start_assignment_locked(self) -> None:
        assignment = _idle_start_assignment()
        try:
            directory_synced = save_start_assignment(assignment)
        except Exception:
            self.persistence_degraded = True
            raise
        self.start_assignment = assignment
        self.last_start_assignment_directory_synced = directory_synced
        if not directory_synced:
            self.persistence_degraded = True

    def _set_start_assignment_locked(self, value: dict[str, Any]) -> bool:
        assignment = dict(value)
        try:
            directory_synced = save_start_assignment(assignment)
        except Exception:
            self.persistence_degraded = True
            raise
        self.start_assignment = assignment
        self.last_start_assignment_directory_synced = directory_synced
        if not directory_synced:
            self.persistence_degraded = True
        return directory_synced

    def claim_start_assignment(self, reference_time: datetime) -> tuple[dict[str, Any], bool]:
        with self.lock:
            if self.start_assignment.get("active"):
                return json.loads(json.dumps(self.start_assignment)), False
            try:
                phase = int(self.game_state.get("phase", 0) or 0)
            except Exception:
                phase = 0
            if phase != 2:
                raise ValueError("Das Spiel kann nur aus der Phase Vorbereitung gestartet werden.")
            run = self.game_state.get("run") if isinstance(self.game_state.get("run"), dict) else {}
            prepared_run_id = str(run.get("run_id") or run.get("id") or self.game_state.get("run_id") or "").strip()
            if not prepared_run_id:
                raise ValueError("Der vorbereitete Lauf wurde noch nicht vom Game Master bestätigt.")
            claim = {
                "active": True,
                "cancel_requested": False,
                "cancellation_durability_pending": False,
                "intent_state": "authorized",
                "candidate_state": "none",
                "start_published": False,
                "status": "start_intent",
                "claim_id": uuid.uuid4().hex,
                "run_id": prepared_run_id,
                "message": "Startbefehl wird an den Game Master gesendet.",
                "requested_at": reference_time.isoformat(timespec="seconds"),
                "reference_time": reference_time.timestamp(),
                "start_clicked_at_ms": int(round(reference_time.timestamp() * 1000)),
                "state_revision": self.game_state_revision,
            }
            directory_synced = self._set_start_assignment_locked(claim)
            if directory_synced:
                self.start_assignment["_intent_durable_runtime"] = True
            else:
                _invalidate_unsynced_start_assignment_file()
                _quarantine_start_assignment_file("initial Start intent directory sync failed")
                self.start_assignment = _terminal_start_assignment(
                    "durability_failed",
                    "Startabsicht wurde nicht dauerhaft bestätigt und wird nicht ausgeführt.",
                    claim_id=claim["claim_id"],
                    run_id=claim["run_id"],
                )
                self.last_start_assignment_directory_synced = False
            result = json.loads(json.dumps(self.start_assignment))
            result["_directory_synced"] = directory_synced
            return result, True

    def update_start_assignment(self, claim_id: str, *, require_active: bool = True, **updates: Any) -> bool:
        with self.lock:
            if str(self.start_assignment.get("claim_id") or "") != str(claim_id or ""):
                return False
            if require_active and (not self.start_assignment.get("active") or self.start_assignment.get("cancel_requested")):
                return False
            return self._set_start_assignment_locked({**self.start_assignment, **updates})

    def get_start_assignment(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.start_assignment))

    def update_game_state(self, payload: dict[str, Any], *, merge: bool = False) -> None:
        with self.lock:
            try:
                previous_phase = int(self.game_state.get("phase", 0) or 0)
            except Exception:
                previous_phase = 0
            previous_run = self.game_state.get("run") if isinstance(self.game_state.get("run"), dict) else {}
            previous_run_id = str(previous_run.get("run_id") or previous_run.get("id") or self.game_state.get("run_id") or "").strip()
            incoming = dict(payload or {})
            if merge:
                next_payload = dict(self.game_state or {})
                next_payload.update(incoming)
                # ``game/state`` is intentionally firmware-safe and no longer carries
                # the dashboard run object. Preserve the richer dashboard state for
                # in-game phases, but clear stale run data outside an active/prepared run.
                try:
                    incoming_phase = int(incoming.get("phase", next_payload.get("phase", 0)) or 0)
                except Exception:
                    incoming_phase = 0
                if "run" not in incoming and incoming_phase in {0, 1}:
                    next_payload["run"] = None
            else:
                next_payload = incoming
            if self.local_players_count_override is not None:
                try:
                    incoming_players_count = parse_players_count_input(next_payload.get("players_count", 0))
                except Exception:
                    incoming_players_count = None
                if incoming_players_count == self.local_players_count_override:
                    self.local_players_count_override = None
                else:
                    next_payload["players_count"] = self.local_players_count_override
            self.game_state = next_payload
            self.game_state_revision += 1
            try:
                phase = int(next_payload.get("phase", 0))
            except Exception:
                phase = 0
            current_run = next_payload.get("run") if isinstance(next_payload.get("run"), dict) else {}
            current_run_id = str(current_run.get("run_id") or current_run.get("id") or next_payload.get("run_id") or "").strip()
            authoritative_run_state = "run" in incoming
            if phase in {0, 1, 2}:
                self._reset_riddle_display_state_locked()
            if authoritative_run_state and phase == 2 and (previous_phase != 2 or current_run_id != previous_run_id):
                restored_claim_matches = (
                    self.start_assignment.get("active")
                    and self.start_assignment.get("_restored_from_disk")
                    and str(self.start_assignment.get("run_id") or "") == current_run_id
                )
                if not restored_claim_matches:
                    self._reset_selected_booking_locked()
                    self._reset_start_assignment_locked()
            elif authoritative_run_state and phase in {0, 1}:
                self._reset_start_assignment_locked()
            elif authoritative_run_state and phase >= 14 and self.start_assignment.get("active"):
                self._set_start_assignment_locked({
                    **self.start_assignment,
                    "active": False,
                    "intent_state": "terminal",
                    "status": "cancelled",
                    "message": "Die Buchungszuordnung wurde beendet, weil der Lauf nicht mehr aktiv ist.",
                })
            elif authoritative_run_state and phase >= 3 and self.start_assignment.get("active") and self.start_assignment.get("cancel_requested"):
                self._set_start_assignment_locked({
                    **self.start_assignment,
                    "active": False,
                    "intent_state": "terminal",
                })

            claimed_run_id = str(self.start_assignment.get("run_id") or "")
            if authoritative_run_state and self.start_assignment.get("active") and claimed_run_id and current_run_id and claimed_run_id != current_run_id:
                self._set_start_assignment_locked({
                    **self.start_assignment,
                    "active": False,
                    "intent_state": "terminal",
                    "status": "cancelled",
                    "message": "Die Buchungszuordnung wurde verworfen, weil inzwischen ein anderer Lauf aktiv ist.",
                })

            has_run_booking = (
                authoritative_run_state
                and "booking" in current_run
                and isinstance(current_run.get("booking"), dict)
            )
            if has_run_booking:
                run_booking = current_run["booking"]
                normalized_booking = normalize_booking_selection(run_booking)
                if normalized_booking != normalize_booking_selection(self.selected_booking):
                    try:
                        directory_synced = save_selected_booking(normalized_booking)
                    except Exception:
                        self.persistence_degraded = True
                        LOG.error("Rich game state booking could not be persisted", exc_info=True)
                    else:
                        self.selected_booking = normalized_booking
                        self.local_players_count_override = None
                        if not directory_synced:
                            self.persistence_degraded = True

    def set_local_phase(self, mode: str) -> None:
        phase_by_mode = {"standby": 0, "maintenance": 1, "prepare": 2}
        phase = phase_by_mode.get(str(mode or "").strip().lower())
        if phase is None:
            return
        with self.lock:
            self._reset_start_assignment_locked()
            current = dict(self.game_state)
            try:
                prev_phase = int(current.get("phase", 0))
            except Exception:
                prev_phase = 0
            current["last_phase"] = prev_phase
            current["phase"] = phase
            current["timer_running"] = False
            current["current_riddle_name"] = ""
            current["current_riddle_started_at"] = None
            if phase == 2:
                current["run"] = None
                current["players_count"] = 0
                self._reset_selected_booking_locked()
            self.game_state = current
            if phase in {0, 1, 2}:
                self._reset_riddle_display_state_locked()

    def set_local_players_count(self, players_count: int) -> None:
        with self.lock:
            current = dict(self.game_state)
            current["players_count"] = int(players_count)
            run = current.get("run") if isinstance(current.get("run"), dict) else None
            if run is not None:
                run = dict(run)
                run["players_count"] = int(players_count)
                current["run"] = run
            self.local_players_count_override = int(players_count)
            self.game_state = current

    def set_selected_booking(
        self,
        booking: dict[str, Any],
        *,
        expected_run_id: str = "",
        require_durable: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_booking_selection(booking)
        players_count = int(normalized.get("players") or 0)
        with self.lock:
            if expected_run_id:
                run = self.game_state.get("run") if isinstance(self.game_state.get("run"), dict) else {}
                current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
                try:
                    phase = int(self.game_state.get("phase", 0) or 0)
                except Exception:
                    phase = 0
                if current_run_id != expected_run_id or not 3 <= phase <= 13:
                    raise ValueError("Die Buchungszuordnung gehört nicht mehr zum aktiven Spiel.")
            current = dict(self.game_state)
            current["booking"] = normalized
            current["booking_code"] = str(normalized.get("bookingCode") or "")
            current["booking_email"] = str(normalized.get("customerEmail") or "")
            current["players_count"] = players_count
            run = current.get("run") if isinstance(current.get("run"), dict) else None
            if run is not None:
                run = dict(run)
                run["players_count"] = players_count
                run["booking"] = normalized
                run["booking_code"] = str(normalized.get("bookingCode") or "")
                run["booking_email"] = str(normalized.get("customerEmail") or "")
                current["run"] = run
            try:
                directory_synced = save_selected_booking(normalized)
            except Exception:
                self.persistence_degraded = True
                raise
            self.local_players_count_override = players_count
            self.selected_booking = normalized
            self.game_state = current
            if not directory_synced:
                self.persistence_degraded = True
                if require_durable:
                    raise RuntimeError("Die Buchungsauswahl wurde sichtbar gespeichert, aber nicht dauerhaft bestätigt.")
        return normalized

    def get_selected_booking(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.selected_booking or EMPTY_BOOKING_SELECTION))

    def _clear_node_payload_locked(self, node_id: str) -> None:
        existing = self.node_states.get(node_id, {}) or {}
        hb = existing.get("hb") if isinstance(existing, dict) else None
        self.node_states[node_id] = {"hb": hb} if hb is not None else {}

    def _reset_riddle_display_state_locked(self) -> None:
        for node_id in ["images_piano", "chess", "knocking", "candles", "star_slider", "stars"]:
            self._clear_node_payload_locked(node_id)
        self.riddle_states["images"] = {"id": "images", "buttons": {}}
        self.riddle_states["piano"] = {"id": "piano", "played_notes": []}
        self.riddle_states["chess"] = {"id": "chess", "reader_labels": {}}
        self.riddle_states["knocking"] = {"id": "knocking", "tries": 0, "attempted_sequences": []}
        self.riddle_states["candles"] = {"id": "candles", "tries": 0, "attempted_sequences": []}
        empty_stars_state = {"id": "stars", "tries": 0, "attempted_star_signs": [], "reader_positions": {}}
        self.riddle_states["stars"] = dict(empty_stars_state)
        self.riddle_states["star_slider"] = {**empty_stars_state, "id": "star_slider"}
        self.local_hint_counts = {}
        save_hint_store(self.local_hint_counts)

    def update_node_hb(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.node_last_hb[node_id] = time.monotonic()
            self.node_states.setdefault(node_id, {})["hb"] = payload

    def update_node_state(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            previous_node = self.node_states.get(node_id, {}) if isinstance(self.node_states.get(node_id), dict) else {}
            merged_node = dict(previous_node)
            merged_node.update(payload)
            self.node_states[node_id] = merged_node

            if node_id == "images_piano":
                if self._is_images_payload(payload):
                    prev = self.riddle_states.get("images", {}) if isinstance(self.riddle_states.get("images"), dict) else {}
                    merged = dict(prev)
                    merged.update(payload)
                    merged["id"] = "images"
                    self.riddle_states["images"] = merged
                    return
                if self._is_piano_payload(payload):
                    prev = self.riddle_states.get("piano", {}) if isinstance(self.riddle_states.get("piano"), dict) else {}
                    merged = dict(prev)
                    merged.update(payload)
                    merged["id"] = "piano"
                    played_notes = list(prev.get("played_notes") or [])
                    encoded = str(payload.get("encoded") or "").strip()
                    if encoded:
                        played_notes.append({
                            "encoded": encoded,
                            "accepted": bool(payload.get("accepted", False)),
                        })
                    merged["played_notes"] = played_notes[-40:]
                    self.riddle_states["piano"] = merged
                    return

            riddle_id = str(payload.get("id", "")).strip()
            if riddle_id:
                prev = self.riddle_states.get(riddle_id, {}) if isinstance(self.riddle_states.get(riddle_id), dict) else {}
                merged = dict(prev)
                merged.update(payload)
                if riddle_id in {"knocking", "candles"}:
                    attempts = list(merged.get("attempted_sequences") or [])
                    last_attempt = str(payload.get("last_attempt") or "").strip()
                    if last_attempt and (not attempts or attempts[-1] != last_attempt):
                        attempts.append(last_attempt)
                    merged["attempted_sequences"] = attempts
                elif riddle_id == "star_slider":
                    attempts = list(merged.get("attempted_star_signs") or [])
                    last_positions = payload.get("last_attempt_positions")
                    if isinstance(last_positions, dict) and (not attempts or attempts[-1] != last_positions):
                        attempts.append(last_positions)
                    merged["attempted_star_signs"] = attempts
                self.riddle_states[riddle_id] = merged

    @staticmethod
    def _is_images_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if "buttons" in payload and isinstance(payload.get("buttons"), dict):
            return True
        return any(key in payload for key in ["jesus", "blumen", "flowers", "natur", "nature", "puppe", "doll"])

    @staticmethod
    def _is_piano_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(key in payload for key in ["encoded", "note", "accepted", "top3", "margins"])

    def update_lock_state(self, lock_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.locks[lock_id] = payload

    def update_light_state(self, light_name: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.lights[light_name] = payload

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            game = json.loads(json.dumps(self.game_state))
            node_states = json.loads(json.dumps(self.node_states))
            locks = json.loads(json.dumps(self.locks))
            lights = json.loads(json.dumps(self.lights))
            riddle_states = json.loads(json.dumps(self.riddle_states))
            node_last_hb = dict(self.node_last_hb)
            local_hint_counts = dict(self.local_hint_counts)
            start_assignment = json.loads(json.dumps(self.start_assignment))

        return {
            "game": self._build_game_summary(game),
            "nodes": self._build_node_summary(node_last_hb, node_states),
            "locks": self._build_lock_summary(locks),
            "lights": self._build_light_summary(lights, node_states),
            "riddles": self._build_riddle_summary(game, node_states, riddle_states, node_last_hb, local_hint_counts),
            "booking": self.get_selected_booking(),
            "start_assignment": start_assignment,
            "meta": {
                "broker": BROKER_HOST,
                "website_api_configured": bool(website_api_config()[0]),
                "website_api_base": website_api_config()[0],
                "persistence_degraded": bool(self.persistence_degraded),
            },
        }

    def _build_game_summary(self, game: dict[str, Any]) -> dict[str, Any]:
        phase = int(game.get("phase", 1))
        last_phase = game.get("last_phase")
        phase_meta = PHASE_META.get(phase, {"name": f"phase_{phase}", "active": (), "solved": ()})
        last_name = ""
        if last_phase is not None:
            last_name = PHASE_META.get(int(last_phase), {"name": f"phase_{last_phase}"}).get("name", "")
        phase_name_pretty = pretty_phase_name(phase_meta["name"])
        last_name_pretty = pretty_phase_name(last_name)
        timer_running = bool(game.get("timer_running", False))

        run = game.get("run") if isinstance(game.get("run"), dict) else {}
        started_at = run.get("started_at") or game.get("game_started_at") or game.get("started_at")
        last_riddle_solved_at = game.get("last_riddle_solved_at")
        active_riddles = tuple(phase_meta.get("active", ()) or ())

        raw_timings = run.get("riddle_timings") if isinstance(run, dict) else {}
        timing_map: dict[str, dict[str, Any]] = {}
        if isinstance(raw_timings, dict):
            for key, value in raw_timings.items():
                if isinstance(value, dict):
                    timing_map[_canonical_riddle_name(key)] = value
        elif isinstance(raw_timings, list):
            for value in raw_timings:
                if isinstance(value, dict):
                    key = _canonical_riddle_name(value.get("riddle_key") or value.get("id") or value.get("riddle"))
                    if key:
                        timing_map[key] = value

        current_riddle_name = _canonical_riddle_name(game.get("current_riddle_name") or game.get("current_riddle") or "")
        if not current_riddle_name and timer_running:
            for candidate in active_riddles:
                timing = timing_map.get(_canonical_riddle_name(candidate), {})
                if str(timing.get("status") or "").lower() == "active" or bool(timing.get("active")):
                    current_riddle_name = _canonical_riddle_name(candidate)
                    break
        if not current_riddle_name and timer_running and active_riddles:
            current_riddle_name = _canonical_riddle_name(active_riddles[0])

        current_riddle_started_at = None
        if current_riddle_name:
            current_riddle_started_at = last_riddle_solved_at or started_at

        def _seconds_since(value: Any) -> int:
            if not value:
                return 0
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
            except Exception:
                return 0

        def _numeric_seconds(*values: Any) -> int | None:
            for value in values:
                if value in {None, ""}:
                    continue
                try:
                    return max(0, int(round(float(value))))
                except Exception:
                    continue
            return None

        current_timing = timing_map.get(current_riddle_name, {}) if current_riddle_name else {}
        live_elapsed = _numeric_seconds(run.get("live_duration_s"), run.get("duration_s"), game.get("duration_s"))
        current_elapsed = _numeric_seconds(
            current_timing.get("display_time_s"),
            current_timing.get("live_time_s"),
            current_timing.get("solve_time_s"),
        )
        recovery = game.get("recovery") if isinstance(game.get("recovery"), dict) else {}
        recovery_restored = bool(recovery.get("restored"))
        phase_display = f"{phase}: {phase_name_pretty}"
        if recovery_restored:
            phase_display += " (nach Neustart wiederhergestellt)"

        return {
            "phase": phase,
            "phase_name": phase_meta["name"],
            "phase_name_pretty": phase_name_pretty,
            "phase_display": phase_display,
            "last_phase": last_phase,
            "last_phase_name": last_name,
            "last_phase_name_pretty": last_name_pretty,
            "players_count": int(run.get("players_count") if run.get("players_count") is not None else (game.get("players_count") or 0)),
            "timer_running": timer_running,
            "elapsed_s": live_elapsed if live_elapsed is not None else (_seconds_since(started_at) if timer_running else 0),
            "started_at": started_at,
            "last_riddle_solved_at": last_riddle_solved_at,
            "current_riddle_elapsed_s": current_elapsed if current_elapsed is not None else (_seconds_since(current_riddle_started_at) if timer_running else 0),
            "current_riddle_name": current_riddle_name,
            "current_riddle_started_at": current_riddle_started_at,
            "run_id": run.get("run_id") or run.get("id") or "",
            "leaderboard_code": str(run.get("leaderboard_code") or "").strip(),
            "ended_at": run.get("ended_at"),
            "recovery_restored": recovery_restored,
            "recovery_checkpoint_saved_at": recovery.get("checkpoint_saved_at"),
        }

    def _build_node_summary(self, node_last_hb: dict[str, float], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        out = []
        for node_id, label in NODE_LABELS:
            last = node_last_hb.get(node_id)
            online = (last is not None) and (now_mono - last <= 15.0)
            hb = node_states.get(node_id, {}).get("hb", {})
            uptime = hb.get("up") if isinstance(hb, dict) else None
            status = "online" if online else "offline"
            if online and isinstance(uptime, (int, float)):
                status = f"online ({int(uptime)}s)"
            out.append({"id": node_id, "label": label, "online": online, "status": status})
        return out

    def _build_lock_summary(self, locks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in LOCKS:
            payload = locks.get(item["id"], {})
            state = str(payload.get("state", "")).upper()
            if state == "OPEN":
                is_open = True
                state_label = "open"
            elif state == "CLOSED":
                is_open = False
                state_label = "closed"
            else:
                is_open = None
                state_label = "unknown"
            action = "close" if item["kind"] == "toggle" and is_open else "open"
            out.append({
                "id": item["id"],
                "label": item["label"],
                "kind": item["kind"],
                "is_open": is_open,
                "state_label": state_label,
                "button": "Schließen" if action == "close" else "Öffnen",
                "state_class": "is-open" if is_open else ("is-closed" if is_open is False else "is-unknown"),
            })
        return out

    def _build_light_summary(self, lights: dict[str, dict[str, Any]], node_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        star_sky_state = node_states.get("star_sky", {})
        if not isinstance(star_sky_state, dict):
            star_sky_state = {}

        def component_state(payload: Any) -> tuple[bool, int] | None:
            if not isinstance(payload, dict) or not payload:
                return None
            pct: int | None = None
            try:
                if payload.get("pct") is not None:
                    pct = max(0, min(100, int(payload["pct"])))
            except (TypeError, ValueError):
                pct = None
            raw_on = payload.get("on")
            if isinstance(raw_on, bool):
                is_on = raw_on
            elif isinstance(raw_on, (int, float)) and not isinstance(raw_on, bool):
                is_on = raw_on > 0
            elif isinstance(raw_on, str) and raw_on.strip().lower() in {"on", "true", "1"}:
                is_on = True
            elif isinstance(raw_on, str) and raw_on.strip().lower() in {"off", "false", "0"}:
                is_on = False
            elif pct is not None:
                is_on = pct > 0
            else:
                return None
            return is_on, (pct if pct is not None else (100 if is_on else 0))

        for key, cfg in LIGHT_GROUPS.items():
            components = [state for name in cfg["lights"] if (state := component_state(lights.get(name))) is not None]
            total_components = len(cfg["lights"])
            if key == "star_sky":
                total_components += 1
                enabled_key = next(
                    (name for name in ("enabled", "moduleEnabled", "module_enabled") if name in star_sky_state),
                    None,
                )
                if enabled_key is not None:
                    star_component = component_state({"on": star_sky_state[enabled_key]})
                    if star_component is not None:
                        components.append(star_component)

            known_count = len(components)
            on_values = [is_on for is_on, _pct in components]
            effective_pct_values = [pct_value if is_on else 0 for is_on, pct_value in components]
            any_on = any(on_values)
            all_on = known_count == total_components and known_count > 0 and all(on_values)
            partial = 0 < known_count < total_components
            mixed = len(set(on_values)) > 1 or (
                bool(cfg.get("dimmable"))
                and len(set(effective_pct_values)) > 1
            )
            pct = max(effective_pct_values) if effective_pct_values else 0
            pct_min = min(effective_pct_values) if effective_pct_values else 0
            if mixed:
                state = "mixed"
                state_label = f"gemischt ({pct_min}-{pct}%)" if cfg.get("dimmable") else "gemischt"
            elif partial:
                state = "partial"
                known_state = "an" if any_on else "aus"
                state_label = f"teilweise bekannt ({known_state}, {pct}%)" if cfg.get("dimmable") else f"teilweise bekannt ({known_state})"
            elif not components:
                state = "unknown"
                state_label = "unbekannt"
            elif all_on:
                state = "on"
                state_label = f"an ({pct}%)" if cfg.get("dimmable") else "an"
            else:
                state = "off"
                state_label = "aus (0%)" if cfg.get("dimmable") else "aus"
            out.append({
                "id": key,
                "label": cfg["label"],
                "on": all_on,
                "any_on": any_on,
                "all_on": all_on,
                "state": state,
                "mixed": mixed,
                "partial": partial,
                "known_count": known_count,
                "component_count": total_components,
                "pct": pct,
                "pct_min": pct_min,
                "button": "Aus" if any_on else "An",
                "dimmable": bool(cfg.get("dimmable")),
                "state_label": state_label,
                "state_class": f"is-{state}",
            })
        return out

    def _build_riddle_summary(self, game: dict[str, Any], node_states: dict[str, dict[str, Any]], riddle_states: dict[str, dict[str, Any]], node_last_hb: dict[str, float], local_hint_counts: dict[str, int]) -> list[dict[str, Any]]:
        phase = int(game.get("phase", 1))
        meta = PHASE_META.get(phase, {"active": (), "solved": ()})
        active = {_canonical_riddle_name(item) for item in (meta.get("active", ()) or ())}
        solved = {_canonical_riddle_name(item) for item in (meta.get("solved", ()) or ())}

        run = game.get("run") if isinstance(game.get("run"), dict) else {}
        raw_timings = run.get("riddle_timings") if isinstance(run, dict) else {}
        riddle_timings: dict[str, dict[str, Any]] = {}
        if isinstance(raw_timings, dict):
            for key, value in raw_timings.items():
                if isinstance(value, dict):
                    riddle_timings[_canonical_riddle_name(key)] = value
        elif isinstance(raw_timings, list):
            for value in raw_timings:
                if isinstance(value, dict):
                    key = _canonical_riddle_name(value.get("riddle_key") or value.get("id") or value.get("riddle"))
                    if key:
                        riddle_timings[key] = value

        now_mono = time.monotonic()
        out = []
        for row in RIDDLES:
            riddle_id = _canonical_riddle_name(row["id"])
            node_id = row["node_id"]
            state_payload = riddle_states.get(riddle_id) or (node_states.get(node_id, {}) if node_id else {})
            timing = riddle_timings.get(riddle_id) or {}
            timing_status = str(timing.get("status") or "").strip().lower().replace(" ", "_")

            skipped = bool(timing.get("skipped", False)) or timing_status == "skipped"
            not_solved = bool(timing.get("not_solved", False)) or timing_status == "not_solved"
            reset_pending = bool(timing.get("reset_pending", False)) or timing_status == "reset"
            solved_by_timing = bool(timing.get("solved", False)) or timing_status == "solved"
            active_by_timing = bool(timing.get("active", False)) or timing_status == "active"

            if reset_pending:
                phase_state = "reset"
            elif skipped:
                phase_state = "skipped"
            elif not_solved:
                phase_state = "not_solved"
            elif riddle_id in solved or solved_by_timing:
                phase_state = "solved"
            elif riddle_id in active or active_by_timing:
                phase_state = "active"
            else:
                phase_state = "pending"

            if row["manual"]:
                node_status = "manual"
                online = None
            else:
                last = node_last_hb.get(node_id or "")
                online = (last is not None) and (now_mono - last <= 15.0)
                hb = node_states.get(node_id or "", {}).get("hb", {})
                uptime = hb.get("up") if isinstance(hb, dict) else None
                node_status = "online" if online else "offline"
                if online and isinstance(uptime, (int, float)):
                    node_status = f"online ({int(uptime)}s)"

            def _first_seconds(*values: Any) -> int:
                for value in values:
                    if value in {None, ""}:
                        continue
                    try:
                        return max(0, int(round(float(value))))
                    except Exception:
                        continue
                return 0

            display_seconds = _first_seconds(
                timing.get("display_time_s"),
                timing.get("live_time_s"),
                timing.get("solve_time_s"),
            )
            hint_count = int(timing.get("hint_count") if timing.get("hint_count") is not None else local_hint_counts.get(riddle_id, 0) or 0)
            phase_state_label = {
                "solved": "Gelöst",
                "active": "Aktiv",
                "pending": "Ausstehend",
                "skipped": "Übersprungen",
                "not_solved": "Nicht gelöst",
                "reset": "Zurückgesetzt",
            }.get(phase_state, phase_state)

            out.append({
                "id": riddle_id,
                "label": row["label"],
                "manual": row["manual"],
                "phase_state": phase_state,
                "phase_state_label": phase_state_label,
                "phase_state_class": f"phase-{phase_state.replace('_', '-')}",
                "node_status": node_status,
                "node_status_class": "node-manual" if row["manual"] else ("node-online" if online else "node-offline"),
                "tries": self._extract_tries(state_payload),
                "info": self._extract_info(riddle_id, state_payload),
                "images_buttons": self._extract_images_buttons(riddle_id, state_payload),
                "chess_slots": self._extract_chess_slots(riddle_id, state_payload),
                "attempts_summary": self._extract_attempts_summary(riddle_id, state_payload),
                "star_slider_summary": self._extract_star_slider_summary(riddle_id, state_payload),
                "piano_summary": self._extract_piano_summary(riddle_id, state_payload),
                "hint_count": hint_count,
                "time_s": display_seconds,
                "display_time_s": display_seconds,
                "solve_time_s": _first_seconds(timing.get("solve_time_s")),
                "live_time_s": _first_seconds(timing.get("live_time_s")),
                "skipped": skipped,
                "not_solved": not_solved,
                "reset_pending": reset_pending,
                "resettable": riddle_id in {"prison", "wheel", "chains", "tangram", "magnet"},
                "can_solve": phase_state in {"active", "reset"},
                "solve_advances": phase_state == "active" or (phase_state == "reset" and riddle_id in active),
            })
        return out

    @staticmethod
    def _extract_tries(state_payload: dict[str, Any]) -> str:
        if not state_payload:
            return ""
        for key in ["tries", "attempt", "attempts", "attempt_idx", "attemptIndex"]:
            if key in state_payload:
                return str(state_payload.get(key, ""))
        return ""

    @staticmethod
    def _extract_images_buttons(riddle_id: str, state_payload: dict[str, Any]) -> dict[str, bool] | None:
        if riddle_id != "images" or not state_payload:
            return None
        buttons = state_payload.get("buttons")
        if isinstance(buttons, dict):
            return {
                "jesus": bool(buttons.get("jesus", False)),
                "blumen": bool(buttons.get("blumen", buttons.get("flowers", False))),
                "natur": bool(buttons.get("natur", buttons.get("nature", False))),
                "puppe": bool(buttons.get("puppe", buttons.get("doll", False))),
            }
        return {
            "jesus": bool(state_payload.get("jesus", False)),
            "blumen": bool(state_payload.get("blumen", state_payload.get("flowers", False))),
            "natur": bool(state_payload.get("natur", state_payload.get("nature", False))),
            "puppe": bool(state_payload.get("puppe", state_payload.get("doll", False))),
        }

    @staticmethod
    def _stringify_scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip()

    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [cls._stringify_scalar(x) for x in value if cls._stringify_scalar(x)]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("[") and text.endswith("]"):
                try:
                    loaded = json.loads(text)
                    if isinstance(loaded, list):
                        return [cls._stringify_scalar(x) for x in loaded if cls._stringify_scalar(x)]
                except Exception:
                    pass
            if any(sep in text for sep in [",", " "]):
                return [part for part in re.split(r"[\s,]+", text) if part]
            return [text]
        return [cls._stringify_scalar(value)]

    @classmethod
    def _normalize_knocking_attempt(cls, value: Any) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ""
            if raw.isdigit():
                return raw
            tokens = [part for part in re.split(r"[^A-Za-z0-9]+", raw) if part]
            return "".join(tokens)
        return "".join(cls._string_list(value))

    @classmethod
    def _normalize_flat_attempt(cls, value: Any) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ""
            if raw.isdigit():
                return raw
            tokens = [part for part in re.split(r"[^A-Za-z0-9]+", raw) if part]
            return "".join(tokens)
        return "".join(cls._string_list(value))

    @staticmethod
    def _extract_chess_slots(riddle_id: str, state_payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if riddle_id != "chess" or not state_payload:
            return None
        expected = {
            "queen": "QUEEN",
            "knight": "HORSE",
            "rook": "ROOK",
            "king": "KING",
        }
        slot_labels = {"queen": "Dame", "knight": "Pferd", "rook": "Turm", "king": "König"}
        value_labels = {"QUEEN": "Dame", "HORSE": "Pferd", "KNIGHT": "Pferd", "ROOK": "Turm", "KING": "König", "EMPTY": "Leer", "UNKNOWN": "Unbekannt"}
        raw_labels = state_payload.get("reader_labels") or state_payload.get("reader_label") or state_payload
        labels: dict[str, Any] = {}
        if isinstance(raw_labels, dict):
            labels = {str(key).strip().lower(): value for key, value in raw_labels.items()}
        elif isinstance(raw_labels, (list, tuple)):
            ordered = list(raw_labels)[:4]
            labels = {slot: ordered[idx] if idx < len(ordered) else "EMPTY" for idx, slot in enumerate(["queen", "knight", "rook", "king"])}

        out: list[dict[str, Any]] = []
        for slot, target in expected.items():
            raw_value = labels.get(slot, labels.get("horse", "EMPTY") if slot == "knight" else "EMPTY")
            value = str(raw_value).strip().upper() or "EMPTY"
            normalized_value = "HORSE" if value in {"HORSE", "KNIGHT"} else value
            out.append({
                "slot": slot_labels.get(slot, slot.capitalize()),
                "value": value_labels.get(value, value_labels.get(normalized_value, normalized_value)),
                "correct": normalized_value == target,
            })
        return out

    @classmethod
    def _extract_attempts_summary(cls, riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id not in {"knocking", "candles"} or not state_payload:
            return None
        tries = cls._extract_tries(state_payload)
        attempts_raw = state_payload.get("attempted_sequences")
        attempts: list[str] = []
        items = attempts_raw if isinstance(attempts_raw, list) else ([attempts_raw] if attempts_raw is not None else [])
        for item in items:
            text = cls._normalize_knocking_attempt(item) if riddle_id == "knocking" else cls._normalize_flat_attempt(item)
            if text:
                attempts.append(text)
        return {"tries": tries, "attempts": attempts}

    @classmethod
    def _extract_star_slider_values(cls, value: Any) -> list[str]:
        order = ["r2", "r1", "r0"]
        if isinstance(value, dict):
            return [str(value.get(key, "none") or "none").strip() for key in order]
        if isinstance(value, (list, tuple)):
            raw = [str(x).strip() or "none" for x in list(value)[:3]]
            while len(raw) < 3:
                raw.append("none")
            return [raw[2], raw[1], raw[0]]
        return []

    @classmethod
    def _extract_star_slider_summary(cls, riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id != "star_slider" or not state_payload:
            return None
        positions = state_payload.get("reader_positions") or {}
        current = cls._extract_star_slider_values(positions)
        if not current:
            current = cls._extract_star_slider_values(state_payload.get("reader_labels"))
        attempts = []
        for item in state_payload.get("attempted_star_signs") or []:
            if not isinstance(item, dict):
                continue
            vals = cls._extract_star_slider_values(item.get("positions") or item)
            if vals:
                attempts.append(vals)
        return {"current": current, "attempts": attempts}

    @staticmethod
    def _extract_piano_summary(riddle_id: str, state_payload: dict[str, Any]) -> dict[str, Any] | None:
        if riddle_id != "piano" or not state_payload:
            return None
        played_notes = []
        for item in state_payload.get("played_notes") or []:
            if not isinstance(item, dict):
                continue
            encoded = str(item.get("encoded") or "").strip()
            if not encoded:
                continue
            played_notes.append({
                "encoded": encoded,
                "accepted": bool(item.get("accepted", False)),
            })
        return {"played_notes": played_notes}

    @staticmethod
    def _extract_info(riddle_id: str, state_payload: dict[str, Any]) -> str:
        if not state_payload or riddle_id in {"images", "piano", "chess", "knocking", "candles", "star_slider"}:
            return ""
        generic = []
        for key, value in state_payload.items():
            if key in {"id", "fw", "up", "ts", "time_valid", "buttons"}:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            generic.append(f"{key}: {value}")
            if len(generic) >= 4:
                break
        return "   ".join(generic)



store = DashboardStore()
node_log_buffer = NodeLogBuffer()
_last_requested_log_levels: dict[str, dict[str, str]] = {}
_last_requested_log_levels_lock = threading.RLock()
_log_level_publish_locks = {node: threading.Lock() for node in LOG_NODE_IDS}
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
if hasattr(mqtt_client, "suppress_exceptions"):
    mqtt_client.suppress_exceptions = True
_start_assignment_workers: dict[str, threading.Thread] = {}
_start_assignment_workers_lock = threading.Lock()


def _remember_requested_log_level(node: str, level: str, *, requested_at: datetime | None = None) -> dict[str, str]:
    timestamp = (requested_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    record = {
        "level": level,
        "requested_at": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    with _last_requested_log_levels_lock:
        _last_requested_log_levels[node] = record
    return dict(record)


def _requested_log_levels_snapshot() -> dict[str, dict[str, str]]:
    with _last_requested_log_levels_lock:
        return {node: dict(record) for node, record in _last_requested_log_levels.items()}


def _publish_requested_log_level(node: str, level: str) -> dict[str, str] | None:
    with _log_level_publish_locks[node]:
        if not mqtt_publish(f"{node}/log/level", level):
            return None
        return _remember_requested_log_level(node, level)




def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    raise TypeError(f"Unsupported row type for dict conversion: {type(row)!r}")


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[:1] not in '[{':
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _parse_dt(value: Any) -> Any:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        try:
            from datetime import datetime
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(normalized)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def format_datetime_readable(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return str(value or "—")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_mmss(value: Any) -> str:
    if value in {None, ""}:
        return "—"
    try:
        total_seconds = int(round(float(value)))
    except Exception:
        return str(value)
    minutes, seconds = divmod(max(total_seconds, 0), 60)
    return f"{minutes:02d}:{seconds:02d}"


def serialize_db_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def display_players_count(value: Any) -> str:
    try:
        return str(max(int(float(str(value).strip() or "0")), 0))
    except Exception:
        return "0"


def parse_players_count_input(value: Any) -> int:
    try:
        return max(int(float(str(value).strip() or "0")), 0)
    except Exception as exc:
        raise ValueError("players_count must be a non-negative integer") from exc


def parse_mmss_input(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "—":
        return None
    if re.fullmatch(r"\d{1,2}:\d{1,2}(?::\d{1,2})?", text):
        chunks = [int(part) for part in text.split(":")]
        if len(chunks) == 2:
            minutes, seconds = chunks
            return float(minutes * 60 + seconds)
        hours, minutes, seconds = chunks
        return float(hours * 3600 + minutes * 60 + seconds)
    return float(text)


def _seconds_or_none(value: Any) -> float | None:
    if value in {None, "", "—"}:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _riddle_anchor_seconds(riddle_name: str, solve_by_name: dict[str, float | None], sequential_previous_solve: float) -> float:
    name = str(riddle_name or "").strip()
    if name in {"tangram", "magnet"}:
        rope_solve = solve_by_name.get("rope_paths")
        if rope_solve is not None:
            return float(rope_solve)
    if name == "chess":
        later_parallel = [solve_by_name.get("tangram"), solve_by_name.get("magnet")]
        solved = [float(item) for item in later_parallel if item is not None]
        if solved:
            return max(solved)
    return float(sequential_previous_solve or 0.0)


def _calculate_riddle_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    solve_by_name: dict[str, float | None] = {}
    sequential_previous_solve = 0.0
    calculated: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        solve_seconds = _seconds_or_none(row.get("solve_time_from_run_start_s"))
        anchor_seconds = _riddle_anchor_seconds(str(row.get("riddle") or ""), solve_by_name, sequential_previous_solve)
        if solve_seconds is None:
            riddle_seconds = None
        else:
            riddle_seconds = max(0.0, solve_seconds - anchor_seconds)
            sequential_previous_solve = solve_seconds
        solve_by_name[str(row.get("riddle") or "").strip()] = solve_seconds
        row["_anchor_seconds"] = anchor_seconds
        row["_solve_seconds"] = solve_seconds
        row["_riddle_seconds"] = riddle_seconds
        calculated.append(row)
    return calculated


def _recalculate_game_riddle_solve_times(
    conn: sqlite3.Connection,
    game_id: str,
    *,
    row_name_overrides: dict[int, str] | None = None,
    solve_overrides: dict[int, float | None] | None = None,
    duration_overrides: dict[int, float | None] | None = None,
) -> list[dict[str, Any]]:
    row_name_overrides = dict(row_name_overrides or {})
    solve_overrides = dict(solve_overrides or {})
    duration_overrides = dict(duration_overrides or {})

    rows = [
        _row_to_dict(row) or {}
        for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()
    ]
    if not rows:
        return []

    for row in rows:
        rowid = int(row.get("_rowid_") or 0)
        if rowid in row_name_overrides:
            row["riddle"] = row_name_overrides[rowid]

    current_rows = _calculate_riddle_timing_rows(rows)
    current_durations = {
        int(row.get("_rowid_") or 0): row.get("_riddle_seconds")
        for row in current_rows
    }

    solve_by_name: dict[str, float | None] = {}
    sequential_previous_solve = 0.0
    recalculated: list[dict[str, Any]] = []
    for row in rows:
        rowid = int(row.get("_rowid_") or 0)
        row_name = str(row.get("riddle") or "").strip()
        anchor_seconds = _riddle_anchor_seconds(row_name, solve_by_name, sequential_previous_solve)

        has_solve_override = rowid in solve_overrides
        if has_solve_override:
            solve_seconds = solve_overrides[rowid]
            riddle_seconds = None if solve_seconds is None else max(0.0, float(solve_seconds) - anchor_seconds)
        else:
            riddle_seconds = duration_overrides.get(rowid, current_durations.get(rowid))
            solve_seconds = None if riddle_seconds is None else max(0.0, anchor_seconds + float(riddle_seconds))

        row["solve_time_from_run_start_s"] = solve_seconds
        row["_anchor_seconds"] = anchor_seconds
        row["_riddle_seconds"] = riddle_seconds
        row["_solve_seconds"] = solve_seconds
        recalculated.append(row)

        if solve_seconds is not None:
            sequential_previous_solve = float(solve_seconds)
        solve_by_name[row_name] = solve_seconds

    for row in recalculated:
        conn.execute(
            "UPDATE game_riddles SET riddle = ?, solve_time_from_run_start_s = ? WHERE rowid = ?",
            (row.get("riddle"), row.get("solve_time_from_run_start_s"), int(row.get("_rowid_") or 0)),
        )

    final_solve = max((float(row.get("_solve_seconds")) for row in recalculated if row.get("_solve_seconds") is not None), default=None)
    try:
        _refresh_game_duration_and_end(conn, game_id, final_solve)
    except Exception:
        pass

    return recalculated


def _refresh_game_hint_count(conn: sqlite3.Connection, game_id: str) -> int:
    total_hints = conn.execute("SELECT COUNT(*) FROM game_hints WHERE game_id = ?", (game_id,)).fetchone()[0]
    conn.execute("UPDATE games SET hint_count = ? WHERE id = ?", (int(total_hints or 0), game_id))
    return int(total_hints or 0)


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _refresh_game_duration_and_end(conn: sqlite3.Connection, game_id: str, explicit_duration_s: float | None = None) -> float | None:
    if explicit_duration_s is None:
        row = conn.execute(
            "SELECT MAX(solve_time_from_run_start_s) FROM game_riddles WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        duration_s = None if not row or row[0] is None else float(row[0])
    else:
        duration_s = float(explicit_duration_s)

    game_row = conn.execute("SELECT started_at, ended_at FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return duration_s

    started_at = _parse_iso_datetime(game_row[0])
    ended_at_value = game_row[1]
    if duration_s is None:
        conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (None, ended_at_value, game_id))
        return None

    next_ended_at = ended_at_value
    if started_at is not None:
        next_ended_at = (started_at + timedelta(seconds=max(0.0, duration_s))).isoformat(timespec='seconds')

    conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (duration_s, next_ended_at, game_id))
    return duration_s


def update_hint_rows_for_riddle(conn: sqlite3.Connection, game_id: str, riddle: str, target_count: int) -> None:
    target_count = max(int(target_count), 0)
    rows = conn.execute(
        "SELECT id, at, hint_text FROM game_hints WHERE game_id = ? AND riddle = ? ORDER BY id ASC",
        (game_id, riddle),
    ).fetchall()
    current_count = len(rows)
    if target_count == current_count:
        return
    if target_count < current_count:
        to_delete = [row[0] for row in rows[target_count:]]
        conn.executemany("DELETE FROM game_hints WHERE id = ?", [(item,) for item in to_delete])
        return

    base_at = None
    if rows:
        base_at = rows[-1][1]
    if not base_at:
        base_at = conn.execute(
            "SELECT solved_at FROM game_riddles WHERE game_id = ? AND riddle = ? ORDER BY rowid ASC LIMIT 1",
            (game_id, riddle),
        ).fetchone()
        base_at = base_at[0] if base_at and base_at[0] else None
    if not base_at:
        base_at = conn.execute(
            "SELECT started_at FROM games WHERE id = ?",
            (game_id,),
        ).fetchone()
        base_at = base_at[0] if base_at and base_at[0] else datetime.now(timezone.utc).isoformat()

    missing = target_count - current_count
    conn.executemany(
        "INSERT INTO game_hints (game_id, at, riddle, hint_text) VALUES (?, ?, ?, ?)",
        [(game_id, base_at, riddle, "") for _ in range(missing)],
    )


def first_existing_key(row: dict[str, Any] | None, candidates: list[str]) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in candidates:
        if key in row:
            return key
    return None


def build_editable_columns(rows: list[dict[str, Any]], *, exclude: set[str] | None = None, preferred: list[str] | None = None) -> list[str]:
    exclude = set(exclude or set()) | {"_rowid_"}
    ordered: list[str] = []
    preferred = preferred or []
    for name in preferred:
        if name not in exclude and any(name in (row or {}) for row in rows):
            ordered.append(name)
    for row in rows:
        for key in (row or {}).keys():
            if key in exclude or key in ordered:
                continue
            ordered.append(key)
    return ordered


def list_games_from_db() -> list[dict[str, Any]]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rowid AS _rowid_, * FROM games ORDER BY COALESCE(started_at, date, id) DESC, id DESC"
        ).fetchall()

    games: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row) or {}
        item["started_at_display"] = format_datetime_readable(item.get("started_at") or item.get("game_started_at") or item.get("date"))
        item["ended_at_display"] = format_datetime_readable(item.get("ended_at"))
        item["date_display"] = str(item.get("date") or item.get("started_at_display")[:10] or "—")
        item["duration_mmss"] = format_mmss(item.get("duration_s"))
        item["players_count_display"] = display_players_count(item.get("players_count"))
        item["hint_count_display"] = int(item.get("hint_count") or 0)
        item["leaderboard_code_display"] = serialize_db_value(item.get("leaderboard_code")) or ""
        games.append(item)
    return games


def build_game_view_state(game_id: str) -> dict[str, Any]:
    loaded = load_game_from_db(game_id)
    game = loaded["game"]
    riddles = loaded["riddles"]
    hints = loaded["hints"]
    if game is None:
        return {"game": None, "riddles": [], "hints": [], "hint_columns": [], "raw_rows": []}

    game["started_at_display"] = format_datetime_readable(game.get("started_at") or game.get("game_started_at") or game.get("date"))
    game["ended_at_display"] = format_datetime_readable(game.get("ended_at"))
    game["duration_mmss"] = format_mmss(game.get("duration_s"))
    game["players_count_display"] = display_players_count(game.get("players_count"))
    game["hint_count_display"] = int(game.get("hint_count") or 0)
    game["leaderboard_code_display"] = serialize_db_value(game.get("leaderboard_code")) or ""

    riddle_hint_counts: dict[str, int] = {}
    for hint in hints:
        name = str(hint.get("riddle") or "").strip()
        if name:
            riddle_hint_counts[name] = riddle_hint_counts.get(name, 0) + 1

    rendered_riddles: list[dict[str, Any]] = []
    for row in _calculate_riddle_timing_rows(riddles):
        rendered = dict(row)
        rendered["solve_time_mmss"] = format_mmss(rendered.get("_solve_seconds"))
        rendered["riddle_time_mmss"] = format_mmss(rendered.get("_riddle_seconds"))
        rendered["hint_count_display"] = int(riddle_hint_counts.get(str(rendered.get("riddle") or ""), 0))
        rendered_riddles.append(rendered)

    hint_columns = build_editable_columns(hints, preferred=["at", "riddle", "hint_text"])
    raw_rows = [
        {"table": "games", "rowid": game.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in game.items() if not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
    ]
    raw_rows.extend(
        {"table": "game_riddles", "rowid": row.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in row.items() if not str(k).startswith("_") and not str(k).endswith("_display") and not str(k).endswith("_mmss")}, ensure_ascii=False, indent=2, default=str)}
        for row in rendered_riddles
    )
    raw_rows.extend(
        {"table": "game_hints", "rowid": row.get("_rowid_"), "raw_json": json.dumps({k: v for k, v in row.items() if k != "_rowid_"}, ensure_ascii=False, indent=2, default=str)}
        for row in hints
    )
    return {
        "game": game,
        "riddles": rendered_riddles,
        "hints": hints,
        "hint_columns": hint_columns,
        "raw_rows": raw_rows,
    }


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or "TEXT"),
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": bool(row[5]),
        }
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]


def _ensure_missing_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> None:
    src_columns = _table_columns(src, table_name)
    dst_column_names = {item["name"] for item in _table_columns(dst, table_name)}
    for column in src_columns:
        name = column["name"]
        if name in dst_column_names:
            continue
        coltype = column["type"] or "TEXT"
        if name == "players_count":
            dst.execute(f"ALTER TABLE {table_name} ADD COLUMN players_count INTEGER NOT NULL DEFAULT 0")
        else:
            dst.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {coltype}")


def _intersecting_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> list[str]:
    src_names = [item["name"] for item in _table_columns(src, table_name)]
    dst_names = {item["name"] for item in _table_columns(dst, table_name)}
    return [name for name in src_names if name in dst_names]


def _fallback_value_for_missing_column(column: dict[str, Any]) -> Any:
    name = str(column.get("name") or "")
    if name in {"player_names_json", "player_names"}:
        return "[]" if name.endswith("_json") else ""
    if name in {"team_name", "display_mode", "leaderboard_code"}:
        return ""
    coltype = str(column.get("type") or "").upper()
    if "INT" in coltype or "REAL" in coltype or "NUM" in coltype:
        return 0
    return ""


def _build_insert_payload_for_dst(src_row: dict[str, Any], dst: sqlite3.Connection, table_name: str) -> tuple[list[str], list[Any]]:
    columns = _table_columns(dst, table_name)
    names: list[str] = []
    values: list[Any] = []
    for column in columns:
        name = column["name"]
        if name in src_row:
            value = src_row.get(name)
        elif column.get("default") is not None:
            continue
        elif column.get("notnull"):
            value = _fallback_value_for_missing_column(column)
        else:
            value = None
        names.append(name)
        values.append(value)
    return names, values


def _insert_copied_db_row(dst: sqlite3.Connection, table_name: str, src_row: dict[str, Any], *, keep_id: bool = False) -> None:
    cols, params = _build_insert_payload_for_dst(src_row, dst, table_name)

    # The live game DB and the removed-games DB are separate SQLite files.
    # Child tables such as game_riddles use an INTEGER PRIMARY KEY named "id".
    # Copying that source id into the archive DB can collide with an id that
    # already belongs to another archived game, causing:
    #   UNIQUE constraint failed: game_riddles.id
    # Keep the stable game id in the games table, but let SQLite allocate fresh
    # row ids for copied child rows.
    if not keep_id:
        filtered = [(col, val) for col, val in zip(cols, params) if col != "id"]
        if filtered:
            cols, params = [item[0] for item in filtered], [item[1] for item in filtered]

    dst.execute(
        f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        tuple(params),
    )


def _run_json_path(game_id: str) -> Path:
    return RUN_JSON_DIR / f"{game_id}.json"


def _restore_game_from_run_json_backup(conn: sqlite3.Connection, game_id: str) -> bool:
    backup_path = _run_json_path(game_id)
    if not backup_path.exists():
        return False
    try:
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    game_row = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return False

    game_columns = {item["name"] for item in _table_columns(conn, "games")}
    game_updates: dict[str, Any] = {}
    if "players_count" in game_columns:
        game_updates["players_count"] = int(payload.get("players_count") or 0)
    if "leaderboard_code" in game_columns:
        code = str(payload.get("leaderboard_code") or "").strip()
        game_updates["leaderboard_code"] = code or None
    if "hint_count" in game_columns:
        hints_payload = payload.get("hints") if isinstance(payload.get("hints"), list) else []
        game_updates["hint_count"] = len(hints_payload)
    if game_updates:
        set_clause = ", ".join(f"{name} = ?" for name in game_updates.keys())
        conn.execute(f"UPDATE games SET {set_clause} WHERE id = ?", [*game_updates.values(), game_id])

    riddle_timings = payload.get("riddle_timings")
    restored_any = False
    if isinstance(riddle_timings, dict):
        conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        for riddle_name, timing in riddle_timings.items():
            timing = timing if isinstance(timing, dict) else {}
            conn.execute(
                """
                INSERT INTO game_riddles (
                    game_id, riddle, source, activated_at, solved_at,
                    solve_time_from_run_start_s, solve_time_from_activation_s, solved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    str(timing.get("node") or riddle_name),
                    str(timing.get("source") or "manual"),
                    timing.get("activated_at"),
                    timing.get("solved_at"),
                    timing.get("solve_time_from_run_start_s"),
                    timing.get("solve_time_from_activation_s"),
                    1 if bool(timing.get("solved")) else 0,
                ),
            )
            restored_any = True

    hints_payload = payload.get("hints")
    if isinstance(hints_payload, list):
        conn.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
        for hint in hints_payload:
            hint = hint if isinstance(hint, dict) else {}
            conn.execute(
                "INSERT INTO game_hints (game_id, at, riddle, hint_text) VALUES (?, ?, ?, ?)",
                (
                    game_id,
                    hint.get("at") or payload.get("ended_at") or payload.get("started_at") or payload.get("date") or "",
                    str(hint.get("riddle") or ""),
                    str(hint.get("hint_text") or ""),
                ),
            )
            restored_any = True

    return restored_any


def _restore_game_from_backup_db(conn: sqlite3.Connection, game_id: str) -> bool:
    candidates = []
    for path in sorted(GAME_DB_PATH.parent.glob("*.sqlite3*")):
        if path.resolve() != GAME_DB_PATH.resolve():
            candidates.append(path)
    if REMOVED_GAME_DB_PATH.exists():
        candidates.append(REMOVED_GAME_DB_PATH)
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen or not path.exists() or path.is_dir():
            continue
        seen.add(key)
        try:
            with sqlite3.connect(path) as backup:
                backup.row_factory = sqlite3.Row
                if not _table_exists(backup, "games"):
                    continue
                row = backup.execute("SELECT id FROM games WHERE id = ?", (game_id,)).fetchone()
                if row is None:
                    continue
                if _table_exists(backup, "game_riddles"):
                    riddles = backup.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
                    if riddles:
                        conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
                        cols = _intersecting_columns(backup, conn, "game_riddles")
                        for row in riddles:
                            values = dict(row)
                            conn.execute(
                                f"INSERT INTO game_riddles ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                                tuple(values.get(col) for col in cols),
                            )
                if _table_exists(backup, "game_hints"):
                    hints = backup.execute("SELECT * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
                    if hints:
                        conn.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
                        cols = _intersecting_columns(backup, conn, "game_hints")
                        for row in hints:
                            values = dict(row)
                            conn.execute(
                                f"INSERT INTO game_hints ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                                tuple(values.get(col) for col in cols),
                            )
                game_cols = {item["name"] for item in _table_columns(conn, "games")}
                backup_game = dict(backup.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())
                updates = {}
                for name in ("players_count", "hint_count", "leaderboard_code"):
                    if name in game_cols and name in backup_game:
                        updates[name] = backup_game.get(name)
                if updates:
                    set_clause = ", ".join(f"{name} = ?" for name in updates.keys())
                    conn.execute(f"UPDATE games SET {set_clause} WHERE id = ?", [*updates.values(), game_id])
                has_riddles = conn.execute("SELECT 1 FROM game_riddles WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
                has_hints = conn.execute("SELECT 1 FROM game_hints WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
                if has_riddles or has_hints:
                    return True
        except Exception:
            continue
    return False


def _restore_missing_game_details_from_backups(conn: sqlite3.Connection, game_id: str) -> bool:
    restored = _restore_game_from_backup_db(conn, game_id)
    if restored:
        return True
    return _restore_game_from_run_json_backup(conn, game_id)


def _ensure_table_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table_name: str) -> None:
    if _table_exists(dst, table_name):
        return
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"Das Datenbankschema der Tabelle {table_name} konnte nicht gelesen werden.")
    dst.execute(row[0])


def move_game_to_removed(game_id: str) -> None:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")

    REMOVED_GAMES_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(GAME_DB_PATH) as src, sqlite3.connect(REMOVED_GAME_DB_PATH) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row

        game_row = src.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            raise ValueError(f"Kein Spiel mit der ID {game_id} gefunden.")

        riddle_rows = src.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
        hint_rows = src.execute("SELECT * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()

        for table_name in ("games", "game_riddles", "game_hints"):
            _ensure_table_schema(src, dst, table_name)
            _ensure_missing_columns(src, dst, table_name)

        dst.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        dst.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))

        _insert_copied_db_row(dst, "games", dict(game_row), keep_id=True)

        for rows, table_name in ((riddle_rows, "game_riddles"), (hint_rows, "game_hints")):
            for row in rows:
                _insert_copied_db_row(dst, table_name, dict(row), keep_id=False)

        src.execute("DELETE FROM game_hints WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM games WHERE id = ?", (game_id,))

        dst.commit()
        src.commit()


def load_game_from_db(game_id: str) -> dict[str, Any]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
        if game is None:
            return {"game": None, "riddles": [], "hints": []}

        riddles = [_row_to_dict(row) for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()]
        hints = [_row_to_dict(row) for row in conn.execute(
            "SELECT rowid AS _rowid_, * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC",
            (game_id,),
        ).fetchall()]

        if not riddles and _restore_missing_game_details_from_backups(conn, game_id):
            game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
            riddles = [_row_to_dict(row) for row in conn.execute(
                "SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC",
                (game_id,),
            ).fetchall()]
            hints = [_row_to_dict(row) for row in conn.execute(
                "SELECT rowid AS _rowid_, * FROM game_hints WHERE game_id = ? ORDER BY rowid ASC",
                (game_id,),
            ).fetchall()]
            conn.commit()

        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())

    if "players_count" in game:
        game["players_count"] = parse_players_count_input(game.get("players_count"))
    for row in riddles:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])
    for row in hints:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])

    return {"game": game, "riddles": riddles, "hints": hints}


EDITABLE_TABLES: dict[str, dict[str, Any]] = {
    "games": {"blocked_columns": {"id"}},
    "game_riddles": {"blocked_columns": {"id", "game_id"}},
    "game_hints": {"blocked_columns": {"id", "game_id"}},
}


def update_db_row(table_name: str, rowid: int, updates: dict[str, Any]) -> None:
    config = EDITABLE_TABLES.get(table_name)
    if config is None:
        raise ValueError(f"Die Tabelle {table_name} kann nicht bearbeitet werden.")
    if not updates:
        return
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")

    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        columns_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if not columns_info:
            raise ValueError(f"Das Datenbankschema der Tabelle {table_name} konnte nicht gelesen werden.")

        editable_columns = {
            str(col[1])
            for col in columns_info
            if str(col[1]) not in set(config.get("blocked_columns") or set())
        }

        if table_name == "games":
            current = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Zeile {rowid} wurde in der Tabelle {table_name} nicht gefunden.")
            normalized_updates: dict[str, Any] = {}
            for key, value in updates.items():
                column = str(key or "").strip()
                if column == "players_count_display":
                    normalized_updates["players_count"] = parse_players_count_input(value)
                elif column == "duration_mmss":
                    normalized_updates["duration_s"] = parse_mmss_input(value)
                elif column == "hint_count_display":
                    normalized_updates["hint_count"] = max(int(float(str(value).strip() or "0")), 0)
                elif column in editable_columns:
                    normalized_updates[column] = value
                else:
                    raise ValueError(f"Die Spalte {column or key!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")

            if normalized_updates:
                set_clause = ", ".join(f"{column} = ?" for column in normalized_updates.keys())
                values = [normalized_updates[column] for column in normalized_updates.keys()]
                values.append(rowid)
                conn.execute(f"UPDATE games SET {set_clause} WHERE rowid = ?", values)
            conn.commit()
            return

        if table_name == "game_riddles":
            current = conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Zeile {rowid} wurde in der Tabelle {table_name} nicht gefunden.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get("game_id") or "")
            current_riddle = str(current_row.get("riddle") or "")

            pending_riddle_name = current_riddle
            pending_hint_count = None
            leaderboard_code_value = None
            row_name_overrides: dict[int, str] = {}
            solve_overrides: dict[int, float | None] = {}
            duration_overrides: dict[int, float | None] = {}
            direct_updates: dict[str, Any] = {}

            for key, value in updates.items():
                column = str(key or "").strip()
                if column == "solve_time_mmss":
                    solve_overrides[int(rowid)] = parse_mmss_input(value)
                elif column == "riddle_time_mmss":
                    if "solve_time_mmss" not in updates:
                        duration_overrides[int(rowid)] = parse_mmss_input(value)
                elif column == "hint_count_display":
                    pending_hint_count = max(int(float(str(value).strip() or "0")), 0)
                elif column in {"leaderboard_code", "leaderboard_code_display"}:
                    leaderboard_code_value = str(value or "").strip() or None
                elif column == "riddle":
                    pending_riddle_name = str(value or "").strip()
                    row_name_overrides[int(rowid)] = pending_riddle_name
                elif column in editable_columns:
                    direct_updates[column] = value
                else:
                    raise ValueError(f"Die Spalte {column or key!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")

            if pending_riddle_name != current_riddle:
                conn.execute(
                    "UPDATE game_hints SET riddle = ? WHERE game_id = ? AND riddle = ?",
                    (pending_riddle_name, game_id, current_riddle),
                )

            if direct_updates:
                set_clause = ", ".join(f"{column} = ?" for column in direct_updates.keys())
                values = [direct_updates[column] for column in direct_updates.keys()]
                values.append(rowid)
                conn.execute(f"UPDATE game_riddles SET {set_clause} WHERE rowid = ?", values)

            if row_name_overrides or solve_overrides or duration_overrides:
                _recalculate_game_riddle_solve_times(
                    conn,
                    game_id,
                    row_name_overrides=row_name_overrides,
                    solve_overrides=solve_overrides,
                    duration_overrides=duration_overrides,
                )

            if leaderboard_code_value is not None or any(str(k) in {"leaderboard_code", "leaderboard_code_display"} for k in updates):
                conn.execute("UPDATE games SET leaderboard_code = ? WHERE id = ?", (leaderboard_code_value, game_id))

            if pending_hint_count is not None:
                update_hint_rows_for_riddle(conn, game_id, pending_riddle_name, pending_hint_count)
                _refresh_game_hint_count(conn, game_id)

            conn.commit()
            return

        normalized_updates: dict[str, Any] = {}
        for key, value in updates.items():
            column = str(key or "").strip()
            if not column or column not in editable_columns:
                raise ValueError(f"Die Spalte {column or key!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")
            normalized_updates[column] = value

        if not normalized_updates:
            return

        current = conn.execute(f"SELECT rowid FROM {table_name} WHERE rowid = ?", (rowid,)).fetchone()
        if current is None:
            raise ValueError(f"Zeile {rowid} wurde in der Tabelle {table_name} nicht gefunden.")

        set_clause = ", ".join(f"{column} = ?" for column in normalized_updates.keys())
        values = [normalized_updates[column] for column in normalized_updates.keys()]
        values.append(rowid)
        conn.execute(f"UPDATE {table_name} SET {set_clause} WHERE rowid = ?", values)
        if table_name == "game_hints":
            game_row = conn.execute("SELECT game_id FROM game_hints WHERE rowid = ?", (rowid,)).fetchone()
            if game_row and game_row[0]:
                _refresh_game_hint_count(conn, str(game_row[0]))
        conn.commit()

def parse_json_payload(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("d"), dict):
            inner = dict(data["d"])
            for key in ("t", "ts", "time_valid", "type", "v", "id"):
                if key in data and key not in inner:
                    inner[key] = data[key]
            return inner
        return data
    except Exception:
        return None


def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
    for topic, qos in [
        (TOPIC_DASHBOARD_STATE, 0),
        (TOPIC_GAME_STATE, 0),
        ("+/state", 0),
        ("+/hb", 0),
        ("+/log", 0),
        ("maglock/lock/+/state", 0),
        ("lighting/mosfet/+/state", 0),
    ]:
        client.subscribe(topic, qos=qos)


def _handle_mqtt_message(msg: mqtt.MQTTMessage) -> None:
    topic = msg.topic
    if isinstance(topic, str) and topic.endswith("/log") and len(topic.split("/")) == 2:
        entry = parse_node_log(topic, msg.payload)
        if entry is not None:
            node_log_buffer.append(entry)
        return
    data = parse_json_payload(msg.payload)
    if data is None:
        return
    if topic == TOPIC_DASHBOARD_STATE:
        store.update_game_state(data)
        _maybe_resume_persisted_start_assignment()
        return
    if topic == TOPIC_GAME_STATE:
        # Fallback for older game masters, and phase/timer merge for the firmware-safe state.
        store.update_game_state(data, merge=("run" not in data))
        _maybe_resume_persisted_start_assignment()
        return
    if topic.endswith("/hb"):
        node_id = topic.split("/", 1)[0]
        store.update_node_hb(node_id, data)
        return
    if topic.startswith("maglock/lock/") and topic.endswith("/state"):
        parts = topic.split("/")
        if len(parts) >= 4:
            store.update_lock_state(parts[2], data)
        return
    if topic.startswith("lighting/mosfet/") and topic.endswith("/state"):
        parts = topic.split("/")
        if len(parts) >= 4:
            light_name = data.get("light") or LIGHT_NAME_BY_ID.get(parts[2]) or parts[2]
            store.update_light_state(str(light_name), data)
        return
    if topic.endswith("/state"):
        node_id = topic.split("/", 1)[0]
        store.update_node_state(node_id, data)
        return


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    try:
        _handle_mqtt_message(msg)
    except Exception:
        LOG.exception(
            "Dashboard MQTT callback failed topic=%s; exception suppressed so the network loop can continue",
            getattr(msg, "topic", ""),
        )


def mqtt_is_connected() -> bool:
    try:
        return bool(mqtt_client.is_connected())
    except Exception:
        return False


def mqtt_publish(topic: str, payload: dict[str, Any] | str) -> bool:
    try:
        if not mqtt_is_connected():
            return False
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        info = mqtt_client.publish(topic, body, qos=0, retain=False)
        return int(getattr(info, "rc", -1)) == int(getattr(mqtt, "MQTT_ERR_SUCCESS", 0))
    except Exception:
        return False


def mqtt_publish_batch(commands: list[tuple[str, dict[str, Any] | str]]) -> dict[str, Any]:
    queued = [mqtt_publish(topic, payload) for topic, payload in commands]
    queued_count = sum(result is True for result in queued)
    return {
        "mqtt_queued": queued_count == len(commands),
        "command_count": len(commands),
        "queued_count": queued_count,
        "failed_commands": [index for index, result in enumerate(queued) if result is not True],
        "partial": 0 < queued_count < len(commands),
    }


def current_raw_run_payload() -> dict[str, Any]:
    with store.lock:
        raw_game_state = json.loads(json.dumps(store.game_state))
    run = raw_game_state.get("run") if isinstance(raw_game_state.get("run"), dict) else None
    if not run:
        raise ValueError("Es ist noch kein aktueller Spieldurchlauf verfügbar.")
    return run


def website_json(path: str, payload: dict[str, Any] | None = None, method: str | None = None, timeout: int | None = None) -> dict[str, Any]:
    website_api_base, website_api_token = website_api_config()
    if not website_api_base:
        checked = ", ".join(str(path) for path in _candidate_env_paths())
        raise RuntimeError(f"ER1_WEBSITE_API_BASE ist im Dashboard nicht konfiguriert. Geprüft: {checked}")
    url = f"{website_api_base}{path if path.startswith('/') else '/' + path}"
    headers = {"Accept": "application/json"}
    body = None
    request_method = method or ("POST" if payload is not None else "GET")
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if website_api_token:
        # Support both names. The website accepts either header.
        headers["X-Game-Summary-Token"] = website_api_token
        headers["X-Dashboard-Token"] = website_api_token
        headers["Authorization"] = f"Bearer {website_api_token}"
    request_obj = urllib.request.Request(url, data=body, headers=headers, method=request_method)
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout or website_api_timeout()) as response:
            text = response.read().decode("utf-8", errors="replace")
            return json.loads(text or "{}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(text or "{}")
        except Exception:
            error_payload = {"error": text or str(exc)}
        raise RuntimeError(str(error_payload.get("error") or error_payload.get("message") or f"Website HTTP {exc.code}")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason or exc)) from exc


def post_website_json(path: str, payload: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
    return website_json(path, payload, method="POST", timeout=timeout)


def get_website_json(path: str, timeout: int | None = None) -> dict[str, Any]:
    return website_json(path, None, method="GET", timeout=timeout)


def _parse_tsv_table(text: str) -> list[dict[str, Any]]:
    lines = [line.rstrip("\n") for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        values = line.split("\t")
        row = {key: (values[i] if i < len(values) else "") for i, key in enumerate(header)}
        rows.append(row)
    return rows


def _sync_website_bookings_db_via_paramiko(cfg: dict[str, Any]) -> Path:
    remote_tmp = f"/tmp/er1_dashboard_bookings_{int(time.time() * 1000)}.sqlite3"
    local_path = Path(cfg["local_db_path"]).expanduser()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_tmp = local_path.with_suffix(local_path.suffix + ".tmp")
    backup_command = _remote_sqlite_backup_command(cfg, remote_tmp)
    client = _paramiko_connect(cfg, timeout=cfg["timeout"] + 10)
    try:
        _paramiko_exec(client, backup_command, timeout=cfg["timeout"] + 20, label="SSH-SQLite-Sicherung")
        try:
            with client.open_sftp() as sftp:
                sftp.get(remote_tmp, str(local_tmp))
        except Exception as exc:
            raise RuntimeError(f"Der SFTP-Download der Website-Datenbank ist fehlgeschlagen: {exc}") from exc
        local_tmp.replace(local_path)
    finally:
        try:
            _paramiko_exec(client, f"rm -f {shlex.quote(remote_tmp)}", timeout=cfg["timeout"] + 5, label="SSH-Bereinigung")
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
    return local_path


def sync_website_bookings_db_via_ssh() -> Path:
    """Create a consistent backup of the website app.db on Debian and copy it to the Pi.

    In password mode the dashboard uses Paramiko (pure Python SSH/SFTP). In
    passwordless/key mode it can still use the system ssh/scp commands. The
    remote backup is made through Python's sqlite3 module, so the Debian server
    does not need the sqlite3 command-line tool installed.
    """
    cfg = booking_ssh_config()
    if not cfg["host"] or not cfg["user"] or not cfg["db_path"]:
        raise RuntimeError("Die SSH-Synchronisierung der Website-Datenbank ist nicht konfiguriert. ER1_WEBSITE_SSH_HOST, ER1_WEBSITE_SSH_USER und ER1_WEBSITE_DB_PATH müssen gesetzt sein.")

    if _ssh_backend(cfg) == "paramiko":
        return _sync_website_bookings_db_via_paramiko(cfg)

    target = _ssh_target(cfg)
    remote_tmp = f"/tmp/er1_dashboard_bookings_{int(time.time() * 1000)}.sqlite3"
    local_path = Path(cfg["local_db_path"]).expanduser()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_tmp = local_path.with_suffix(local_path.suffix + ".tmp")
    backup_command = _remote_sqlite_backup_command(cfg, remote_tmp)
    _run_process(_ssh_base(cfg) + [backup_command], timeout=cfg["timeout"] + 20, label="SSH-SQLite-Sicherung")
    scp_cmd = [
        "scp",
        "-P", str(cfg["port"]),
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={cfg['timeout']}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{target}:{remote_tmp}",
        str(local_tmp),
    ]
    try:
        _run_process(scp_cmd, timeout=cfg["timeout"] + 20, label="SCP-Übertragung der Website-Datenbank")
        local_tmp.replace(local_path)
    finally:
        try:
            _run_process(_ssh_base(cfg) + [f"rm -f {shlex.quote(remote_tmp)}"], timeout=cfg["timeout"] + 5, label="SSH-Bereinigung")
        except Exception:
            pass
    return local_path

def _booking_label(row: dict[str, Any]) -> str:
    return " · ".join(part for part in [
        row.get("date", ""),
        row.get("slot", ""),
        f"{row.get('players', '')}P" if row.get("players") else "",
        row.get("customer_name") or row.get("customer_email") or row.get("booking_code", ""),
    ] if part)


def list_bookings_from_local_db(db_path: Path, limit: int = 1000) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"Die kopierte Website-Buchungsdatenbank wurde nicht gefunden: {db_path}")
    safe_limit = max(1, min(5000, int(limit or 1000)))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "bookings" not in tables:
            raise RuntimeError(f"Die kopierte Website-Datenbank enthält keine Buchungstabelle: {db_path}")
        rows = conn.execute(
            """
            SELECT id, booking_code, date, slot, players, language, customer_name, customer_email,
                   payment_method, payment_status, booking_status, total_cents, created_at, updated_at
            FROM bookings
            ORDER BY date DESC, slot DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        out.append(normalize_booking_selection({
            "id": row.get("id", ""),
            "bookingCode": row.get("booking_code", ""),
            "date": row.get("date", ""),
            "slot": row.get("slot", ""),
            "players": row.get("players", ""),
            "customerEmail": row.get("customer_email", ""),
            "customerName": row.get("customer_name", ""),
            "language": row.get("language", "de"),
            "paymentStatus": row.get("payment_status", ""),
            "bookingStatus": row.get("booking_status", ""),
            "totalCents": row.get("total_cents", ""),
            "label": _booking_label(row),
        }))
    return out


def list_bookings_via_ssh_copy(limit: int = 1000) -> tuple[list[dict[str, Any]], Path, bool]:
    local_db = sync_website_bookings_db_via_ssh()
    return list_bookings_from_local_db(local_db, limit), local_db, True


def _booking_is_cancelled(booking: dict[str, Any]) -> bool:
    status = f"{booking.get('bookingStatus', '')} {booking.get('paymentStatus', '')}".lower()
    status = "".join(char for char in unicodedata.normalize("NFD", status) if not unicodedata.combining(char))
    return bool(re.search(r"cancel|annull|storn|refund|ruckerstatt|rueckerstatt|ruckzahl|rimbors|cancellat", status))


def _last_sunday_of_month(year: int, month: int) -> int:
    next_month = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day.day - ((last_day.weekday() + 1) % 7)


def _rome_datetime_from_local(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return _rome_datetime_from_timestamp(value.timestamp())
    if EUROPE_ROME_TZ is not None:
        return value.replace(tzinfo=EUROPE_ROME_TZ)
    dst_start = datetime(value.year, 3, _last_sunday_of_month(value.year, 3), 3)
    dst_end = datetime(value.year, 10, _last_sunday_of_month(value.year, 10), 3)
    offset_hours = 2 if dst_start <= value < dst_end else 1
    return value.replace(tzinfo=timezone(timedelta(hours=offset_hours)))


def _rome_datetime_from_timestamp(value: float) -> datetime:
    if EUROPE_ROME_TZ is not None:
        return datetime.fromtimestamp(value, tz=EUROPE_ROME_TZ)
    utc_value = datetime.fromtimestamp(value, tz=timezone.utc)
    dst_start = datetime(utc_value.year, 3, _last_sunday_of_month(utc_value.year, 3), 1, tzinfo=timezone.utc)
    dst_end = datetime(utc_value.year, 10, _last_sunday_of_month(utc_value.year, 10), 1, tzinfo=timezone.utc)
    offset_hours = 2 if dst_start <= utc_value < dst_end else 1
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours)))


def _parse_start_clicked_at_ms(value: Any, *, now_epoch: float | None = None) -> datetime:
    if isinstance(value, bool):
        raise ValueError("Der Startzeitpunkt fehlt oder ist ungültig.")
    try:
        epoch_ms = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Der Startzeitpunkt fehlt oder ist ungültig.") from exc
    if not math.isfinite(epoch_ms) or not epoch_ms.is_integer():
        raise ValueError("Der Startzeitpunkt fehlt oder ist ungültig.")

    server_now = time.time() if now_epoch is None else float(now_epoch)
    clicked_at = epoch_ms / 1000.0
    if clicked_at < server_now - START_CLICK_MAX_AGE_S or clicked_at > server_now + START_CLICK_MAX_FUTURE_S:
        raise ValueError("Der Startzeitpunkt liegt außerhalb des zulässigen Zeitfensters.")
    return _rome_datetime_from_timestamp(clicked_at)


def _booking_start_in_rome(booking: dict[str, Any]) -> datetime | None:
    date_text = str(booking.get("date") or "").strip()
    slot_text = str(booking.get("slot") or "").strip()
    time_match = re.search(r"(\d{1,2})[:.](\d{2})", slot_text)
    if not date_text or not time_match:
        return None
    date_match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", date_text)
    if date_match:
        year, month, day = (int(value) for value in date_match.groups())
    else:
        date_match = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})", date_text)
        if not date_match:
            return None
        day, month, year = (int(value) for value in date_match.groups())
    try:
        return _rome_datetime_from_local(datetime(year, month, day, int(time_match.group(1)), int(time_match.group(2))))
    except (TypeError, ValueError):
        return None


def nearest_booking_for_start(bookings: list[dict[str, Any]], reference_time: datetime) -> dict[str, Any] | None:
    reference = _rome_datetime_from_local(reference_time)
    candidates: list[tuple[float, datetime, str, dict[str, Any]]] = []
    for raw_booking in bookings:
        booking = normalize_booking_selection(raw_booking)
        if booking.get("kind") != "booking" or _booking_is_cancelled(booking):
            continue
        appointment = _booking_start_in_rome(booking)
        if appointment is None:
            continue
        candidates.append((
            abs((appointment - reference).total_seconds()),
            appointment,
            str(booking.get("id") or booking.get("bookingCode") or ""),
            booking,
        ))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3] if candidates else None


def _select_booking_for_start_claim(
    claim: dict[str, Any],
    bookings: list[dict[str, Any]],
    reference_time: datetime,
) -> dict[str, Any] | None:
    snapshot = claim.get("booking_snapshot")
    if isinstance(snapshot, dict):
        selected = normalize_booking_selection(snapshot)
        return selected if selected.get("kind") == "booking" else None

    booking_id = str(claim.get("booking_id") or "").strip()
    if booking_id:
        for raw_booking in bookings:
            selected = normalize_booking_selection(raw_booking)
            if str(selected.get("id") or "").strip() == booking_id:
                return None if _booking_is_cancelled(selected) else selected
        return None

    return nearest_booking_for_start(bookings, reference_time)


def load_bookings_for_start_assignment(limit: int = 1000) -> list[dict[str, Any]]:
    try:
        bookings, _local_db, _fresh = list_bookings_via_ssh_copy(limit)
        return bookings
    except Exception as fresh_error:
        local_db = Path(booking_ssh_config()["local_db_path"]).expanduser()
        try:
            if local_db.exists():
                return list_bookings_from_local_db(local_db, limit)
        except Exception as cache_error:
            raise RuntimeError(
                f"Buchungen konnten weder aktuell noch aus dem Zwischenspeicher geladen werden: {fresh_error}; Zwischenspeicher: {cache_error}"
            ) from cache_error
        raise RuntimeError(f"Buchungen konnten nicht geladen werden: {fresh_error}") from fresh_error


def _booking_selection_key(booking: dict[str, Any]) -> tuple[Any, ...]:
    normalized = normalize_booking_selection(booking)
    return (
        normalized.get("kind"),
        str(normalized.get("id") or ""),
        str(normalized.get("bookingCode") or ""),
        str(normalized.get("customerEmail") or ""),
        str(normalized.get("date") or ""),
        str(normalized.get("slot") or ""),
        int(normalized.get("players") or 0),
        str(normalized.get("language") or "de"),
    )


def _booking_selection_identity(booking: dict[str, Any]) -> tuple[Any, ...]:
    return _booking_selection_key(booking)[:-1]


def publish_booking_selection(
    booking: dict[str, Any],
    *,
    expected_run_id: str = "",
    supersede_automatic: bool = False,
) -> dict[str, Any]:
    selected = normalize_booking_selection(booking)
    with store.lock:
        run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
        current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
        try:
            phase = int(store.game_state.get("phase", 0) or 0)
        except Exception:
            phase = 0
        if expected_run_id:
            if current_run_id != expected_run_id or not 3 <= phase <= 13:
                raise ValueError("Die Buchungszuordnung gehört nicht mehr zum aktiven Spiel.")
        cancellation_pending = store.start_assignment.get("cancellation_durability_pending") is True
        if supersede_automatic and (store.start_assignment.get("active") or cancellation_pending):
            assignment_run_id = str(store.start_assignment.get("run_id") or "").strip()
            if not current_run_id or assignment_run_id != current_run_id:
                raise ValueError("Die automatische Zuordnung gehört zu einem anderen vorbereiteten Lauf.")
            current_selection = normalize_booking_selection(store.selected_booking)
            if selected.get("kind") in {"empty", "test"}:
                raise ValueError("Während der automatischen Zuordnung kann keine leere oder Testbuchung gespeichert werden.")
            if _booking_selection_identity(current_selection) == _booking_selection_identity(selected):
                raise ValueError("Die Tipp-Sprache kann erst nach der automatischen Buchungszuordnung geändert werden.")
            cancellation_synced = store._set_start_assignment_locked({
                **store.start_assignment,
                "active": False,
                "cancel_requested": True,
                "cancellation_durability_pending": True,
                "intent_state": "terminal",
                "status": "manual",
                "message": "Die automatische Zuordnung wurde durch die manuelle Buchungsauswahl beendet.",
            })
            if not cancellation_synced:
                store.start_assignment["cancellation_durability_pending"] = True
                raise RuntimeError(
                    "Die automatische Zuordnung wurde nicht dauerhaft beendet; der manuelle Buchungsbefehl wurde nicht gesendet."
                )
            store.start_assignment["cancellation_durability_pending"] = False
        if supersede_automatic:
            selected = store.set_selected_booking(
                selected,
                expected_run_id=expected_run_id,
                require_durable=True,
            )
        payload: dict[str, Any] = {"cmd": "set_booking", "booking": selected}
        if expected_run_id:
            payload["expected_run_id"] = expected_run_id
        if not mqtt_publish(TOPIC_GAME_CMD, payload):
            raise RuntimeError("Der Buchungsbefehl konnte nicht an MQTT übergeben werden.")
    return selected


def update_hint_language_selection(language: Any, *, expected_run_id: str = "") -> dict[str, Any]:
    with store.lock:
        if store.start_assignment.get("active"):
            raise ValueError("Die Tipp-Sprache kann erst nach der automatischen Buchungszuordnung geändert werden.")

        run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
        current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
        try:
            phase = int(store.game_state.get("phase", 0) or 0)
        except Exception:
            phase = 0
        current_selection = normalize_booking_selection(store.selected_booking)
        if 3 <= phase <= 13 and current_selection.get("kind") not in {"booking", "test"}:
            raise ValueError("Ohne bereits ausgewählte Buchung kann die Tipp-Sprache im aktiven Spiel nicht geändert werden.")

        scoped_run_id = expected_run_id or (current_run_id if 3 <= phase <= 13 else "")
        selected = normalize_booking_selection({
            **current_selection,
            "language": normalize_hint_language(language),
        })
        selected = store.set_selected_booking(
            selected,
            expected_run_id=scoped_run_id,
            require_durable=True,
        )
        return publish_booking_selection(selected, expected_run_id=scoped_run_id)


def publish_hint_count_change(riddle: str, *, delta: int | None = None, count: int | None = None) -> int:
    name = str(riddle or "").strip()
    if not name:
        raise ValueError("Rätsel-ID fehlt")
    with store.lock:
        previous = max(0, int(store.local_hint_counts.get(name, 0) or 0))
        value = max(0, int(count or 0)) if count is not None else max(0, previous + int(delta or 0))
        store.local_hint_counts[name] = value
        try:
            save_hint_store(store.local_hint_counts)
        except Exception:
            store.local_hint_counts[name] = previous
            raise
        if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_hint_count", "riddle": name, "count": value}):
            store.local_hint_counts[name] = previous
            try:
                save_hint_store(store.local_hint_counts)
            except Exception:
                pass
            raise RuntimeError("Der Tippzähler konnte nicht an MQTT übergeben werden.")
        return value


def _start_assignment_worker_entry(claim_id: str) -> None:
    try:
        _run_start_booking_assignment(claim_id)
    except Exception:
        LOG.exception("Unhandled start-assignment worker failure claim_id=%s", claim_id)
    finally:
        with _start_assignment_workers_lock:
            _start_assignment_workers.pop(claim_id, None)


def _launch_start_assignment_worker(claim_id: str, *, resumed: bool = False) -> bool:
    with _start_assignment_workers_lock:
        existing = _start_assignment_workers.get(claim_id)
        if existing is not None:
            return True
        worker = threading.Thread(
            target=_start_assignment_worker_entry,
            args=(claim_id,),
            name=f"booking-assignment-{'resume-' if resumed else ''}{claim_id[:8]}",
            daemon=True,
        )
        _start_assignment_workers[claim_id] = worker
    try:
        worker.start()
    except Exception:
        with _start_assignment_workers_lock:
            if _start_assignment_workers.get(claim_id) is worker:
                _start_assignment_workers.pop(claim_id, None)
        LOG.critical("Could not launch start-assignment worker claim_id=%s", claim_id, exc_info=True)
        return False
    return True


def _publish_start_for_claim(claim_id: str) -> bool:
    with store.lock:
        claim = store.start_assignment
        if (
            str(claim.get("claim_id") or "") != claim_id
            or not claim.get("active")
            or claim.get("cancel_requested")
            or claim.get("intent_state") not in {"authorized", "published"}
        ):
            return False
        run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
        current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
        try:
            phase = int(store.game_state.get("phase", 0) or 0)
        except Exception:
            phase = 0
        if phase != 2 or current_run_id != str(claim.get("run_id") or "").strip():
            return False
        if not (claim.get("_intent_durable_runtime") or claim.get("_directory_barrier_confirmed")):
            LOG.critical("Start intent has no successful durability barrier and will not execute claim_id=%s", claim_id)
            return False
        if claim.get("_start_published_runtime"):
            return True

        if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "start"}):
            try:
                store._set_start_assignment_locked({
                    **claim,
                    "status": "start_pending",
                    "message": "Der Startbefehl wartet auf einen erneuten MQTT-Versuch.",
                })
            except Exception:
                LOG.error("Could not persist pending Start status claim_id=%s", claim_id, exc_info=True)
            return False

        claim["_start_published_runtime"] = True
        try:
            store._set_start_assignment_locked({
                **claim,
                "intent_state": "published",
                "start_published": True,
                "status": "waiting_for_run",
                "message": "Startbefehl gesendet. Der neu gestartete Lauf wird für die Buchungszuordnung abgewartet.",
            })
        except Exception:
            LOG.critical(
                "Start was queued but its published status could not be persisted claim_id=%s",
                claim_id,
                exc_info=True,
            )
        return True


def _ensure_active_start_assignment_worker() -> bool:
    with store.lock:
        claim = store.start_assignment
        if not claim.get("active") or claim.get("cancel_requested"):
            return False
        claim_id = str(claim.get("claim_id") or "").strip()
        run_id = str(claim.get("run_id") or "").strip()
        run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
        current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
        try:
            phase = int(store.game_state.get("phase", 0) or 0)
        except Exception:
            phase = 0
        durability_confirmed = bool(claim.get("_intent_durable_runtime") or claim.get("_directory_barrier_confirmed"))
        if (
            not claim_id
            or not run_id
            or current_run_id != run_id
            or claim.get("intent_state") not in {"authorized", "published"}
            or not durability_confirmed
            or not 2 <= phase <= 13
        ):
            return False
        resumed = bool(claim.get("_restored_from_disk"))

    if phase == 2 and not _publish_start_for_claim(claim_id):
        return False

    if phase >= 3:
        with store.lock:
            current_claim = store.start_assignment
            if (
                str(current_claim.get("claim_id") or "") != claim_id
                or not current_claim.get("active")
                or current_claim.get("cancel_requested")
            ):
                return False
            current_claim["_start_published_runtime"] = True
            if current_claim.get("intent_state") == "authorized":
                try:
                    store._set_start_assignment_locked({
                        **current_claim,
                        "intent_state": "published",
                        "start_published": True,
                        "status": "loading_bookings",
                        "message": "Spiel läuft. Die Buchungszuordnung wird fortgesetzt.",
                    })
                except Exception:
                    LOG.error("Could not persist authoritative Start state claim_id=%s", claim_id, exc_info=True)
    worker_started = _launch_start_assignment_worker(claim_id, resumed=resumed)
    if worker_started and resumed:
        with store.lock:
            current_claim = store.start_assignment
            if str(current_claim.get("claim_id") or "") == claim_id:
                current_claim["_restored_from_disk"] = False
    return worker_started


def _run_start_booking_assignment(claim_id: str) -> None:
    try:
        claim = store.get_start_assignment()
        if claim.get("claim_id") != claim_id or not claim.get("active") or claim.get("cancel_requested"):
            return
        reference_time = _rome_datetime_from_timestamp(float(claim["start_clicked_at_ms"]) / 1000.0)

        deadline = time.monotonic() + 30.0
        run_id = str(claim.get("run_id") or "").strip()
        run_confirmed = False
        while time.monotonic() < deadline:
            with store.lock:
                current_claim = store.start_assignment
                if current_claim.get("claim_id") != claim_id or not current_claim.get("active") or current_claim.get("cancel_requested"):
                    return
                try:
                    phase = int(store.game_state.get("phase", 0) or 0)
                except Exception:
                    phase = 0
                run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
                candidate_run_id = str(run.get("run_id") or run.get("id") or "").strip()
                if 3 <= phase <= 13 and candidate_run_id == run_id:
                    run_confirmed = True
                    try:
                        store._set_start_assignment_locked({
                            **current_claim,
                            "intent_state": "published",
                            "start_published": True,
                            "status": "loading_bookings",
                            "run_id": run_id,
                            "message": "Spiel läuft. Die zeitlich nächste Buchung wird im Hintergrund gesucht.",
                        })
                    except Exception:
                        LOG.error("Could not persist booking lookup status claim_id=%s", claim_id, exc_info=True)
                    break
                if phase not in {2} and not 3 <= phase <= 13:
                    store._set_start_assignment_locked({
                        **current_claim,
                        "active": False,
                        "intent_state": "terminal",
                        "status": "cancelled",
                        "message": "Die Buchungszuordnung wurde verworfen, weil kein neu gestarteter Lauf aktiv ist.",
                    })
                    return
            time.sleep(0.1)

        if not run_confirmed:
            store.update_start_assignment(
                claim_id,
                active=False,
                intent_state="terminal",
                status="failed",
                message="Der neu gestartete Lauf wurde nicht rechtzeitig bestätigt; es wurde keine Buchung zugeordnet.",
            )
            return

        claim = store.get_start_assignment()
        if claim.get("claim_id") != claim_id or not claim.get("active") or claim.get("cancel_requested"):
            return
        retry_snapshot = claim.get("_candidate_retry_snapshot")
        has_exact_candidate = isinstance(claim.get("booking_snapshot"), dict) or isinstance(retry_snapshot, dict)
        bookings = [] if has_exact_candidate else load_bookings_for_start_assignment(1000)
        if not isinstance(claim.get("booking_snapshot"), dict) and isinstance(retry_snapshot, dict):
            claim = {**claim, "booking_snapshot": retry_snapshot}
        selected = _select_booking_for_start_claim(claim, bookings, reference_time)
        if selected is None:
            claimed_booking_id = str(claim.get("booking_id") or "").strip()
            message = (
                "Die bereits ausgewählte Buchung ist nicht mehr verfügbar; es wurde keine andere Buchung zugeordnet."
                if claimed_booking_id
                else "Keine passende normale Buchung gefunden. Bitte die Buchung vor dem E-Mail-Versand manuell auswählen."
            )
            store.update_start_assignment(
                claim_id,
                active=False,
                intent_state="terminal",
                status="no_match",
                message=message,
            )
            return
        selected = normalize_booking_selection(selected)
        if selected.get("kind") != "booking" or not str(selected.get("id") or "").strip():
            raise ValueError("Die automatische Buchungsauswahl ist ungültig.")

        with store.lock:
            current_claim = store.start_assignment
            current_run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
            current_run_id = str(current_run.get("run_id") or current_run.get("id") or "").strip()
            try:
                phase = int(store.game_state.get("phase", 0) or 0)
            except Exception:
                phase = 0
            if current_claim.get("claim_id") != claim_id or not current_claim.get("active") or current_claim.get("cancel_requested") or current_run_id != run_id or not 3 <= phase <= 13:
                return
            existing_snapshot = current_claim.get("booking_snapshot") if isinstance(current_claim.get("booking_snapshot"), dict) else None
            exact_candidate = bool(
                existing_snapshot
                and _booking_selection_key(existing_snapshot) == _booking_selection_key(selected)
            )
            candidate_durable = bool(
                exact_candidate
                and current_claim.get("candidate_state") in {"selected", "published"}
                and (current_claim.get("_candidate_directory_synced") or current_claim.get("_directory_barrier_confirmed"))
            )
            if not candidate_durable:
                candidate_value = {
                    **current_claim,
                    "candidate_state": "selected",
                    "status": "candidate_selected",
                    "booking_id": str(selected.get("id") or ""),
                    "booking_snapshot": selected,
                    "message": "Passende Buchung gefunden; die Auswahl wird dauerhaft gespeichert.",
                }
                try:
                    candidate_synced = store._set_start_assignment_locked(candidate_value)
                except Exception:
                    current_claim["_candidate_retry_snapshot"] = json.loads(json.dumps(selected, ensure_ascii=False))
                    current_claim["status"] = "candidate_pending"
                    current_claim["message"] = "Die ausgewählte Buchung wartet auf einen erneuten Speicherversuch."
                    store.persistence_degraded = True
                    LOG.error("Automatic booking candidate could not be persisted claim_id=%s", claim_id, exc_info=True)
                    return
                if not candidate_synced:
                    store.start_assignment["_candidate_directory_synced"] = False
                    store.start_assignment["status"] = "candidate_pending"
                    store.start_assignment["message"] = "Die ausgewählte Buchung wartet auf eine bestätigte Speicherung."
                    LOG.critical("Automatic booking candidate is visible but not durably confirmed claim_id=%s", claim_id)
                    return
                store.start_assignment["_candidate_directory_synced"] = True

        # Persistence can block long enough for a manual override to arrive.
        # Reacquire and validate the claim before emitting the booking command.
        with store.lock:
            current_claim = store.start_assignment
            current_run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
            current_run_id = str(current_run.get("run_id") or current_run.get("id") or "").strip()
            try:
                phase = int(store.game_state.get("phase", 0) or 0)
            except Exception:
                phase = 0
            durable_snapshot = current_claim.get("booking_snapshot") if isinstance(current_claim.get("booking_snapshot"), dict) else None
            if (
                current_claim.get("claim_id") != claim_id
                or not current_claim.get("active")
                or current_claim.get("cancel_requested")
                or current_run_id != run_id
                or not 3 <= phase <= 13
                or current_claim.get("candidate_state") not in {"selected", "published"}
                or not durable_snapshot
                or _booking_selection_key(durable_snapshot) != _booking_selection_key(selected)
                or not (current_claim.get("_candidate_directory_synced") or current_claim.get("_directory_barrier_confirmed"))
            ):
                return

            if not current_claim.get("_booking_published_runtime"):
                try:
                    selected = publish_booking_selection(selected, expected_run_id=run_id)
                except RuntimeError as exc:
                    try:
                        store._set_start_assignment_locked({
                            **current_claim,
                            "status": "booking_publish_pending",
                            "message": f"Der Buchungsbefehl wartet auf einen erneuten MQTT-Versuch: {exc}",
                        })
                    except Exception:
                        LOG.error("Could not persist pending booking publication claim_id=%s", claim_id, exc_info=True)
                    return
                current_claim = store.start_assignment
                current_claim["_booking_published_runtime"] = True
                try:
                    store._set_start_assignment_locked({
                        **current_claim,
                        "candidate_state": "published",
                        "status": "waiting_for_apply",
                        "message": "Buchungsbefehl gesendet; Bestätigung des aktiven Laufs wird abgewartet.",
                    })
                except Exception:
                    LOG.critical(
                        "Booking was queued but its published status could not be persisted claim_id=%s",
                        claim_id,
                        exc_info=True,
                    )

        confirmation_deadline = time.monotonic() + 10.0
        expected_key = _booking_selection_key(selected)
        while time.monotonic() < confirmation_deadline:
            with store.lock:
                current_claim = store.start_assignment
                if current_claim.get("claim_id") != claim_id or not current_claim.get("active") or current_claim.get("cancel_requested"):
                    return
                try:
                    phase = int(store.game_state.get("phase", 0) or 0)
                except Exception:
                    phase = 0
                run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
                current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
                if current_run_id != run_id or not 3 <= phase <= 13:
                    store._set_start_assignment_locked({
                        **current_claim,
                        "active": False,
                        "intent_state": "terminal",
                        "status": "cancelled",
                        "message": "Die Buchungszuordnung wurde verworfen, weil der gestartete Lauf nicht mehr aktiv ist.",
                    })
                    return
                run_booking = run.get("booking") if isinstance(run.get("booking"), dict) else {}
                if run_booking and _booking_selection_key(run_booking) == expected_key:
                    store.set_selected_booking(selected, expected_run_id=run_id)
                    label = str(selected.get("label") or selected.get("bookingCode") or selected.get("id") or "Buchung")
                    store._set_start_assignment_locked({
                        **current_claim,
                        "active": False,
                        "intent_state": "terminal",
                        "candidate_state": "published",
                        "status": "assigned",
                        "booking_id": str(selected.get("id") or ""),
                        "message": f"Buchung automatisch zugeordnet: {label}",
                    })
                    return
            time.sleep(0.1)

        store.update_start_assignment(
            claim_id,
            active=False,
            intent_state="terminal",
            candidate_state="published",
            status="unconfirmed",
            message="Der Buchungsbefehl wurde gesendet, aber vom aktiven Lauf nicht rechtzeitig bestätigt.",
        )
    except Exception as exc:
        try:
            store.update_start_assignment(
                claim_id,
                status="retry_pending",
                message=f"Automatische Buchungszuordnung wird erneut versucht: {exc}",
            )
        except Exception:
            LOG.exception("Could not persist start-assignment worker failure claim_id=%s", claim_id)


def _resume_persisted_start_assignment() -> None:
    with store.lock:
        claim = store.start_assignment
        if not claim.get("active") or not claim.get("_restored_from_disk"):
            return
        if claim.get("cancel_requested"):
            store._set_start_assignment_locked({
                **claim,
                "active": False,
                "intent_state": "terminal",
                "_restored_from_disk": False,
            })
            return
        if not claim.get("_directory_barrier_confirmed"):
            LOG.critical("Persisted Start intent has no startup directory barrier claim_id=%s", claim.get("claim_id"))
            return
        claim_id = str(claim.get("claim_id") or "").strip()
        run_id = str(claim.get("run_id") or "").strip()
        run = store.game_state.get("run") if isinstance(store.game_state.get("run"), dict) else {}
        current_run_id = str(run.get("run_id") or run.get("id") or "").strip()
        try:
            phase = int(store.game_state.get("phase", 0) or 0)
        except Exception:
            phase = 0
        if not claim_id or not run_id or current_run_id != run_id or not 2 <= phase <= 13:
            return
        claimed_booking_id = str(claim.get("booking_id") or "").strip()
        claimed_booking_snapshot = claim.get("booking_snapshot") if isinstance(claim.get("booking_snapshot"), dict) else None
        run_booking = run.get("booking") if isinstance(run.get("booking"), dict) else {}
        current_booking_id = str(run_booking.get("id") or "").strip()
        snapshot_confirmed = bool(
            claimed_booking_snapshot
            and run_booking
            and _booking_selection_key(run_booking) == _booking_selection_key(claimed_booking_snapshot)
        )
        legacy_id_confirmed = bool(not claimed_booking_snapshot and claimed_booking_id and current_booking_id == claimed_booking_id)
        if phase >= 3 and (snapshot_confirmed or legacy_id_confirmed):
            confirmed_booking = claimed_booking_snapshot or run_booking
            store.set_selected_booking(confirmed_booking, expected_run_id=run_id)
            store._set_start_assignment_locked({
                **claim,
                "active": False,
                "intent_state": "terminal",
                "candidate_state": "published",
                "_restored_from_disk": False,
                "status": "assigned",
                "message": "Die bereits bestätigte Buchungszuordnung wurde nach dem Dashboard-Neustart übernommen.",
            })
            return


def _maybe_resume_persisted_start_assignment() -> None:
    try:
        _resume_persisted_start_assignment()
    except Exception:
        LOG.exception("Could not resume persisted start assignment; exception suppressed for the MQTT network loop")
    try:
        _ensure_active_start_assignment_worker()
    except Exception:
        LOG.exception("Could not ensure start-assignment worker; exception suppressed for the MQTT network loop")


def _send_summary_email_via_paramiko(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    remote_tmp = f"/tmp/er1_dashboard_summary_{int(time.time() * 1000)}.json"
    local_tmp = BASE_DIR / "data" / f"summary_payload_{int(time.time() * 1000)}.json"
    local_tmp.parent.mkdir(parents=True, exist_ok=True)
    local_tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    client = _paramiko_connect(cfg, timeout=cfg["timeout"] + 10)
    try:
        try:
            with client.open_sftp() as sftp:
                sftp.put(str(local_tmp), remote_tmp)
        except Exception as exc:
            raise RuntimeError(f"Der SFTP-Upload der Daten für die Spielzusammenfassung ist fehlgeschlagen: {exc}") from exc
        app_path = shlex.quote(cfg["app_path"])
        script = shlex.quote(cfg["summary_script"])
        remote_payload = shlex.quote(remote_tmp)
        remote_cmd = f"cd {app_path} && node {script} {remote_payload}"
        text = _paramiko_exec(client, remote_cmd, timeout=max(20, cfg["timeout"] + 30), label="SSH-Versand der Spielzusammenfassung")
        return _parse_summary_script_json(text)
    finally:
        try:
            local_tmp.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _paramiko_exec(client, f"rm -f {shlex.quote(remote_tmp)}", timeout=cfg["timeout"] + 5, label="SSH-Bereinigung")
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def send_summary_email_via_ssh(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = booking_ssh_config()
    if not cfg["host"] or not cfg["user"] or not cfg["app_path"]:
        raise RuntimeError("Der Versand der Spielzusammenfassung über Website-SSH ist nicht konfiguriert. ER1_WEBSITE_SSH_HOST, ER1_WEBSITE_SSH_USER und ER1_WEBSITE_APP_PATH müssen gesetzt sein.")

    if _ssh_backend(cfg) == "paramiko":
        return _send_summary_email_via_paramiko(payload, cfg)

    target = _ssh_target(cfg)
    remote_tmp = f"/tmp/er1_dashboard_summary_{int(time.time() * 1000)}.json"
    local_tmp = BASE_DIR / "data" / f"summary_payload_{int(time.time() * 1000)}.json"
    local_tmp.parent.mkdir(parents=True, exist_ok=True)
    local_tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    scp_cmd = [
        "scp",
        "-P", str(cfg["port"]),
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={cfg['timeout']}",
        "-o", "StrictHostKeyChecking=accept-new",
        str(local_tmp),
        f"{target}:{remote_tmp}",
    ]
    try:
        _run_process(scp_cmd, timeout=cfg["timeout"] + 20, label="SCP-Übertragung der Spielzusammenfassungsdaten")
        app_path = shlex.quote(cfg["app_path"])
        script = shlex.quote(cfg["summary_script"])
        remote_payload = shlex.quote(remote_tmp)
        remote_cmd = f"cd {app_path} && node {script} {remote_payload}"
        completed = _run_process(_ssh_base(cfg) + [remote_cmd], timeout=max(20, cfg["timeout"] + 30), label="SSH-Versand der Spielzusammenfassung")
        return _parse_summary_script_json(completed.stdout or "")
    finally:
        try:
            local_tmp.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _run_process(_ssh_base(cfg) + [f"rm -f {shlex.quote(remote_tmp)}"], timeout=cfg["timeout"] + 5, label="SSH-Bereinigung")
        except Exception:
            pass

@app.get("/")
def index() -> str:
    return render_template("index.html")




@app.get("/games")
def game_viewer() -> str:
    game_id = str(request.args.get("game_id", "")).strip()
    game = None
    riddles: list[dict[str, Any]] = []
    hints: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    error = str(request.args.get("error", "")).strip()
    message = str(request.args.get("message", "")).strip()
    summary_columns: list[str] = []
    riddle_columns: list[str] = []
    hint_columns: list[str] = []
    raw_rows: list[dict[str, Any]] = []

    try:
        games = list_games_from_db()
    except Exception as exc:
        error = error or str(exc)

    if game_id and not error:
        try:
            state = build_game_view_state(game_id)
            game = state["game"]
            riddles = state["riddles"]
            hints = state["hints"]
            hint_columns = state["hint_columns"]
            raw_rows = state["raw_rows"]
            if game is None:
                error = f"Kein Spiel mit der ID {game_id} gefunden."
            else:
                summary_columns = [col for col in ["id", "date", "players_count", "hint_count", "leaderboard_code"] if col in game]
                riddle_columns = ["riddle", "riddle_time_mmss", "hint_count_display", "skipped", "not_solved"]
        except Exception as exc:
            error = str(exc)

    return render_template(
        "game_viewer.html",
        game_id=game_id,
        game=game,
        riddles=riddles,
        hints=hints,
        games=games,
        error=error,
        message=message,
        db_path=str(GAME_DB_PATH),
        removed_db_path=str(REMOVED_GAME_DB_PATH),
        run_json_dir=str(RUN_JSON_DIR),
        summary_columns=summary_columns,
        riddle_columns=riddle_columns,
        hint_columns=hint_columns,
        serialize_db_value=serialize_db_value,
        editable_tables=EDITABLE_TABLES,
        raw_rows=raw_rows,
    )


@app.post("/games/delete/<game_id>")
def delete_game(game_id: str) -> Any:
    game_id = str(game_id or "").strip()
    if not game_id:
        return redirect(url_for("game_viewer", error="Spiel-ID fehlt."))
    try:
        move_game_to_removed(game_id)
        return redirect(url_for("game_viewer", message=f"Spiel {game_id} wurde nach {REMOVED_GAMES_DIR} verschoben."))
    except Exception as exc:
        return redirect(url_for("game_viewer", error=str(exc), game_id=game_id))

@app.post("/api/db/update")
def api_db_update() -> Any:
    data = request.get_json(force=True) or {}
    table_name = str(data.get("table", "")).strip()
    try:
        rowid = int(data.get("rowid"))
    except Exception:
        return jsonify({"ok": False, "error": "Die Zeilen-ID muss eine ganze Zahl sein."}), 400

    updates = data.get("updates") or {}
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "Die Änderungen müssen als Objekt übergeben werden."}), 400

    try:
        update_db_row(table_name, rowid, updates)
        return jsonify({"ok": True, "table": table_name, "rowid": rowid, "updated_columns": sorted(updates.keys())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/state")
def api_state() -> Any:
    _maybe_resume_persisted_start_assignment()
    response = jsonify(store.snapshot())
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/logs")
def api_logs() -> Any:
    try:
        query = parse_logs_query(request.args)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    result = node_log_buffer.query(**query)
    selected_nodes = query["nodes"]
    selected_levels = query["levels"]
    response = jsonify({
        "ok": True,
        **result,
        "nodes": [node for node in LOG_NODE_IDS if selected_nodes is None or node in selected_nodes],
        "levels": [level for level in LOG_LEVELS if selected_levels is None or level in selected_levels],
        "last_requested": _requested_log_levels_snapshot(),
        "mqtt_connected": mqtt_is_connected(),
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.post("/api/log-level")
def api_log_level() -> Any:
    try:
        node, level = validate_log_level_request(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    record = _publish_requested_log_level(node, level)
    if record is None:
        return jsonify({
            "ok": False,
            "node": node,
            "requested": level,
            "mqtt_queued": False,
            "applied": False,
            "error": "Die Log-Stufe konnte nicht an MQTT übergeben werden.",
        }), 503
    return jsonify({
        "ok": True,
        "node": node,
        "requested": level,
        "requested_at": record["requested_at"],
        "mqtt_queued": True,
        "applied": False,
        "qos": 0,
        "retained": False,
    })


@app.post("/api/phase")
def api_phase() -> Any:
    data = request.get_json(force=True) or {}
    action = str(data.get("action", "")).strip().lower()
    if action == "start":
        try:
            reference_time = _parse_start_clicked_at_ms(data.get("start_clicked_at_ms"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "start_assignment": store.get_start_assignment()}), 400
        try:
            claim, created = store.claim_start_assignment(reference_time)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "start_assignment": store.get_start_assignment()}), 409
        except Exception:
            LOG.error("Game start was blocked because its durable assignment claim could not be saved", exc_info=True)
            return jsonify({
                "ok": False,
                "error": "Der Spielstart wurde nicht gesendet, weil die Startzuordnung nicht sicher gespeichert werden konnte.",
                "start_assignment": store.get_start_assignment(),
            }), 503
        if not created:
            _maybe_resume_persisted_start_assignment()
            worker_started = _ensure_active_start_assignment_worker()
            claim = store.get_start_assignment()
            return jsonify({
                "ok": True,
                "idempotent": True,
                "mqtt_queued": bool(claim.get("_start_published_runtime")),
                "assignment_worker_started": worker_started,
                "start_assignment": claim,
            })
        claim_id = str(claim["claim_id"])
        directory_synced = bool(claim.pop("_directory_synced", getattr(store, "last_start_assignment_directory_synced", True)))
        if not directory_synced:
            LOG.critical(
                "Start intent is visible but not durably confirmed; Start publish is blocked claim_id=%s",
                claim_id,
            )
            return jsonify({
                "ok": False,
                "mqtt_queued": False,
                "error": "Der Spielstart wurde nicht gesendet, weil die Startabsicht nicht dauerhaft bestätigt werden konnte.",
                "start_assignment": store.get_start_assignment(),
            }), 503
        worker_started = _ensure_active_start_assignment_worker()
        current_claim = store.get_start_assignment()
        mqtt_queued = bool(current_claim.get("_start_published_runtime"))
        if not mqtt_queued:
            return jsonify({
                "ok": False,
                "mqtt_queued": False,
                "error": "Der Startbefehl konnte nicht an MQTT übergeben werden; die dauerhaft gespeicherte Startabsicht wird erneut versucht.",
                "start_assignment": current_claim,
            }), 503
        return jsonify({
            "ok": True,
            "mqtt_queued": True,
            "assignment_worker_started": worker_started,
            "start_assignment": current_claim,
        })
    if action in {"standby", "maintenance", "prepare"}:
        if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_mode", "mode": action}):
            return jsonify({"ok": False, "error": "Die Phasenaktion konnte nicht an MQTT übergeben werden."}), 503
        store.set_local_phase(action)
        return jsonify({"ok": True, "mqtt_queued": True})
    return jsonify({"ok": False, "error": "Ungültige Phasenaktion"}), 400




@app.get("/api/bookings")
def api_bookings() -> Any:
    bookings = [normalize_booking_selection(TEST_BOOKING_DEFAULT)]
    try:
        remote_bookings, local_db, fresh = list_bookings_via_ssh_copy(1000)
        bookings.extend(remote_bookings)
        return jsonify({
            "ok": True,
            "bookings": bookings,
            "source": "ssh-copy",
            "copiedDb": str(local_db),
            "fresh": fresh,
        })
    except Exception as exc:
        # Still allow the test booking. If a previous copied DB exists, use it as a fallback.
        cfg = booking_ssh_config()
        local_db = Path(cfg["local_db_path"]).expanduser()
        try:
            if local_db.exists():
                bookings.extend(list_bookings_from_local_db(local_db, 1000))
                return jsonify({
                    "ok": True,
                    "bookings": bookings,
                    "source": "cached-copy",
                    "copiedDb": str(local_db),
                    "warning": f"Die Buchungsdaten konnten nicht aktuell kopiert werden; die zwischengespeicherte Datenbank wird verwendet: {exc}",
                })
        except Exception as cache_exc:
            return jsonify({"ok": True, "bookings": bookings, "source": "test-only", "warning": f"Die Buchungsdaten konnten weder aktuell noch aus dem Zwischenspeicher geladen werden: {exc}; Zwischenspeicher: {cache_exc}"})
        return jsonify({"ok": True, "bookings": bookings, "source": "test-only", "warning": f"Die Buchungsdaten konnten nicht geladen werden: {exc}"})


@app.post("/api/select-booking")
def api_select_booking() -> Any:
    data = request.get_json(force=True) or {}
    expected_run_id = str(data.get("expected_run_id") or "").strip()
    try:
        if "hint_language" in data:
            selected = update_hint_language_selection(data.get("hint_language"), expected_run_id=expected_run_id)
        else:
            booking = normalize_booking_selection(data.get("booking") if isinstance(data.get("booking"), dict) else data)
            selected = publish_booking_selection(booking, expected_run_id=expected_run_id, supersede_automatic=True)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "start_assignment": store.get_start_assignment()}), 409
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except OSError as exc:
        LOG.error("Selected booking state could not be persisted", exc_info=True)
        return jsonify({"ok": False, "error": f"Die Buchungsauswahl konnte nicht sicher gespeichert werden: {exc}"}), 503
    players_count = int(selected.get("players") or 0)
    return jsonify({
        "ok": True,
        "booking": selected,
        "players_count": players_count,
        "mqtt_queued": True,
        "applied": False,
    })


@app.post("/api/send-summary-email")
def api_send_summary_email() -> Any:
    data = request.get_json(silent=True) or {}
    try:
        run = current_raw_run_payload()
        code = str(run.get("leaderboard_code") or run.get("leaderboardCode") or "").strip()
        if not code:
            return jsonify({"ok": False, "error": "Das Spiel hat noch keinen Ranglisten-Code. Beende zuerst das Spiel und versuche es danach erneut."}), 400
        selected_booking = normalize_booking_selection(data.get("booking") if isinstance(data.get("booking"), dict) else store.get_selected_booking())
        if selected_booking.get("players"):
            run = dict(run)
            run["players_count"] = int(selected_booking.get("players") or 0)
        payload = {
            "run": run,
            "leaderboardCode": code,
            "bookingCode": str(data.get("bookingCode") or data.get("booking_code") or selected_booking.get("bookingCode") or "").strip(),
            "booking": selected_booking,
            "bookingEmail": str(selected_booking.get("customerEmail") or "").strip(),
            "players": int(selected_booking.get("players") or 0),
            "testBooking": selected_booking.get("kind") == "test",
            # Hint for the website-side mailer: keep the summary email minimal.
            # Intended content: total time, hint count, highlighted leaderboard code,
            # then the leaderboard link. Older website scripts simply ignore this.
            "emailTemplate": "minimal_leaderboard_summary",
            "emailFields": ["total_time", "hint_count", "leaderboard_code", "leaderboard_link"],
            "leaderboardUrl": os.getenv("ER1_LEADERBOARD_URL", "https://escapeschenna.com/rangliste").strip(),
        }
        mode = os.getenv("ER1_SUMMARY_EMAIL_MODE", "ssh").strip().lower()
        if mode == "http":
            result = post_website_json("/api/game-summary/send", payload)
        else:
            result = send_summary_email_via_ssh(payload)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/players-count")
def api_players_count() -> Any:
    data = request.get_json(force=True) or {}
    try:
        players_count = parse_players_count_input(data.get("players_count", 0))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_players_count", "players_count": players_count}):
        return jsonify({"ok": False, "error": "Die Spielerzahl konnte nicht an MQTT übergeben werden."}), 503
    store.set_local_players_count(players_count)
    return jsonify({"ok": True, "players_count": players_count, "mqtt_queued": True})


@app.post("/api/solve")
def api_solve() -> Any:
    data = request.get_json(force=True)
    node = str(data.get("node", "")).strip()
    if not node:
        return jsonify({"ok": False, "error": "Rätsel-ID fehlt"}), 400
    if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "solve", "node": node, "riddle": node}):
        return jsonify({"ok": False, "error": "Der Gelöst-Befehl konnte nicht an MQTT übergeben werden."}), 503
    return jsonify({"ok": True, "node": node, "mqtt_queued": True})


@app.post("/api/lock")
def api_lock() -> Any:
    data = request.get_json(force=True)
    lock_id = str(data.get("lock", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    if lock_id not in {item["id"] for item in LOCKS}:
        return jsonify({"ok": False, "error": "Ungültiges Schloss"}), 400
    if action not in {"open", "close"}:
        return jsonify({"ok": False, "error": "Ungültige Aktion"}), 400
    result = mqtt_publish_batch([(TOPIC_MAGLOCK_CMD, {"cmd": action, "lock": lock_id})])
    if not result["mqtt_queued"]:
        return jsonify({
            "ok": False,
            **result,
            "applied": False,
            "error": "Der Schlossbefehl konnte nicht an MQTT übergeben werden.",
        }), 503
    return jsonify({"ok": True, **result, "applied": False})


@app.post("/api/light")
def api_light() -> Any:
    data = request.get_json(force=True)
    group_id = str(data.get("group", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    pct_raw = data.get("pct")
    cfg = LIGHT_GROUPS.get(group_id)
    if cfg is None:
        return jsonify({"ok": False, "error": "Ungültige Lichtgruppe"}), 400

    if group_id == "star_sky":
        if action not in {"on", "off"}:
            return jsonify({"ok": False, "error": "Der Sternenhimmel unterstützt nur Ein/Aus"}), 400
        result = mqtt_publish_batch([
            (TOPIC_STAR_SKY_CMD, {"cmd": "on" if action == "on" else "off"}),
            ("star_sky/sys/cmd", "SOLVE" if action == "on" else "DISABLE"),
            (TOPIC_LIGHTING_CMD, {"cmd": "turn_on" if action == "on" else "turn_off", "light": "r3_uv"}),
        ])
        if not result["mqtt_queued"]:
            return jsonify({
                "ok": False,
                **result,
                "applied": False,
                "error": "Die Sternenhimmel-Befehle konnten nicht vollständig an MQTT übergeben werden.",
            }), 503
        return jsonify({"ok": True, **result, "applied": False})

    if cfg.get("dimmable"):
        try:
            pct = max(0, min(100, int(pct_raw)))
        except Exception:
            pct = 100
        if action == "off":
            pct = 0
        elif action not in {"on", "off", "set_pct"}:
            return jsonify({"ok": False, "error": "Ungültige Aktion"}), 400
        result = mqtt_publish_batch([
            (TOPIC_LIGHTING_CMD, {"cmd": "set", "light": light_name, "pct": pct})
            for light_name in cfg["lights"]
        ])
        if not result["mqtt_queued"]:
            return jsonify({
                "ok": False,
                **result,
                "applied": False,
                "pct": pct,
                "error": "Die Lichtbefehle konnten nicht vollständig an MQTT übergeben werden.",
            }), 503
        return jsonify({"ok": True, **result, "applied": False, "pct": pct})

    if action not in {"on", "off"}:
        return jsonify({"ok": False, "error": "Ungültige Aktion"}), 400
    result = mqtt_publish_batch([
        (TOPIC_LIGHTING_CMD, {"cmd": "turn_on" if action == "on" else "turn_off", "light": light_name})
        for light_name in cfg["lights"]
    ])
    if not result["mqtt_queued"]:
        return jsonify({
            "ok": False,
            **result,
            "applied": False,
            "error": "Die Lichtbefehle konnten nicht vollständig an MQTT übergeben werden.",
        }), 503
    return jsonify({"ok": True, **result, "applied": False})


@app.post("/api/hints")
def api_set_hint_count() -> Any:
    data = request.get_json(force=True)
    riddle = str(data.get("riddle", "")).strip()
    if not riddle:
        return jsonify({"ok": False, "error": "Rätsel-ID fehlt"}), 400

    try:
        if "count" in data:
            count = publish_hint_count_change(riddle, count=int(data.get("count") or 0))
        else:
            delta = int(data.get("delta") or 0)
            if delta == 0:
                return jsonify({"ok": False, "error": "Änderung oder Anzahl fehlt"}), 400
            count = publish_hint_count_change(riddle, delta=delta)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "hint_count": count, "mqtt_queued": True})




@app.post("/api/riddle-time")
def api_riddle_time() -> Any:
    data = request.get_json(force=True) or {}
    riddle = _canonical_riddle_name(data.get("riddle") or data.get("node") or "")
    if not riddle:
        return jsonify({"ok": False, "error": "Rätsel-ID fehlt"}), 400
    raw_value = data.get("time_text", data.get("time", data.get("solve_time_s", data.get("seconds", 0))))
    try:
        seconds = parse_mmss_input(raw_value)
        seconds = max(0.0, float(seconds or 0.0))
    except Exception:
        return jsonify({"ok": False, "error": "Die Zeit muss als Sekunden, mm:ss oder hh:mm:ss angegeben werden."}), 400
    current_row = next((row for row in (store.snapshot().get("riddles") or []) if _canonical_riddle_name(row.get("id")) == riddle), None)
    if current_row and str(current_row.get("phase_state") or "pending") == "pending":
        return jsonify({"ok": False, "error": "Die Zeit eines noch nicht erreichten Rätsels kann nicht geändert werden."}), 400
    if not mqtt_publish(TOPIC_GAME_CMD, {"cmd": "set_riddle_time", "riddle": riddle, "solve_time_s": round(seconds, 3)}):
        return jsonify({"ok": False, "error": "Die Rätselzeit konnte nicht an MQTT übergeben werden."}), 503
    return jsonify({"ok": True, "riddle": riddle, "solve_time_s": round(seconds, 3), "mqtt_queued": True})


@app.post("/api/riddle-outcome")
def api_riddle_outcome() -> Any:
    data = request.get_json(force=True) or {}
    riddle = _canonical_riddle_name(data.get("riddle") or data.get("node") or "")
    if not riddle:
        return jsonify({"ok": False, "error": "Rätsel-ID fehlt"}), 400
    outcome = str(data.get("outcome") or data.get("status") or "").strip().lower().replace(" ", "_")
    if riddle == "sissi":
        return jsonify({"ok": False, "error": "Sissi darf nur über den normalen Gelöst-Befehl abgeschlossen werden."}), 400
    command: dict[str, Any]
    response: dict[str, Any]
    if outcome in {"skip", "skipped"}:
        advance = bool(data.get("advance", True))
        if advance:
            command = {"cmd": "skip_riddle", "riddle": riddle}
        else:
            command = {"cmd": "set_riddle_outcome", "riddle": riddle, "outcome": "skipped", "advance": False}
        response = {"riddle": riddle, "outcome": "skipped", "advance": advance}
    elif outcome in {"not_solved", "failed", "fail"}:
        advance = bool(data.get("advance", False))
        command = {"cmd": "mark_not_solved", "riddle": riddle, "advance": advance}
        response = {"riddle": riddle, "outcome": "not_solved", "advance": advance}
    elif outcome in {"reset", "reset_timing"}:
        if riddle not in {"prison", "wheel", "chains", "tangram", "magnet"}:
            return jsonify({"ok": False, "error": "Dieses Rätsel kann nicht sicher zurückgesetzt werden."}), 400
        command = {"cmd": "reset_riddle", "riddle": riddle}
        response = {"riddle": riddle, "outcome": "reset"}
    elif outcome in {"clear", "pending", ""}:
        command = {"cmd": "clear_riddle_outcome", "riddle": riddle}
        response = {"riddle": riddle, "outcome": "clear"}
    elif outcome == "solved":
        advance = bool(data.get("advance", False))
        command = {"cmd": "set_riddle_outcome", "riddle": riddle, "outcome": "solved", "advance": advance}
        response = {"riddle": riddle, "outcome": "solved", "advance": advance}
    else:
        return jsonify({"ok": False, "error": "Der Status muss übersprungen, nicht gelöst, gelöst, zurückgesetzt oder geleert sein."}), 400
    if not mqtt_publish(TOPIC_GAME_CMD, command):
        return jsonify({"ok": False, "error": "Der Rätselstatus konnte nicht an MQTT übergeben werden."}), 503
    return jsonify({"ok": True, **response, "mqtt_queued": True})
# ---- ER1 v2 dashboard overrides (new game_master DB schema) ----
RIDDLE_ALIASES = {
    "open_prison": "prison",
    "mount_wheel": "wheel",
    "rope_paths": "chains",
    "star_slider": "stars",
    "prison": "prison",
    "wheel": "wheel",
    "chains": "chains",
    "stars": "stars",
}
RIDDLE_ORDER_V2 = [
    "images", "piano", "prison", "wheel", "chains",
    "tangram", "magnet", "chess", "knocking", "candles", "stars", "sissi",
]
RIDDLE_LABELS_V2 = {
    "images": "Bilder",
    "piano": "Piano",
    "prison": "Gefängnis",
    "wheel": "Rad",
    "chains": "Ketten",
    "tangram": "Tangram",
    "magnet": "Magnetschlüssel",
    "chess": "Pferd",
    "knocking": "Klopfen",
    "candles": "Kerzen",
    "stars": "Sterne",
    "sissi": "Sissi",
}

PHASE_META = {
    0: {"name": "standby", "active": (), "solved": ()},
    1: {"name": "maintenance", "active": tuple(RIDDLE_ORDER_V2), "solved": ()},
    2: {"name": "prepare", "active": (), "solved": ()},
    3: {"name": "start", "active": ("images",), "solved": ()},
    4: {"name": "piano", "active": ("piano",), "solved": ("images",)},
    5: {"name": "prison", "active": ("prison",), "solved": ("images", "piano")},
    6: {"name": "wheel", "active": ("wheel",), "solved": ("images", "piano", "prison")},
    7: {"name": "chains", "active": ("chains",), "solved": ("images", "piano", "prison", "wheel")},
    8: {"name": "tangram_magnet", "active": ("tangram", "magnet"), "solved": ("images", "piano", "prison", "wheel", "chains")},
    9: {"name": "chess", "active": ("chess",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet")},
    10: {"name": "knocking", "active": ("knocking",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess")},
    11: {"name": "candles", "active": ("candles",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking")},
    12: {"name": "stars", "active": ("stars",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking", "candles")},
    13: {"name": "sissi", "active": ("sissi",), "solved": ("images", "piano", "prison", "wheel", "chains", "tangram", "magnet", "chess", "knocking", "candles", "stars")},
    14: {"name": "finished", "active": (), "solved": tuple(RIDDLE_ORDER_V2)},
}

RIDDLES = [
    {"id": "images", "label": "Bilder", "node_id": "images_piano", "manual": False},
    {"id": "piano", "label": "Piano", "node_id": "images_piano", "manual": False},
    {"id": "prison", "label": "Gefängnis", "node_id": None, "manual": True},
    {"id": "wheel", "label": "Rad", "node_id": None, "manual": True},
    {"id": "chains", "label": "Ketten", "node_id": None, "manual": True},
    {"id": "tangram", "label": "Tangram", "node_id": None, "manual": True},
    {"id": "magnet", "label": "Magnetschlüssel", "node_id": None, "manual": True},
    {"id": "chess", "label": "Pferd", "node_id": "chess", "manual": False},
    {"id": "knocking", "label": "Klopfen", "node_id": "knocking", "manual": False},
    {"id": "candles", "label": "Kerzen", "node_id": "candles", "manual": False},
    {"id": "stars", "label": "Sterne", "node_id": "star_slider", "manual": False},
    {"id": "sissi", "label": "Sissi", "node_id": None, "manual": True},
]
NODE_LABELS = [
    ("lighting", "Lichtsteuerung"),
    ("maglock", "Schlosssteuerung"),
    ("images_piano", "Bilder / Piano"),
    ("chess", "Pferd"),
    ("knocking", "Klopfen"),
    ("candles", "Kerzen"),
    ("star_slider", "Sternenschieber"),
    ("star_sky", "Sternenhimmel"),
]

PHASE_LABELS_DE = {
    0: "Bereitschaft",
    1: "Wartung",
    2: "Vorbereitung",
    3: "Bilder",
    4: "Piano",
    5: "Gefängnis",
    6: "Rad",
    7: "Ketten",
    8: "Tangram & Magnetschlüssel",
    9: "Pferd",
    10: "Klopfen",
    11: "Kerzen",
    12: "Sterne",
    13: "Sissi",
    14: "Beendet",
}

def pretty_phase_name(name: str) -> str:
    text = str(name or "").strip().lower()
    by_name = {
        "standby": "Bereitschaft", "maintenance": "Wartung", "prepare": "Vorbereitung",
        "start": "Bilder", "piano": "Piano", "prison": "Gefängnis", "wheel": "Rad",
        "chains": "Ketten", "rope": "Ketten", "tangram_magnet": "Tangram & Magnetschlüssel",
        "chess": "Pferd", "knocking": "Klopfen", "candles": "Kerzen",
        "stars": "Sterne", "sissi": "Sissi", "finished": "Beendet",
    }
    return by_name.get(text, str(name or "").replace("_", " ").strip().title())

LOCKS = [
    {"id": "r2", "label": "Raum 2", "kind": "toggle"},
    {"id": "r3", "label": "Raum 3", "kind": "toggle"},
    {"id": "images", "label": "Bildertür", "kind": "pulse"},
    {"id": "knocking", "label": "Klopftür", "kind": "pulse"},
    {"id": "slider", "label": "Schiebertür", "kind": "pulse"},
]
LIGHT_GROUPS = {
    "entrance": {"label": "Eingang", "lights": ["torch_stiege"], "dimmable": False},
    "r1": {"label": "Raum 1", "lights": ["r1_stuen", "r1_bild"], "dimmable": False},
    "r2_main": {"label": "R2 Pferd + Kiste", "lights": ["r2_chess", "r2_schronk"], "dimmable": False},
    "r2_torch": {"label": "R2 Fackel", "lights": ["torch_r2"], "dimmable": False},
    "r3_main": {"label": "R3 Schieber + Käfig", "lights": ["r3_slider", "r3_cage"], "dimmable": True},
    "r3_torch": {"label": "R2/R3 Fackel", "lights": ["torch_r2r3"], "dimmable": False},
    "star_sky": {"label": "Sternenhimmel", "lights": ["r3_uv"], "dimmable": False, "special": "star_sky"},
}

EDITABLE_TABLES = {
    "games": {"blocked_columns": {"id", "ended_at", "duration_s", "hint_count"}},
    "game_riddles": {"blocked_columns": {"id", "game_id", "riddle_key"}},
}


def _canonical_riddle_name(name: Any) -> str:
    return RIDDLE_ALIASES.get(str(name or "").strip(), str(name or "").strip())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _safe_bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value or '').strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on', 'checked', 'skipped', 'not_solved', 'not solved'}:
        return 1
    return 0


def _duration_and_progress_from_rows(rows: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    by_key = {_canonical_riddle_name(r.get('riddle_key')): max(0.0, _safe_float(r.get('solve_time_s'))) for r in rows}
    progress = {}
    elapsed = 0.0
    for key in ["images", "piano", "prison", "wheel", "chains"]:
        elapsed += by_key.get(key, 0.0)
        progress[key] = elapsed
    chain_end = progress.get("chains", elapsed)
    tangram_end = chain_end + by_key.get("tangram", 0.0)
    magnet_end = chain_end + by_key.get("magnet", 0.0)
    progress["tangram"] = tangram_end
    progress["magnet"] = magnet_end
    elapsed = max(tangram_end, magnet_end)
    for key in ["chess", "knocking", "candles", "stars", "sissi"]:
        elapsed += by_key.get(key, 0.0)
        progress[key] = elapsed
    return round(max(0.0, elapsed), 3), progress


def _refresh_game_hint_count(conn: sqlite3.Connection, game_id: str) -> int:
    total_hints = conn.execute("SELECT COALESCE(SUM(hint_count), 0) FROM game_riddles WHERE game_id = ?", (game_id,)).fetchone()[0]
    conn.execute("UPDATE games SET hint_count = ? WHERE id = ?", (int(total_hints or 0), game_id))
    return int(total_hints or 0)


def _refresh_game_duration_and_end(conn: sqlite3.Connection, game_id: str, explicit_duration_s: float | None = None) -> float | None:
    rows = [_row_to_dict(r) or {} for r in conn.execute("SELECT riddle_key, solve_time_s FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    duration_s = float(explicit_duration_s) if explicit_duration_s is not None else _duration_and_progress_from_rows(rows)[0]
    game_row = conn.execute("SELECT started_at, ended_at FROM games WHERE id = ?", (game_id,)).fetchone()
    if game_row is None:
        return duration_s
    started_at = _parse_iso_datetime(game_row[0])
    ended_at_value = game_row[1]
    next_ended_at = ended_at_value
    if started_at is not None:
        next_ended_at = (started_at + timedelta(seconds=max(0.0, duration_s))).isoformat(timespec='seconds')
        next_date = started_at.date().isoformat()
        conn.execute("UPDATE games SET date = ?, duration_s = ?, ended_at = ? WHERE id = ?", (next_date, duration_s, next_ended_at, game_id))
    else:
        conn.execute("UPDATE games SET duration_s = ?, ended_at = ? WHERE id = ?", (duration_s, next_ended_at, game_id))
    return duration_s


def _calculate_riddle_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = []
    has_run_start_times = False
    for source_row in rows:
        row = dict(source_row)
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key') or row.get('riddle'))
        solve_from_run_start = _seconds_or_none(row.get('solve_time_from_run_start_s'))
        row['_solve_from_run_start_seconds'] = solve_from_run_start
        if solve_from_run_start is not None:
            has_run_start_times = True
        canonical.append(row)
    _, progress = _duration_and_progress_from_rows(canonical)
    calculated = []
    for row in canonical:
        key = row.get('riddle_key')
        if key in {'tangram', 'magnet'}:
            anchor = progress.get('chains', 0.0)
        elif key == 'chess':
            anchor = max(progress.get('tangram', progress.get('chains', 0.0)), progress.get('magnet', progress.get('chains', 0.0)))
        elif key == 'images':
            anchor = 0.0
        else:
            idx = RIDDLE_ORDER_V2.index(key) if key in RIDDLE_ORDER_V2 else -1
            prev_key = RIDDLE_ORDER_V2[idx - 1] if idx > 0 else None
            anchor = progress.get(prev_key, 0.0) if prev_key else 0.0
        solve_from_run_start = row.get('_solve_from_run_start_seconds')
        if has_run_start_times and solve_from_run_start is not None:
            solve_total = max(0.0, float(solve_from_run_start))
            direct = max(0.0, solve_total - anchor)
        else:
            direct = max(0.0, _safe_float(row.get('solve_time_s')))
            solve_total = progress.get(key, direct)
        row['_anchor_seconds'] = anchor
        row['_riddle_seconds'] = direct
        row['_solve_seconds'] = solve_total
        calculated.append(row)
    return calculated


def _recalculate_game_riddle_solve_times(conn: sqlite3.Connection, game_id: str, *, row_name_overrides=None, solve_overrides=None, duration_overrides=None):
    row_name_overrides = dict(row_name_overrides or {})
    solve_overrides = dict(solve_overrides or {})
    duration_overrides = dict(duration_overrides or {})
    rows = [_row_to_dict(r) or {} for r in conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    if not rows:
        return []
    for row in rows:
        rid = int(row.get('_rowid_') or 0)
        if rid in row_name_overrides:
            row['riddle_key'] = _canonical_riddle_name(row_name_overrides[rid])
        if rid in solve_overrides:
            row['solve_time_s'] = max(0.0, _safe_float(solve_overrides[rid])) if solve_overrides[rid] is not None else 0.0
        elif rid in duration_overrides:
            row['solve_time_s'] = max(0.0, _safe_float(duration_overrides[rid])) if duration_overrides[rid] is not None else 0.0
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key'))
    for row in rows:
        conn.execute("UPDATE game_riddles SET riddle_key = ?, solve_time_s = ? WHERE rowid = ?", (row.get('riddle_key'), round(max(0.0, _safe_float(row.get('solve_time_s'))), 3), int(row.get('_rowid_') or 0)))
    _refresh_game_duration_and_end(conn, game_id)
    _refresh_game_hint_count(conn, game_id)
    return _calculate_riddle_timing_rows(rows)


def build_game_view_state(game_id: str) -> dict[str, Any]:
    loaded = load_game_from_db(game_id)
    game = loaded['game']
    riddles = loaded['riddles']
    if game is None:
        return {"game": None, "riddles": [], "hints": [], "hint_columns": [], "raw_rows": []}
    game['started_at_display'] = format_datetime_readable(game.get('started_at') or game.get('date'))
    game['ended_at_display'] = format_datetime_readable(game.get('ended_at'))
    game['duration_mmss'] = format_mmss(game.get('duration_s'))
    game['players_count_display'] = display_players_count(game.get('players_count'))
    game['hint_count_display'] = int(game.get('hint_count') or 0)
    game['leaderboard_code_display'] = serialize_db_value(game.get('leaderboard_code')) or ''
    rendered_riddles = []
    for row in _calculate_riddle_timing_rows(riddles):
        rendered = dict(row)
        rendered['riddle_label'] = RIDDLE_LABELS_V2.get(rendered.get('riddle_key'), rendered.get('riddle_key'))
        rendered['riddle_time_mmss'] = format_mmss(rendered.get('_riddle_seconds'))
        rendered['hint_count_display'] = int(rendered.get('hint_count') or 0)
        rendered['hints_display'] = serialize_db_value(rendered.get('hints')) or ''
        rendered['skipped_display'] = '1' if _safe_bool_int(rendered.get('skipped')) else '0'
        rendered['not_solved_display'] = '1' if _safe_bool_int(rendered.get('not_solved')) else '0'
        rendered_riddles.append(rendered)
    rendered_riddles.sort(key=lambda row: RIDDLE_ORDER_V2.index(row.get('riddle_key')) if row.get('riddle_key') in RIDDLE_ORDER_V2 else 999)
    raw_rows = [
        {"table": "games", "rowid": game.get('_rowid_'), "raw_json": json.dumps({k: v for k, v in game.items() if not str(k).startswith('_') and not str(k).endswith('_display') and not str(k).endswith('_mmss')}, ensure_ascii=False, indent=2, default=str)}
    ] + [
        {"table": "game_riddles", "rowid": row.get('_rowid_'), "raw_json": json.dumps({k: v for k, v in row.items() if not str(k).startswith('_') and not str(k).endswith('_display') and not str(k).endswith('_mmss')}, ensure_ascii=False, indent=2, default=str)}
        for row in rendered_riddles
    ]
    return {"game": game, "riddles": rendered_riddles, "hints": [], "hint_columns": [], "raw_rows": raw_rows}


def move_game_to_removed(game_id: str) -> None:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")
    REMOVED_GAMES_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(GAME_DB_PATH) as src, sqlite3.connect(REMOVED_GAME_DB_PATH) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        game_row = src.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game_row is None:
            raise ValueError(f"Kein Spiel mit der ID {game_id} gefunden.")
        riddle_rows = src.execute("SELECT * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()
        for table_name in ("games", "game_riddles"):
            _ensure_table_schema(src, dst, table_name)
            _ensure_missing_columns(src, dst, table_name)
        dst.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        _insert_copied_db_row(dst, "games", dict(game_row), keep_id=True)
        for row in riddle_rows:
            _insert_copied_db_row(dst, "game_riddles", dict(row), keep_id=False)
        src.execute("DELETE FROM game_riddles WHERE game_id = ?", (game_id,))
        src.execute("DELETE FROM games WHERE id = ?", (game_id,))
        dst.commit()
        src.commit()


def load_game_from_db(game_id: str) -> dict[str, Any]:
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")
    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        game = _row_to_dict(conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE id = ?", (game_id,)).fetchone())
        if game is None:
            return {"game": None, "riddles": [], "hints": []}
        riddles = [_row_to_dict(row) for row in conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE game_id = ? ORDER BY rowid ASC", (game_id,)).fetchall()]
    if 'players_count' in game:
        game['players_count'] = parse_players_count_input(game.get('players_count'))
    for row in riddles:
        for key in list(row.keys()):
            row[key] = _maybe_json(row[key])
        row['riddle_key'] = _canonical_riddle_name(row.get('riddle_key') or row.get('riddle'))
    return {"game": game, "riddles": riddles, "hints": []}


def update_db_row(table_name: str, rowid: int, updates: dict[str, Any]) -> None:
    config = EDITABLE_TABLES.get(table_name)
    if config is None:
        raise ValueError(f"Die Tabelle {table_name} kann nicht bearbeitet werden.")
    if not updates:
        return
    if not GAME_DB_PATH.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {GAME_DB_PATH}")
    with sqlite3.connect(GAME_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        columns_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        editable_columns = {str(col[1]) for col in columns_info if str(col[1]) not in set(config.get('blocked_columns') or set())}
        if table_name == 'games':
            current = conn.execute("SELECT rowid AS _rowid_, * FROM games WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Zeile {rowid} wurde in der Tabelle {table_name} nicht gefunden.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get('id') or '').strip()
            normalized_updates = {}
            for key, value in updates.items():
                column = str(key or '').strip()
                if column == 'players_count_display':
                    normalized_updates['players_count'] = parse_players_count_input(value)
                elif column in {'leaderboard_code', 'leaderboard_code_display'}:
                    normalized_updates['leaderboard_code'] = str(value or '').strip() or None
                elif column in {'date_display', 'started_at_display'}:
                    raise ValueError(f"Die Spalte {column!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")
                elif column in editable_columns:
                    normalized_updates[column] = value
                else:
                    raise ValueError(f"Die Spalte {column!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")
            if normalized_updates:
                set_clause = ', '.join(f"{column} = ?" for column in normalized_updates.keys())
                values = list(normalized_updates.values()) + [rowid]
                conn.execute(f"UPDATE games SET {set_clause} WHERE rowid = ?", values)
                if 'started_at' in normalized_updates:
                    _refresh_game_duration_and_end(conn, game_id)
            conn.commit()
            return
        if table_name == 'game_riddles':
            current = conn.execute("SELECT rowid AS _rowid_, * FROM game_riddles WHERE rowid = ?", (rowid,)).fetchone()
            if current is None:
                raise ValueError(f"Zeile {rowid} wurde in der Tabelle {table_name} nicht gefunden.")
            current_row = _row_to_dict(current) or {}
            game_id = str(current_row.get('game_id') or '').strip()
            direct_updates = {}
            solve_overrides = {}
            for key, value in updates.items():
                column = str(key or '').strip()
                if column in {'riddle_time_mmss', 'solve_time_s'}:
                    solve_overrides[int(rowid)] = parse_mmss_input(value) if column == 'riddle_time_mmss' else max(0.0, _safe_float(value))
                elif column in {'hint_count_display', 'hint_count'}:
                    direct_updates['hint_count'] = max(0, _safe_int(value))
                elif column in {'skipped', 'skipped_display'}:
                    direct_updates['skipped'] = _safe_bool_int(value)
                elif column in {'not_solved', 'not_solved_display'}:
                    direct_updates['not_solved'] = _safe_bool_int(value)
                elif column == 'hints':
                    direct_updates['hints'] = str(value or '')
                elif column in editable_columns:
                    direct_updates[column] = value
                else:
                    raise ValueError(f"Die Spalte {column!r} kann in der Tabelle {table_name} nicht bearbeitet werden.")
            if direct_updates:
                if direct_updates.get('skipped'):
                    direct_updates['not_solved'] = 0
                elif direct_updates.get('not_solved'):
                    direct_updates['skipped'] = 0
                set_clause = ', '.join(f"{column} = ?" for column in direct_updates.keys())
                values = list(direct_updates.values()) + [rowid]
                conn.execute(f"UPDATE game_riddles SET {set_clause} WHERE rowid = ?", values)
            if solve_overrides:
                _recalculate_game_riddle_solve_times(conn, game_id, solve_overrides=solve_overrides)
            else:
                _refresh_game_duration_and_end(conn, game_id)
                _refresh_game_hint_count(conn, game_id)
            conn.commit()
            return
        raise ValueError(f"Die Tabelle {table_name} kann nicht bearbeitet werden.")


def _reset_riddle_display_state_locked_v2(self):
    for node_id in ["images_piano", "chess", "knocking", "candles", "star_slider", "stars"]:
        self._clear_node_payload_locked(node_id)
    self.riddle_states["images"] = {"id": "images", "buttons": {}}
    self.riddle_states["piano"] = {"id": "piano", "played_notes": []}
    self.riddle_states["chess"] = {"id": "chess", "reader_labels": {}}
    self.riddle_states["knocking"] = {"id": "knocking", "tries": 0, "attempted_sequences": []}
    self.riddle_states["candles"] = {"id": "candles", "tries": 0, "attempted_sequences": []}
    empty_stars_state = {"id": "stars", "tries": 0, "attempted_star_signs": [], "reader_positions": {}}
    self.riddle_states["stars"] = dict(empty_stars_state)
    self.riddle_states["star_slider"] = {**empty_stars_state, "id": "star_slider"}
    self.local_hint_counts = {}
    save_hint_store(self.local_hint_counts)


def _update_node_state_v2(self, node_id: str, payload: dict[str, Any]) -> None:
    with self.lock:
        previous_node = self.node_states.get(node_id, {}) if isinstance(self.node_states.get(node_id), dict) else {}
        merged_node = dict(previous_node)
        merged_node.update(payload)
        self.node_states[node_id] = merged_node
        if node_id == 'images_piano':
            if self._is_images_payload(payload):
                prev = self.riddle_states.get('images', {}) if isinstance(self.riddle_states.get('images'), dict) else {}
                merged = dict(prev); merged.update(payload); merged['id'] = 'images'; self.riddle_states['images'] = merged; return
            if self._is_piano_payload(payload):
                prev = self.riddle_states.get('piano', {}) if isinstance(self.riddle_states.get('piano'), dict) else {}
                merged = dict(prev); merged.update(payload); merged['id'] = 'piano'
                played_notes = list(prev.get('played_notes') or [])
                encoded = str(payload.get('encoded') or '').strip()
                if encoded:
                    played_notes.append({'encoded': encoded, 'accepted': bool(payload.get('accepted', False))})
                merged['played_notes'] = played_notes[-40:]
                self.riddle_states['piano'] = merged; return
        riddle_id = _canonical_riddle_name(payload.get('id') or node_id)
        if riddle_id:
            prev = self.riddle_states.get(riddle_id, {}) if isinstance(self.riddle_states.get(riddle_id), dict) else {}
            merged = dict(prev); merged.update(payload); merged['id'] = riddle_id
            if riddle_id in {'knocking', 'candles'}:
                attempts = list(merged.get('attempted_sequences') or [])
                last_attempt = str(payload.get('last_attempt') or '').strip()
                if last_attempt and (not attempts or attempts[-1] != last_attempt):
                    attempts.append(last_attempt)
                merged['attempted_sequences'] = attempts
            elif riddle_id in {'stars', 'star_slider'}:
                attempts = list(merged.get('attempted_star_signs') or [])
                last_positions = payload.get('last_attempt_positions')
                if isinstance(last_positions, dict) and (not attempts or attempts[-1] != last_positions):
                    attempts.append(last_positions)
                merged['attempted_star_signs'] = attempts
            self.riddle_states[riddle_id] = merged


DashboardStore._reset_riddle_display_state_locked = _reset_riddle_display_state_locked_v2
DashboardStore.update_node_state = _update_node_state_v2

@classmethod
def _extract_star_slider_summary_any(cls, riddle_id: str, state_payload: dict[str, Any]):
    if riddle_id not in {'stars', 'star_slider'} or not state_payload:
        return None
    positions = state_payload.get('reader_positions') or {}
    current = cls._extract_star_slider_values(positions)
    if not current:
        current = cls._extract_star_slider_values(state_payload.get('reader_labels'))
    attempts = []
    for item in state_payload.get('attempted_star_signs') or []:
        if not isinstance(item, dict):
            continue
        vals = cls._extract_star_slider_values(item.get('positions') or item)
        if vals:
            attempts.append(vals)
    return {'current': current, 'attempts': attempts}
DashboardStore._extract_star_slider_summary = _extract_star_slider_summary_any

@staticmethod
def _extract_info_any(riddle_id: str, state_payload: dict[str, Any]) -> str:
    if not state_payload or riddle_id in {'images', 'piano', 'chess', 'knocking', 'candles', 'stars', 'star_slider'}:
        return ''
    generic = []
    for key, value in state_payload.items():
        if key in {'id', 'fw', 'up', 'ts', 'time_valid', 'buttons'}:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        generic.append(f"{key}: {value}")
        if len(generic) >= 4:
            break
    return '   '.join(generic)
DashboardStore._extract_info = _extract_info_any

_original_dashboard_snapshot = DashboardStore.snapshot

def _snapshot_german(self):
    data = _original_dashboard_snapshot(self)
    game = data.get("game") or {}
    phase = int(game.get("phase", 0) or 0)
    last_phase = game.get("last_phase")
    game["phase_name_pretty"] = PHASE_LABELS_DE.get(phase, pretty_phase_name(game.get("phase_name", "")))
    game["phase_display"] = f"{phase}: {game['phase_name_pretty']}"
    if game.get("recovery_restored"):
        game["phase_display"] += " (nach Neustart wiederhergestellt)"
    if last_phase is not None:
        try:
            game["last_phase_name_pretty"] = PHASE_LABELS_DE.get(int(last_phase), pretty_phase_name(game.get("last_phase_name", "")))
        except Exception:
            pass
    game["is_live"] = 3 <= phase <= 13
    data["game"] = game

    status_labels = {
        "solved": "Gelöst", "active": "Aktiv", "pending": "Ausstehend",
        "skipped": "Übersprungen", "not_solved": "Nicht gelöst", "reset": "Zurückgesetzt",
    }
    active_riddles = set(PHASE_META.get(phase, {}).get("active", ()))
    for row in data.get("riddles") or []:
        rid = _canonical_riddle_name(row.get("id"))
        row["label"] = RIDDLE_LABELS_V2.get(rid, row.get("label", rid))
        state_name = str(row.get("phase_state") or "pending")
        row["phase_state_label"] = status_labels.get(state_name, state_name)
        row["resettable"] = rid in {"prison", "wheel", "chains", "tangram", "magnet"}
        row["solve_advances"] = state_name == "active" or (state_name == "reset" and rid in active_riddles)
        row["can_solve"] = state_name in {"active", "reset"}
        row["is_current"] = state_name == "active"
    for node in data.get("nodes") or []:
        status = str(node.get("status") or "")
        if status.startswith("online ("):
            seconds = status.removeprefix("online (").removesuffix("s)")
            node["status"] = f"verbunden ({seconds} s)"
        elif status == "online":
            node["status"] = "verbunden"
        elif status == "offline":
            node["status"] = "nicht verbunden"
    for lock in data.get("locks") or []:
        lock["state_label"] = {"open": "offen", "closed": "geschlossen", "unknown": "unbekannt"}.get(lock.get("state_label"), lock.get("state_label"))
    return data

DashboardStore.snapshot = _snapshot_german

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=30)
mqtt_client.loop_start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("ER1_DASHBOARD_PORT", "8080")), debug=False)
