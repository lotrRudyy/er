# ER System – Full Developer Workflow

This document describes the complete development, deployment, and maintenance workflow for the Escape Room control system.
It matches the canonical ER1 Protocol and defines the workflow used on both the PC and laptop development environments.

---

# 1. Repository Structure

```
er/
  docs/
    er/

  pc-scripts/          # PowerShell and deployment tooling (PC + laptop)
    er1_profile.ps1
    codex_commit.ps1

  er1/                 # PlatformIO firmware for ER1 devices
    firmware/
    pi-runtime/
      scripts/
      systemd/
      logs/
      config/

  er2/
    firmware/
    pi-runtime/

  er3/
    firmware/
    pi-runtime/
```

`pc-scripts` is the single source of truth for all developer-side tools.

---

# 2. Development Machine Setup

## PowerShell 7
Required.
VS Code must use:

```
C:\Program Files\PowerShell\7\pwsh.exe
```

## VS Code Profile

File:

```
$HOME\Documents\PowerShell\Microsoft.VSCode_profile.ps1
```

It loads `er1_profile.ps1` from one of two repo roots:

- **PC:** `C:\Users\rudol\Documents\Escape Room\er`
- **Laptop:** `C:\Users\Rudy\Documents\er`

The profile provides all ER commands (`er1 push`, `er1 deploy`, `er1 ota`, etc.).

---

# 3. Commands Defined in `er1_profile.ps1`

## 3.1 `er1 push`
Stages → commits → pushes from the repo root. Prints the current branch and
upstream, sets the upstream to `origin/<branch>` if missing, and refuses to run
outside the repo.

```
er1 push "updated chess fsm"
```

## 3.2 `er1 deploy`
Deploys the Pi runtime (`er1/pi-runtime`) to `/home/rudyy/er1`.

```
er1 deploy           # runtime mode (scripts/systemd/docs/config template)
er1 deploy full      # mirror the whole pi-runtime tree
```

### Deployment behavior

- Uses `rsync -avz --delete` if available, `scp -r` fallback otherwise.
- Syncs scripts/, systemd/, docs/, and config/ (skipping `config/local.env`).
- Leaves `/home/rudyy/er1/logs/` untouched.
- Re-applies execute bits on the remote runtime tree.
- Prints each rsync/scp command before running it so you know what will happen.
- Follow deployment with `er1 push "<msg>"` so Pi + Git stay aligned.

---

## 3.3 `er1-ota`
Triggers OTA update for a specific ESP32 device.

Correct command:

```
er1-ota <Dev>
```

Which internally runs:

```
pwsh ota.ps1 -Target <Dev>
```

Example:

```
er1-ota images_piano
```

The OTA binary must exist at:

```
/firmware/<Dev>.bin
```

---

## 3.4 Logging Commands

### Live log stream
```
er1 log -live
```

### Filter for device
```
er1 log chess
```

### All logs through Pi’s logging system
All logs originate from `/home/rudyy/er1/scripts/mqtt_logs.sh`.

---

## 3.5 Lock Commands
High-level helpers that publish to:

```
maglock/lock/<id>/cmd
```

Available lock IDs:

- `images`
- `r2`
- `r3`
- `slider`
- `knocking`

Commands:

```
er1-lock-images
er1-lock-r2
er1-lock-r3
er1-lock-slider
er1-lock-knocking
```

Legacy IDs `door_to_r2` and `door_to_r3` are deprecated.

---

# 4. Deployment Workflow in Detail

## 4.1 What gets deployed

Pi runtime directory after deploy:

```
/home/rudyy/er1/
  scripts/
  systemd/
  docs/
  config/local.env      # preserved
  logs/                 # preserved
```

`er1 deploy` (runtime mode) replaces only files under:

- `/scripts/`
- `/systemd/`
- `/docs/`
- `config/local.env.example`

It does **not** delete logs or overwrite `config/local.env`.

---

## 4.2 Deploy Command (`er1 deploy`)

Deploy steps:

1. Detect correct repo root (PC or laptop) and Pi target via `$er1Pi`.
2. Validate the Pi is reachable (`ssh $er1Pi`).
3. Sync runtime using `rsync -avz --delete` (or `scp -r` fallback when rsync is missing).
4. Re-apply execute bits on `~/er1/er1` and scripts/.
5. Print a reminder to commit+push so Git matches what was deployed.

Run deploy before using OTA, because Pi-side publisher must exist.

`er1 deploy full` mirrors the entire `pi-runtime/` tree with the same safeguards.

If services need a bounce after copying, restart them manually, e.g.:

```
ssh rudyy@100.108.1.80 "sudo systemctl restart er1-runtime.service"
```

---

# 5. OTA Workflow

OTA tool:

```
firmware/ota.ps1
```

Correct usage:

```
pwsh ota.ps1 -Target <Dev>
```

Example:

```
pwsh ota.ps1 -Target images_piano
```

OTA steps:

1. Build firmware via PlatformIO (requires `OTA_PSK` at build time; retrieve it from `/etc/er1/ota_psk` on the Pi without storing locally)
2. Upload `<Dev>.bin` into `/home/rudyy/firmware/` on the Pi (handled by `ota.ps1`)
3. `ota.ps1` SSHes into the Pi to run `~/er1/scripts/ota_publish.py --dev <Dev> --url /firmware/<Dev>.bin --broker 192.168.0.10`, which reads `/etc/er1/ota_psk`, computes sha256 + HMAC, and publishes `UPDATE sha256=... hmac=... url=...` to `<Dev>/cmd`
4. ESP32 fetches OTA binary over HTTP
5. ESP32 reboots
6. Node publishes new heartbeat with updated FW_VERSION

---

# 6. Logging System (MQTT → File)

Logging is centralized on ER1 Pi.

Logs stored at:

```
/home/rudyy/er1/logs/er1-DD.MM.YYYY.log
```

Format:

```
YYYY.MM.DD HH:MM:SS.mmm topic payload
```

Timestamp generated by:

```
date +"%Y.%m.%d %H:%M:%S.%3N"
```

Logging controller script:

```
/home/rudyy/er1/scripts/mqtt_logs.sh
```

Commands:

```
mqtt_logs.sh daemon
mqtt_logs.sh live
mqtt_logs.sh tail
mqtt_logs.sh grep <pattern>
```

Systemd unit:

```
er1-mqtt-log.service
```

---

# 7. Runtime Systemd Unit

ER1 runtime service:

```
/home/rudyy/er1/systemd/er1-runtime.service
```

Starts the runtime binary and supervises it.

After deployment, this unit is restarted automatically.

---

# 8. Adding a New Device

Steps:

1. Add a PlatformIO environment to `er1/firmware/platformio.ini`
2. Add `<Dev>.bin` to `deviceMap` in `ota.ps1`
3. Add lock or diagnostic helpers in `er1_profile.ps1` if needed
4. Deploy Pi runtime if systemd scripts or config change
5. Perform OTA with:

```
er1-ota <Dev>
```

---

# 9. Troubleshooting

### Profile not loading
- VS Code using PowerShell 5.1
- Wrong repo path
- Wrong profile path

### `er1 push` not found
`er1_profile.ps1` didn't load - fix VS Code profile.

### `er1 deploy` fails
- Pi unreachable
- Wrong SSH key or password
- Use scp fallback on laptop (rsync missing)

### OTA fails
- Wrong `Dev` name
- Missing `<Dev>.bin`
- Firmware path not copied to Pi
- ESP offline

### Logs missing
- MQTT broker not running
- er1-mqtt-log.service not running
- Wrong date format in log script

---

# 10. Best Practices

- Commit before deploying.
- Increase FW_VERSION on each firmware change.
- Use `er1 log -live` right after OTA.
- Avoid manual edits on the Pi runtime folder — always deploy.
- Keep repo paths consistent on PC and laptop.

---

_End of document._
