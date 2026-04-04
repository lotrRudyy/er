# ER1 Dashboard

Single-page Flask dashboard for ER1.

## Features
- Game mode buttons for the 4 current modes
- Timer that counts up from the current run start
- Spieleranzahl setzen über Zähler/Plus-Minus
- Lock controls
- Light group controls
- Solve buttons for riddles
- MQTT state polling backend

## Run

```bash
cd er1_dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://<pi-ip>:8080
```

## Environment variables

- `ER1_MQTT_HOST` default: `192.168.0.10`
- `ER1_MQTT_PORT` default: `1883`
- `ER1_DASHBOARD_PORT` default: `8080`

## Notes

- Uses `game/cmd` for mode, players_count, and solve.
- Uses `maglock/cmd` for lock control.
- Uses `lighting/cmd` for light control.
- Uses `star_sky/sys/cmd` plus `lighting/cmd` for the `star sky` row.
- `reset` is intentionally omitted.
