from __future__ import annotations

import argparse
import sqlite3
import tempfile
from pathlib import Path

from migrate_v2 import migrate


def get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def is_v2_schema(conn: sqlite3.Connection) -> bool:
    cols = get_columns(conn, 'game_riddles')
    return {'game_id', 'riddle_key', 'solve_time_s', 'hint_count', 'hints'}.issubset(cols)


def ensure_v2_source(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        if is_v2_schema(conn):
            return path

    tmpdir = Path(tempfile.mkdtemp(prefix='gm_backfill_'))
    converted = tmpdir / 'converted_v2.sqlite3'
    migrate(str(path), str(converted))
    return converted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--current', required=True, help='Path to current v2 game_master.sqlite3')
    parser.add_argument('--backup', required=True, help='Path to backup DB (old or v2)')
    args = parser.parse_args()

    current_path = Path(args.current).resolve()
    backup_path = Path(args.backup).resolve()

    if not current_path.exists():
        raise SystemExit(f'Current DB not found: {current_path}')
    if not backup_path.exists():
        raise SystemExit(f'Backup DB not found: {backup_path}')

    source_path = ensure_v2_source(backup_path)

    with sqlite3.connect(current_path) as cur_conn, sqlite3.connect(source_path) as src_conn:
        cur_conn.execute('PRAGMA foreign_keys=ON')
        src_rows = src_conn.execute(
            '''
            SELECT game_id, riddle_key, solve_time_s, hint_count, hints
            FROM game_riddles
            ORDER BY game_id ASC, id ASC
            '''
        ).fetchall()

        inserted = 0
        updated = 0
        skipped = 0
        touched_games: set[str] = set()

        for game_id, riddle_key, solve_time_s, hint_count, hints in src_rows:
            game_exists = cur_conn.execute(
                'SELECT 1 FROM games WHERE id = ? LIMIT 1',
                (game_id,),
            ).fetchone()
            if not game_exists:
                skipped += 1
                continue

            existing = cur_conn.execute(
                '''
                SELECT id, solve_time_s, hint_count, hints
                FROM game_riddles
                WHERE game_id = ? AND riddle_key = ?
                LIMIT 1
                ''',
                (game_id, riddle_key),
            ).fetchone()

            new_solve = round(float(solve_time_s or 0), 3)
            new_hint_count = int(hint_count or 0)
            new_hints = hints or ''

            if existing is None:
                cur_conn.execute(
                    '''
                    INSERT INTO game_riddles (game_id, riddle_key, solve_time_s, hint_count, hints)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (game_id, riddle_key, new_solve, new_hint_count, new_hints),
                )
                inserted += 1
                touched_games.add(game_id)
                continue

            row_id, old_solve, old_hint_count, old_hints = existing
            old_solve = round(float(old_solve or 0), 3)
            old_hint_count = int(old_hint_count or 0)
            old_hints = old_hints or ''

            should_update = (
                old_solve <= 0 < new_solve
                or old_hint_count < new_hint_count
                or (not old_hints and bool(new_hints))
            )

            if should_update:
                cur_conn.execute(
                    '''
                    UPDATE game_riddles
                    SET solve_time_s = ?,
                        hint_count = CASE WHEN ? > hint_count THEN ? ELSE hint_count END,
                        hints = CASE WHEN TRIM(COALESCE(hints, '')) = '' THEN ? ELSE hints END
                    WHERE id = ?
                    ''',
                    (new_solve if old_solve <= 0 < new_solve else old_solve,
                     new_hint_count, new_hint_count,
                     new_hints,
                     row_id),
                )
                updated += 1
                touched_games.add(game_id)
            else:
                skipped += 1

        # Recalculate game-level hint_count from current game_riddles for touched games.
        for game_id in touched_games:
            total_hints = cur_conn.execute(
                'SELECT COALESCE(SUM(hint_count), 0) FROM game_riddles WHERE game_id = ?',
                (game_id,),
            ).fetchone()[0]
            cur_conn.execute(
                'UPDATE games SET hint_count = ? WHERE id = ?',
                (int(total_hints or 0), game_id),
            )

        cur_conn.commit()

        print(f'Backfill source: {source_path}')
        print(f'Inserted rows: {inserted}')
        print(f'Updated rows: {updated}')
        print(f'Skipped rows: {skipped}')
        print(f'Touched games: {len(touched_games)}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
