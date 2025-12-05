# MQTT Command Cheatsheet

Fast references for ER1 MQTT work. Constants:

- `LOCAL_BROKER = 192.168.0.10`
- `REMOTE_BROKER = 100.108.1.80`

Canonical timestamp pipeline (use verbatim for every logging command):

```
ts '[%d.%m.%Y %H:%M:%S.%N]' | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/'
```

## Lock Control

Lock IDs: `images`, `door_to_r2`, `door_to_r3`, `slider`, `knocking`.

### Local broker

```bash
mosquitto_pub -h 192.168.0.10 -t 'esc/ctrl/lock/<id>/cmd' -m "OPEN"
mosquitto_pub -h 192.168.0.10 -t 'esc/ctrl/lock/<id>/cmd' -m "CLOSE"
```

### Remote broker

```bash
mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/<id>/cmd' -m "OPEN"
mosquitto_pub -h 100.108.1.80 -t 'esc/ctrl/lock/<id>/cmd' -m "CLOSE"
```

Replace `<id>` with one of the canonical lock IDs.

## Live Logging (LOCAL broker only)

```bash
mosquitto_sub -h 192.168.0.10 -t 'esc/#' -v \
  | ts '[%d.%m.%Y %H:%M:%S.%N]' \
  | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/'
```

## Log-to-File Examples

### On the Pi (Linux)

```bash
mosquitto_sub -h 192.168.0.10 -t 'esc/#' -v \
  | ts '[%d.%m.%Y %H:%M:%S.%N]' \
  | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/' \
  >> /home/rudyy/er/logs/$(date +%Y-%m-%d).log
```

### On Windows

```powershell
mosquitto_sub -h 192.168.0.10 -t 'esc/#' -v `
  | ts '[%d.%m.%Y %H:%M:%S.%N]' `
  | sed -E 's/([0-9]{3})[0-9]{6}]/\1]/' `
  >> C:\er_logs\2025-12-04.log
```

## Scripts & Aliases

- Daily logging helpers live in `scripts/mqtt-logs.sh`. Use `live`, `tail`, or `grep` subcommands.
- Lock helpers live in `scripts/mqtt-locks.sh` with `open`/`close` actions.
- Source `scripts/aliases.er1.sh` to load the `log_*` and `lock_*` aliases (`log_live`, `log_tail`, `log_grep`, `log_help`, `lock_open`, `lock_close`).
