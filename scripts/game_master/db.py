from __future__ import annotations

import json
import random
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
                    players_count INTEGER NOT NULL DEFAULT 0,
                    hint_count INTEGER NOT NULL DEFAULT 0,
                    leaderboard_code TEXT
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
            self._ensure_games_column(conn, "leaderboard_code", "TEXT")
            self._ensure_games_column(conn, "players_count", "INTEGER NOT NULL DEFAULT 0")
            self._drop_player_names_column_if_present(conn)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_leaderboard_code ON games(leaderboard_code) WHERE leaderboard_code IS NOT NULL"
            )

    def _ensure_games_column(self, conn: sqlite3.Connection, column_name: str, column_type: str) -> None:
        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(games)").fetchall()
        }
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE games ADD COLUMN {column_name} {column_type}")

    def _drop_player_names_column_if_present(self, conn: sqlite3.Connection) -> None:
        existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()]
        if "player_names_json" not in existing_columns:
            return

        conn.executescript(
            """
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
            SELECT id, date, started_at, ended_at, duration_s, COALESCE(players_count, 0), hint_count, leaderboard_code
            FROM games_old;

            DROP TABLE games_old;
            """
        )

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

    def save_completed_run(self, run: CurrentRun) -> None:
        leaderboard_code = run.leaderboard_code or self.generate_leaderboard_code()
        run.leaderboard_code = leaderboard_code

        with self._connect() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO games (
                    id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
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
                SELECT id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code
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
                    "players_count": row[5],
                    "hint_count": row[6],
                    "leaderboard_code": row[7],
                }
            )
        return out
