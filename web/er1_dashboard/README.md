# ER1 Dashboard

Single-page Flask dashboard for ER1.

## Features
- Game mode buttons for the 4 current modes
- Timer that counts up from the current run start
- Booking selector that pulls customer email and player count from the website
- Test booking with editable email/player count
- Lock controls
- Light group controls
- Solve/skip/not-solved buttons and live riddle time editing
- Game finished and summary-email buttons
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

The dashboard now reads `.env` from the project folder or its parent. Copy `.env.example` to `.env` and adjust the values.

- `ER1_MQTT_HOST` default: `192.168.0.10`
- `ER1_MQTT_PORT` default: `1883`
- `ER1_DASHBOARD_PORT` default: `8080`
- `ER1_WEBSITE_API_BASE` website URL used for bookings and summary emails
- `ER1_WEBSITE_API_TOKEN` optional shared token, matching `GAME_SUMMARY_API_TOKEN` on the website

## Notes

- Uses `game/cmd` for mode, selected booking player count, solve, skip/not-solved, live times, and finish-game.
- Uses `maglock/cmd` for lock control.
- Uses `lighting/cmd` for light control.
- Uses `star_sky/sys/cmd` plus `lighting/cmd` for the `star sky` row.
- `reset` is intentionally omitted.
