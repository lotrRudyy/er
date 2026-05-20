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



def ensure_outcome_columns(conn: sqlite3.Connection) -> None:
    cols = get_columns(conn, 'game_riddles')
    if 'skipped' not in cols:
        conn.execute('ALTER TABLE game_riddles ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0')
    if 'not_solved' not in cols:
        conn.execute('ALTER TABLE game_riddles ADD COLUMN not_solved INTEGER NOT NULL DEFAULT 0')
    conn.execute('UPDATE game_riddles SET skipped = 0 WHERE skipped IS NULL')
    conn.execute('UPDATE game_riddles SET not_solved = 0 WHERE not_solved IS NULL')
    conn.execute('UPDATE game_riddles SET not_solved = 0 WHERE skipped = 1 AND not_solved = 1')


def select_riddle_rows(conn: sqlite3.Connection) -> list[tuple]:
    cols = get_columns(conn, 'game_riddles')
    skipped_expr = 'skipped' if 'skipped' in cols else '0 AS skipped'
    not_solved_expr = 'not_solved' if 'not_solved' in cols else '0 AS not_solved'
    order_expr = 'id' if 'id' in cols else 'riddle_key'
    return conn.execute(
        f'''
        SELECT game_id, riddle_key, solve_time_s, hint_count, hints, {skipped_expr}, {not_solved_expr}
        FROM game_riddles
        ORDER BY game_id ASC, {order_expr} ASC
        '''
    ).fetchall()

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
        ensure_outcome_columns(cur_conn)
        src_rows = select_riddle_rows(src_conn)

        inserted = 0
        updated = 0
        skipped = 0
        touched_games: set[str] = set()

        for game_id, riddle_key, solve_time_s, hint_count, hints, skipped_flag, not_solved_flag in src_rows:
            game_exists = cur_conn.execute(
                'SELECT 1 FROM games WHERE id = ? LIMIT 1',
                (game_id,),
            ).fetchone()
            if not game_exists:
                skipped += 1
                continue

            existing = cur_conn.execute(
                '''
                SELECT id, solve_time_s, hint_count, hints, skipped, not_solved
                FROM game_riddles
                WHERE game_id = ? AND riddle_key = ?
                LIMIT 1
                ''',
                (game_id, riddle_key),
            ).fetchone()

            new_solve = round(float(solve_time_s or 0), 3)
            new_hint_count = int(hint_count or 0)
            new_hints = hints or ''
            new_skipped = 1 if bool(skipped_flag) else 0
            new_not_solved = 1 if bool(not_solved_flag) and not new_skipped else 0

            if existing is None:
                cur_conn.execute(
                    '''
                    INSERT INTO game_riddles (game_id, riddle_key, solve_time_s, hint_count, hints, skipped, not_solved)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (game_id, riddle_key, new_solve, new_hint_count, new_hints, new_skipped, new_not_solved),
                )
                inserted += 1
                touched_games.add(game_id)
                continue

            row_id, old_solve, old_hint_count, old_hints, old_skipped, old_not_solved = existing
            old_solve = round(float(old_solve or 0), 3)
            old_hint_count = int(old_hint_count or 0)
            old_hints = old_hints or ''
            old_skipped = 1 if bool(old_skipped) else 0
            old_not_solved = 1 if bool(old_not_solved) and not old_skipped else 0

            should_update = (
                old_solve <= 0 < new_solve
                or old_hint_count < new_hint_count
                or (not old_hints and bool(new_hints))
                or (not old_skipped and bool(new_skipped))
                or (not old_not_solved and bool(new_not_solved))
            )

            if should_update:
                cur_conn.execute(
                    '''
                    UPDATE game_riddles
                    SET solve_time_s = ?,
                        hint_count = CASE WHEN ? > hint_count THEN ? ELSE hint_count END,
                        hints = CASE WHEN TRIM(COALESCE(hints, '')) = '' THEN ? ELSE hints END,
                        skipped = CASE WHEN ? = 1 THEN 1 ELSE skipped END,
                        not_solved = CASE WHEN ? = 1 AND COALESCE(skipped, 0) = 0 THEN 1 ELSE not_solved END
                    WHERE id = ?
                    ''',
                    (new_solve if old_solve <= 0 < new_solve else old_solve,
                     new_hint_count, new_hint_count,
                     new_hints,
                     new_skipped,
                     new_not_solved,
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

        cur_conn.execute('UPDATE game_riddles SET not_solved = 0 WHERE skipped = 1 AND not_solved = 1')
        cur_conn.commit()

        print(f'Backfill source: {source_path}')
        print(f'Inserted rows: {inserted}')
        print(f'Updated rows: {updated}')
        print(f'Skipped rows: {skipped}')
        print(f'Touched games: {len(touched_games)}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
