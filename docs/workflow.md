# ER System – Full Developer Workflow

This document describes the complete development, deployment, and maintenance workflow for the Escape Room control system.
It matches the canonical ER1 Protocol and defines the workflow used on both the PC and laptop development environments.

---

# 1. Repository Structure

```
er/
  docs/
    er/

  scripts/             # PowerShell and deployment tooling (PC + laptop)
    er1_profile.ps1
    codex-commit.ps1

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

scripts/ is the single source of truth for all developer-side tools.

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
- Leaves `/home/rudyy/er1/data/logs/` untouched.
- Re-applies execute bits on the remote runtime tree.
- Prints each rsync/scp command before running it so you know what will happen.
- Follow deployment with `er1 push "<msg>"` so Pi + Git stay aligned.

---

## 3.3 `er1 ota`
Triggers OTA update for a deployment target using the canonical OTA map inside `er1/firmware/ota.ps1`.

```
er1 ota <target>
```

Examples:

- `er1 ota images_piano` (publishes UPDATE to `images/cmd`, verifies `images` + `piano`)
- `er1 ota maglock` (canonical file `/firmware/maglock.bin`, also copies `maglock_ctrl.bin` on the Pi for migrations)

The map aligns PlatformIO env, firmware filename, MQTT command topic, and verifier nodes. OTA artifact lives on the Pi at `/home/rudyy/firmware/<FirmwareName>`.

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
pwsh ota.ps1 -Target <target>
```

Example:

```
pwsh ota.ps1 -Target images_piano
```

OTA steps:

1. Resolve target via the canonical map (Env, Dev, CmdNode, FirmwareName, optional LegacyFirmwareNames, VerifyNodes). Build the PlatformIO env unless `-NoBuild` is set.
2. Upload `.pio/build/<Env>/firmware.bin` to `/home/rudyy/firmware/<FirmwareName>` on the Pi and create any `LegacyFirmwareNames` copies (e.g., `maglock_ctrl.bin`).
3. From the Pi, verify `http://192.168.0.10/firmware/<FirmwareName>` responds with HTTP 200 + Content-Length.
4. Run `~/er1/scripts/ota_publish.py --dev <Dev> --cmd-node <CmdNode> --version <FW_VERSION> --target <NodeId> --url http://192.168.0.10/firmware/<FirmwareName> --file /home/rudyy/firmware/<FirmwareName>` so `UPDATE {json}` is published to `<CmdNode>/cmd` with sha256 + size computed on the Pi (no PSK/HMAC).
5. ESP32 downloads OTA over HTTP, reboots, and resumes heartbeats. `ota_verify.py` watches `VerifyNodes` (images_piano verifies both `images` and `piano` even though `CmdNode=images`).

---

# 6. Logging System (MQTT → File)

Logging is centralized on ER1 Pi.

Logs stored at:

```
/home/rudyy/er1/data/logs/er1-DD.MM.YYYY.log
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
2. Add the deployment to the OTA map in `er1/firmware/ota.ps1` (Env, Dev, CmdNode, FirmwareName, optional LegacyFirmwareNames + VerifyNodes)
3. Add lock or diagnostic helpers in `er1_profile.ps1` if needed
4. Deploy Pi runtime if systemd scripts or config change
5. Perform OTA with:

```
er1 ota <Dev>
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
- Wrong target name (must match OTA map keys)
- Missing `/home/rudyy/firmware/<FirmwareName>` on the Pi
- Firmware path not copied or legacy alias missing
- ESP offline

### Logs missing
- MQTT broker not running
- er1-mqtt-log.service not running
- Wrong date format in log script

---

## Manual update on Pi: MQTT logging runtime files

Use these steps after the log path move to `/home/rudyy/er1/data/logs/` when you need to refresh only the logging runtime files (no deploy helpers used).

1) Copy updated files from this repo to the Pi (from Windows/dev box, repo root):

```bash
scp er1/pi-runtime/scripts/mqtt_logs.sh rudyy@100.108.1.80:/home/rudyy/er1/scripts/mqtt_logs.sh
scp er1/pi-runtime/systemd/er1-mqtt-log.service rudyy@100.108.1.80:/home/rudyy/er1/systemd/er1-mqtt-log.service
ssh rudyy@100.108.1.80 "chmod +x /home/rudyy/er1/scripts/mqtt_logs.sh && sudo cp /home/rudyy/er1/systemd/er1-mqtt-log.service /etc/systemd/system/er1-mqtt-log.service"
```

Optional if rsync is available on both ends (behaves like deploy tooling):

```bash
rsync -avz er1/pi-runtime/scripts/mqtt_logs.sh rudyy@100.108.1.80:/home/rudyy/er1/scripts/
rsync -avz er1/pi-runtime/systemd/er1-mqtt-log.service rudyy@100.108.1.80:/home/rudyy/er1/systemd/
ssh rudyy@100.108.1.80 "chmod +x /home/rudyy/er1/scripts/mqtt_logs.sh && sudo cp /home/rudyy/er1/systemd/er1-mqtt-log.service /etc/systemd/system/er1-mqtt-log.service"
```

Notes:
- `er1-mqtt-log.service` must land in `/etc/systemd/system/er1-mqtt-log.service` (sudo required).
- Script path must stay `/home/rudyy/er1/scripts/mqtt_logs.sh` to match `ExecStart`.
- `chmod +x` keeps the service runnable.

2) Reload + restart systemd service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart er1-mqtt-log.service
sudo systemctl status er1-mqtt-log.service --no-pager
```

3) Validate log directory and output:

```bash
ls -lah /home/rudyy/er1/data/logs
journalctl -u er1-mqtt-log.service -n 200 --no-pager
```

Expected:
- service is active/running
- /home/rudyy/er1/data/logs exists
- log files appear/rotate

If broken:
- permission errors → check ownership and mkdir pre-start
- wrong paths → check ExecStart and script location
- missing directory → ensure mkdir -p step exists / ExecStartPre used

# 10. Best Practices
# 10. Best Practices

- Commit before deploying.
- Increase FW_VERSION on each firmware change.
- Use `er1 log -live` right after OTA.
- Avoid manual edits on the Pi runtime folder — always deploy.
- Keep repo paths consistent on PC and laptop.

---

_End of document._
