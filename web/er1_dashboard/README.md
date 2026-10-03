# ER1 Dashboard

Single-page Flask dashboard for ER1.

## Features
- Game mode buttons for the 4 current modes
- Timer that counts up from the current run start
- Booking selector that pulls customer email and player count from the website
- Password-based SSH/SFTP booking sync via Paramiko, no local ssh/scp binary required
- Test booking with editable email/player count
- Lock controls that honor the retained pulse cooldown and boot-guard state
- Light group controls
- Immediate solve, confirmed skip, and minimum-one-second riddle time correction controls
- Maintenance-only hint-template editor backed by atomic local persistence
- Whitelisted physical-node restart controls using `<node>/sys/cmd` with the plain `REBOOT` payload
- Confirmed manual bulk restart in phase 0; individual physical nodes remain restartable in other phases except for the maglock controller. The Game Master also schedules the same command every 14 days at 04:00 Europe/Rome and waits for phase 0 if necessary
- Single-flight server-side booking assignment when an accepted game start creates the active run
- MQTT state polling backend

## Normal game finish

The normal finish does not use a separate `finish_game` command. Solving the active
`sissi` riddle sends the normal `solve` command. The game master records the Sissi
time, advances from phase 13 to phase 14, stops the timer, persists the run, and
generates the leaderboard code. The removed emergency finish control is unrelated
to this normal transition.

## Booking assignment

Preparing a new run always clears the previous booking and player count. Once the
Game Master confirms that prepared run, Start creates one server-side assignment
claim shared by all browser sessions. The HTTP request only queues Start; SSH copy,
Europe/Rome appointment ranking, and booking lookup run in a background worker.
The Start intent is made durable before MQTT publication and can be resumed for
the same prepared run after a dashboard restart. The worker durably records the
exact normalized booking candidate before publishing it, writes only to that run,
and marks the booking as assigned only after the Game Master reports it back.
Reloading a browser shows the same persisted assignment status. If no
non-cancelled booking matches, select one manually before sending the summary
email. A manual selection durably cancels an automatic lookup for the same run and
persists the manual selection before its MQTT command is published.

The persisted state machine advances `intent_state` from `authorized` to
`published` and finally `terminal`. Automatic booking work advances
`candidate_state` from `none` to `selected` and then `published`. A startup
directory barrier is required before a restored state may emit either command.

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
- `ER1_DASHBOARD_CLIENT_ID` optional override; otherwise port 8080 uses `er1_dashboard` and previews use `er1_dashboard_<port>`
- `ER1_WEBSITE_API_BASE` optional website URL used only when `ER1_SUMMARY_EMAIL_MODE=http`
- `ER1_WEBSITE_API_TOKEN` optional shared token for HTTP summary email mode, matching `GAME_SUMMARY_API_TOKEN` on the website

## Notes

- Uses `game/cmd` for mode, selected booking/player count, solve, skip, hints, and live time corrections.
- Uses `maglock/cmd` for lock control.
- Uses `lighting/cmd` for light control.
- Uses `star_sky/sys/cmd` plus `lighting/cmd` for the `star sky` row.
- Reset and clear controls are intentionally omitted from the live dashboard.
- In maintenance, riddle rows expose only immediate Solve; skip, time, and hint-count mutations require a started or completed run. Hint-template editing remains available in maintenance.
- Restarting all nodes or the maglock controller is restricted to phase 0. Other individual physical nodes can still be restarted in later phases.
- Lock, light, and diagnostics commands report local MQTT queue failures and possible partial batches; QoS 0 still provides no physical-device acknowledgment.
- On its first MQTT connection, the dashboard stores retained `WRN` log-level defaults for all nodes. Manual per-node diagnostic levels remain temporary and non-retained.
- Automatic booking matching is restricted to an inclusive ±30-minute Europe/Rome window. Cached SSH copies are eligible for automatic matching only while younger than `ER1_BOOKINGS_CACHE_MAX_AGE_S` (default 900 seconds); older copies remain available for warned manual selection.
- If the website server is unreachable, the locally started game continues without automatic booking assignment. Cached, test, or empty bookings remain available for manual selection. Summary-email failures do not change the completed run and can be retried later.
- Deferred security boundary: the dashboard control APIs have no application-level authentication and rely on the trusted escape-room LAN/deployment boundary. Adding authentication requires a coordinated deployment/client migration and is intentionally not changed here.


## Optional HTTP summary delivery

This section applies only when `ER1_SUMMARY_EMAIL_MODE=http`. Do not point `ER1_WEBSITE_API_BASE` at `192.168.0.111:3002` unless the Node app is actually listening on the LAN. In the normal nginx setup, use:

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

## Booking lookup from Debian website

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
