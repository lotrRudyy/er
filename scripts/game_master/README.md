# Game master

Run from your Pi repo at:

```bash
cd ~/er1/scripts/game_master
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Storage paths when run from `~/er1/scripts/game_master`:
- SQLite DB: `~/er1/scripts/game_master/data/game_master.sqlite3`
- Per-game JSON files: `~/er1/scripts/game_master/data/game_runs/`

Manual dashboard solve commands:
```json
{"cmd":"solve","node":"chains"}
{"cmd":"solve","node":"tangram"}
{"cmd":"solve","node":"magnet"}
```

Progression:
- images -> piano -> chains
- chains -> tangram and magnet
- only when tangram and magnet are both solved -> chess
- chess -> candles and knocking
- candles -> star_sky and star_slider
