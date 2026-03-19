from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from models import CurrentRun


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
                '''
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_s REAL,
                    player_names_json TEXT NOT NULL,
                    hint_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS game_riddles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    riddle TEXT NOT NULL,
                    source TEXT NOT NULL,
                    activated_at TEXT,
                    solved_at TEXT,
                    solve_time_from_run_start_s REAL,
                    solve_time_from_activation_s REAL,
                    solved INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS game_hints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    riddle TEXT NOT NULL,
                    hint_text TEXT NOT NULL,
                    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
                );
                '''
            )

    def save_completed_run(self, run: CurrentRun) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO games (
                    id, date, started_at, ended_at, duration_s, player_names_json, hint_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    run.run_id,
                    run.date,
                    run.started_at,
                    run.ended_at,
                    run.duration_s,
                    json.dumps(run.players, ensure_ascii=False),
                    run.hint_count(),
                ),
            )

            conn.execute("DELETE FROM game_riddles WHERE game_id = ?", (run.run_id,))
            for timing in run.riddle_timings.values():
                conn.execute(
                    '''
                    INSERT INTO game_riddles (
                        game_id, riddle, source, activated_at, solved_at,
                        solve_time_from_run_start_s, solve_time_from_activation_s, solved
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        run.run_id,
                        timing.node,
                        timing.source,
                        timing.activated_at,
                        timing.solved_at,
                        timing.solve_time_from_run_start_s,
                        timing.solve_time_from_activation_s,
                        1 if timing.solved else 0,
                    ),
                )

            conn.execute("DELETE FROM game_hints WHERE game_id = ?", (run.run_id,))
            for hint in run.hints:
                conn.execute(
                    '''
                    INSERT INTO game_hints (game_id, at, riddle, hint_text)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (
                        run.run_id,
                        hint["at"],
                        hint["riddle"],
                        hint["hint_text"],
                    ),
                )

    def list_games(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                '''
                SELECT id, date, started_at, ended_at, duration_s, player_names_json, hint_count
                FROM games
                ORDER BY started_at DESC
                '''
            ).fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "date": row[1],
                    "started_at": row[2],
                    "ended_at": row[3],
                    "duration_s": row[4],
                    "player_names": json.loads(row[5]),
                    "hint_count": row[6],
                }
            )
        return out
