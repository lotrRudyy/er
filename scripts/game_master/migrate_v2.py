from __future__ import annotations

import sqlite3
from pathlib import Path

MAP = {
    "images": "images",
    "piano": "piano",
    "open_prison": "prison",
    "prison": "prison",
    "mount_wheel": "wheel",
    "wheel": "wheel",
    "rope_paths": "chains",
    "chains": "chains",
    "tangram": "tangram",
    "magnet": "magnet",
    "chess": "chess",
    "knocking": "knocking",
    "candles": "candles",
    "star_slider": "stars",
    "stars": "stars",
    "sissi": "sissi",
}
PRIORITY = {"manual_fix": 3, "manual": 2, "node": 1}


def migrate(old_path: str, new_path: str) -> None:
    old = Path(old_path)
    new = Path(new_path)
    if new.exists():
        new.unlink()
    conn = sqlite3.connect(new)
    try:
        conn.executescript("""
        ATTACH DATABASE '%s' AS old;
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
        CREATE TABLE game_riddles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            riddle_key TEXT NOT NULL,
            solve_time_s REAL NOT NULL DEFAULT 0,
            hint_count INTEGER NOT NULL DEFAULT 0,
            hints TEXT NOT NULL DEFAULT '',
            skipped INTEGER NOT NULL DEFAULT 0,
            not_solved INTEGER NOT NULL DEFAULT 0,
            UNIQUE(game_id, riddle_key)
        );
        INSERT INTO games (id, date, started_at, ended_at, duration_s, players_count, hint_count, leaderboard_code)
        SELECT id, date, started_at, ended_at, duration_s, COALESCE(players_count,0), COALESCE(hint_count,0), NULLIF(TRIM(leaderboard_code), '')
        FROM old.games;
        """ % str(old.resolve()).replace("'", "''"))
        rows = conn.execute("SELECT game_id, riddle, source, solve_time_from_run_start_s, solve_time_from_activation_s FROM old.game_riddles").fetchall()
        best = {}
        for game_id, riddle, source, run_s, act_s in rows:
            key = MAP.get(riddle)
            if not key:
                continue
            solve = float(act_s or 0) if float(act_s or 0) > 0 else float(run_s or 0)
            pri = PRIORITY.get(source or '', 0)
            cur = best.get((game_id, key))
            cand = (pri, solve)
            if cur is None or cand > cur[:2]:
                best[(game_id, key)] = (pri, solve)
        hint_rows = []
        try:
            hint_rows = conn.execute("SELECT game_id, riddle, hint_text FROM old.game_hints").fetchall()
        except sqlite3.OperationalError:
            hint_rows = []
        hint_map = {}
        for game_id, riddle, hint_text in hint_rows:
            key = MAP.get(riddle)
            if not key:
                continue
            rec = hint_map.setdefault((game_id, key), {"count": 0, "texts": []})
            rec["count"] += 1
            if hint_text:
                rec["texts"].append(str(hint_text))
        for (game_id, key), (_, solve) in best.items():
            hints = hint_map.get((game_id, key), {"count": 0, "texts": []})
            conn.execute(
                "INSERT INTO game_riddles (game_id, riddle_key, solve_time_s, hint_count, hints) VALUES (?, ?, ?, ?, ?)",
                (game_id, key, round(float(solve or 0), 3), int(hints["count"]), "\n---\n".join(hints["texts"]))
            )
        conn.execute("CREATE UNIQUE INDEX idx_games_leaderboard_code ON games(leaderboard_code) WHERE leaderboard_code IS NOT NULL AND TRIM(leaderboard_code) != ''")
        conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    migrate('data/game_master.sqlite3', 'data/game_master_v2.sqlite3')
    print('Wrote data/game_master_v2.sqlite3')
