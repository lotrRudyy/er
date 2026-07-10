# ER1 Dashboard

Single-page Flask dashboard for ER1.

## Features
- Game mode buttons for the 4 current modes
- Timer that counts up from the current run start
- Booking selector that pulls customer email and player count from the website
- Password-based SSH/SFTP booking sync via Paramiko, no local ssh/scp binary required
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


## Booking lookup from Debian website

Do not point `ER1_WEBSITE_API_BASE` at `192.168.0.111:3002` unless the Node app is actually listening on the LAN. In the normal nginx setup, use:

```env
ER1_WEBSITE_API_BASE=https://escapeschenna.com
```

Set the same token on both machines:

```env
# Pi dashboard .env
ER1_WEBSITE_API_TOKEN=replace-with-the-same-token

# Debian website .env
GAME_SUMMARY_API_TOKEN=replace-with-the-same-token
GAME_DASHBOARD_API_TOKEN=replace-with-the-same-token
```

For booking selection, the dashboard copies the website SQLite DB over SSH/SFTP and reads the copied DB locally:

```env
ER1_BOOKINGS_SOURCE=ssh-copy
ER1_WEBSITE_SSH_HOST=192.168.0.111
ER1_WEBSITE_SSH_USER=rudyy
ER1_WEBSITE_SSH_PORT=22
ER1_WEBSITE_SSH_PASSWORD='replace-with-password-or-leave-empty'
ER1_WEBSITE_SSH_BACKEND=auto
ER1_WEBSITE_REMOTE_PYTHON=python3
ER1_WEBSITE_DB_PATH=/home/rudyy/escapeschenna/data/app.db
ER1_LOCAL_BOOKINGS_DB_PATH=/home/rudyy/er1/web/data/website_app_bookings.sqlite3
```

When `ER1_WEBSITE_SSH_PASSWORD` is set, the dashboard uses Paramiko, so password-based SSH works without local `ssh`, `scp`, or `sshpass`. When the password is empty, it can still use passwordless OpenSSH/scp. The remote backup uses Python's built-in SQLite module, so the Debian server does not need the `sqlite3` command-line tool.

## Booking sync and summary email over SSH

The dashboard does not call the admin website API to load bookings. When you click
"Refresh bookings", the Pi SSHes into the Debian website server, creates a safe
SQLite backup of `/home/rudyy/escapeschenna/data/app.db`, copies that backup to
`/home/rudyy/er1/web/data/website_app_bookings.sqlite3`, and reads bookings from
that local copy.

The summary email is also sent through SSH by default. The Pi copies a JSON payload
to the Debian server and runs `node scripts/send-game-summary-email.js` inside the
website project, so the website keeps using its own SMTP configuration and database.

With password-based SSH, keep `ER1_WEBSITE_SSH_PASSWORD` in `.env` and reinstall requirements after updating:

```bash
cd er1_dashboard
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

With passwordless SSH instead, leave `ER1_WEBSITE_SSH_PASSWORD` empty and verify access from the Pi service user:

```bash
ssh-copy-id rudyy@192.168.0.111
ssh rudyy@192.168.0.111 "hostname && python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/home/rudyy/escapeschenna/data/app.db')
print(conn.execute('SELECT COUNT(*) FROM bookings').fetchone()[0])
PY"
```
