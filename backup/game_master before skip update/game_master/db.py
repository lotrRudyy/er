from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from models import CurrentRun

RIDDLE_ORDER = [
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
]


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_s REAL,
                    players_count INTEGER NOT NULL DEFAULT 0,
                    hint_count INTEGER NOT NULL DEFAULT 0,
                    leaderboard_code TEXT
                );

                CREATE TABLE IF NOT EXISTS game_riddles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    riddle_key TEXT NOT NULL,
                    solve_time_s REAL NOT NULL DEFAULT 0,
                    hint_count INTEGER NOT NULL DEFAULT 0,
                    hints TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                    UNIQUE(game_id, riddle_key)
                );
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_leaderboard_code ON games(leaderboard_code) WHERE leaderboard_code IS NOT NULL AND TRIM(leaderboard_code) != ''"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_game_riddles_game_id ON game_riddles(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_game_riddles_riddle_key ON game_riddles(riddle_key)")

    def generate_leaderboard_code(self) -> str:
        with self._connect() as conn:
            for _ in range(100):
                code = f"{random.randint(0, 999999):06d}"
                exists = conn.execute(
                    "SELECT 1 FROM games WHERE leaderboard_code = ? LIMIT 1",
                    (code,),
                ).fetchone()
                if not exists:
                    return code
        raise RuntimeError("Could not generate unique leaderboard code")

    def _compute_effective_duration_s(self, run: CurrentRun) -> float:
        times = {
            key: float((run.riddle_timings.get(key).solve_time_s if run.riddle_timings.get(key) else 0) or 0)
            for key in RIDDLE_ORDER
        }
        serial_before_parallel = (
            times["images"]
            + times["piano"]
            + times["prison"]
            + times["wheel"]
            + times["chains"]
        )
        parallel_end_offset = max(times["tangram"], times["magnet"])
        duration_s = (
            serial_before_parallel
            + parallel_end_offset
            + times["chess"]
            + times["knocking"]
            + times["candles"]
            + times["stars"]
            + times["sissi"]
        )
        return round(max(0.0, duration_s), 3)

    def recalc_run(self, run: CurrentRun) -> None:
        duration_s = self._compute_effective_duration_s(run)
        run.duration_s = duration_s
        try:
            started_dt = datetime.fromisoformat(run.started_at)
            ended_dt = started_dt + timedelta(seconds=duration_s)
            run.ended_at = ended_dt.isoformat(timespec="seconds")
            run.date = started_dt.date().isoformat()
        except Exception:
            run.ended_at = None

    def save_completed_run(self, run: CurrentRun) -> None:
        leaderboard_code = run.leaderboard_code or self.generate_leaderboard_code()
        run.leaderboard_code = leaderboard_code
        self.recalc_run(run)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO games (
                    id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.date,
                    run.started_at,
                    run.ended_at,
                    run.duration_s,
                    int(run.players_count or 0),
                    run.hint_count(),
                    leaderboard_code,
                ),
            )

            conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (run.run_id,))
            for timing in run.riddle_timings.values():
                conn.execute(
                    """
                    INSERT INTO game_riddles (
                        game_id, riddle_key, solve_time_s, hint_count, hints
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        timing.riddle_key,
                        round(float(timing.solve_time_s or 0), 3),
                        int(timing.hint_count or 0),
                        timing.hints or "",
                    ),
                )

    def list_games(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code
                FROM games
                ORDER BY started_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row[0],
                "date": row[1],
                "started_at": row[2],
                "ended_at": row[3],
                "duration_s": row[4],
                "players_count": row[5],
                "hint_count": row[6],
                "leaderboard_code": row[7],
            }
            for row in rows
        ]

    def get_run_riddles(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT riddle_key, solve_time_s, hint_count, hints
                FROM game_riddles
                WHERE game_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "riddle_key": row[0],
                "solve_time_s": row[1],
                "hint_count": row[2],
                "hints": row[3],
            }
            for row in rows
        ]

    def recalc_run_by_id(self, run_id: str) -> None:
        with self._connect() as conn:
            game_row = conn.execute(
                "SELECT id, date, started_at, players_count, leaderboard_code FROM games WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not game_row:
                raise ValueError("Run not found")

            run = CurrentRun(
                run_id=game_row[0],
                date=game_row[1],
                started_at=game_row[2],
                started_monotonic=0.0,
                players_count=game_row[3],
                leaderboard_code=game_row[4],
            )

            rows = conn.execute(
                "SELECT riddle_key, solve_time_s, hint_count, hints FROM game_riddles WHERE game_id = ?",
                (run_id,),
            ).fetchall()

            for row in rows:
                from models import RiddleTiming
                run.riddle_timings[row[0]] = RiddleTiming(
                    riddle_key=row[0],
                    solve_time_s=float(row[1] or 0),
                    hint_count=int(row[2] or 0),
                    hints=row[3] or "",
                )

            self.recalc_run(run)

            conn.execute(
                "UPDATE games SET date = ?, ended_at = ?, duration_s = ?, hint_count = ? WHERE id = ?",
                (run.date, run.ended_at, run.duration_s, run.hint_count(), run_id),
            )
