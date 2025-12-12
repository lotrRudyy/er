# ER1 Protocol (Canonical)

If this document conflicts with anything else, **this wins**.

---

# 0. Repo & Pi layout

## 0.1 Git repo structure

Repo root (PC / GitHub):

```
er/
  shared/
    pc-scripts/
      er1_profile.ps1
      open_er1_pi_terminals.ps1
      push.ps1
      reset_repo.ps1
      push.sh
      reset_repo.sh
      deploy_pi.ps1   # deploys er1/pi-runtime -> /home/rudyy/er1 and restarts the runtime
    libs/          # reserved for shared C++ libs
    docs/          # reserved for cross-ER docs

  er1/
    firmware/      # ER1 PlatformIO project
      platformio.ini
      src/
      include/
      lib/
      ota.ps1
      .vscode/
    pi-runtime/    # mirror of /home/rudyy/er1 on the Pi
      scripts/
        mqtt-logs.sh
        mqtt-locks.sh
        log_live.sh
        ota
        aliases
      systemd/
        er1-mqtt-log.service
        er1-runtime.service
      logs/
        .gitkeep
      config/
        local.env.example
      docs/
        README.md

    docs/
      ER1_PROTOCOL.md
      ER1_CODEX_REPORT.md
      mqtt_commands.md
      commands.md
      er1_logging_and_tools.md
      pwsh_profile.md
      pwsh_setup.md

  er2/
    firmware/
    pi-runtime/
      scripts/
      systemd/
      logs/
        .gitkeep
      config/
        local.env.example
      docs/
    docs/

  er3/
    firmware/
    pi-runtime/
      scripts/
      systemd/
      logs/
        .gitkeep
      config/
        local.env.example
      docs/
    docs/
```

---

## 0.2 Pi layout for ER1

ER1 Pi runtime root:

```
/home/rudyy/er1
  scripts/
  systemd/
  logs/
  config/
    local.env
  docs/
```

Mapping from repo → Pi:

- `er1/pi-runtime/scripts/  → /home/rudyy/er1/scripts/`
- `er1/pi-runtime/systemd/  → /home/rudyy/er1/systemd/`
- `er1/pi-runtime/docs/     → /home/rudyy/er1/docs/`
- `er1/pi-runtime/config/local.env.example → /home/rudyy/er1/config/local.env` (manual edit)
- `/home/rudyy/er1/logs/` exists only on Pi (repo only stores `.gitkeep`)

### Deploy behavior (canonical)
`deploy_pi.ps1`:

1. Syncs **pi-runtime/** into `/home/rudyy/er1/`
   (scripts, systemd, docs, config template)
2. **Preserves**:
   - `/home/rudyy/er1/logs/`
   - `/home/rudyy/er1/config/local.env`
3. Restarts runtime:

```
sudo systemctl restart er1-runtime.service
```

---

## 0.3 .gitignore rules (relevant)

Ignore:

- `erX/firmware/.pio/`
- `erX/pi-runtime/logs/` (except `.gitkeep`)
- `erX/pi-runtime/config/local.env`

Keep:

- `local.env.example`
- all scripts, docs, systemd units

---

# 1. Network & IPs

- Subnet: `192.168.0.0/24`
- Router: `192.168.0.1`
- ER1 Pi / MQTT broker: `192.168.0.10`
- NVR: `192.168.0.5`

### 1.1 Fixed IP map (ER1 devices)

- `192.168.0.11` → maglock controller
- `192.168.0.12` → images_piano
- `192.168.0.13` → chess
- `192.168.0.14` → knocking
- `192.168.0.15` → candles
- `192.168.0.16` → star_sky
- `192.168.0.17` → star_slider
- `192.168.0.18` → stop_timer

---

# 2. Global ESP32 rules

## 2.1 Hardware / transport

- ESP32 DevKit (38-pin)
- **Ethernet only** (W5500)
- No Wi-Fi libraries
- Static IPs
- Gateway & DNS: `0.0.0.0`

W5500 pins:

- MOSI = 23
- MISO = 19
- SCK  = 18
- ETH_CS  = 15
- ETH_RST = 27 (reserved)

---

## 2.2 MQTT topic pattern

For all ESP32 nodes:

```
esc/<room>/<dev>/hb
esc/<room>/<dev>/event
esc/<room>/<dev>/cmd
esc/<room>/<dev>/log
esc/<room>/<dev>/metric
```

Where:

- `<room>` ∈ {room0, room1, room2, room3}
- `<dev>` = canonical device name (section 3)

QoS:

- hb: QoS0 retained
- event: QoS0/1 not retained
- cmd: QoS1 not retained
- log: QoS0
- metric: QoS0

---

## 2.3 Heartbeats, metrics, logs

Heartbeat JSON:

```json
{"ver":"FW_X.Y","ip":"192.168.0.xx","up":123,"heap":12345,"rssi":-1,"reboot_count":0,"last_event_ts":0,"error_count":0,"diag_level":0}
```

Cadence:

- Heartbeat: **5–10 s**
- Metrics: **1–5 min**
- Logs: event-driven only

---

## 2.4 Command protocol

Commands arrive on:

```
esc/<room>/<dev>/cmd
```

Supported:

- ENABLE
- DISABLE
- REBOOT
- PING
- SET key=val
- DIAG level=<n> ttl_s=<s>
- UPDATE url=/firmware/<Dev>.bin

---

## 2.5 OTA

OTA served by ER1 Pi.

OTA path:

```
/firmware/<Dev>.bin
```

Correct invocation (inside `er1/firmware`):

```
pwsh ota.ps1 -Target <Dev>
```

Example:

```
pwsh ota.ps1 -Target images_piano
```

OTA lookup via deviceMap inside `ota.ps1`.

Always bump `FW_VERSION`.

---

# 3. Device map (Env / Dev / Room / IP / OTA)

| Role               | Env name             | Dev name      | Room   | IP            | OTA path                        |
|--------------------|-----------------------|----------------|--------|----------------|----------------------------------|
| Maglock controller | room0_maglock_ctrl   | maglock_ctrl  | room0  | 192.168.0.11  | /firmware/maglock_ctrl.bin      |
| Images + piano     | room1_images_piano   | images_piano  | room1  | 192.168.0.12  | /firmware/images_piano.bin      |
| Chess              | room2_chess          | chess         | room2  | 192.168.0.13  | /firmware/chess.bin             |
| Knocking           | room3_knocking       | knocking      | room3  | 192.168.0.14  | /firmware/knocking.bin          |
| Candles            | room3_candles        | candles       | room3  | 192.168.0.15  | /firmware/candles.bin           |
| Star sky           | room3_star_sky       | star_sky      | room3  | 192.168.0.16  | /firmware/star_sky.bin          |
| Star slider        | room3_star_slider    | star_slider   | room3  | 192.168.0.17  | /firmware/star_slider.bin       |
| Stop timer         | room3_stop_timer     | stop_timer    | room3  | 192.168.0.18  | /firmware/stop_timer.bin        |

Examples:

- `esc/room2/chess/hb`
- `esc/room3/star_sky/event`
- `esc/room3/stop_timer/hb`

Maglock controller node topics:

```
esc/room0/maglock_ctrl/hb
esc/room0/maglock_ctrl/cmd
esc/room0/maglock_ctrl/log
esc/room0/maglock_ctrl/metric
```

---

# 4. Maglocks (IDs, topics, behavior)

## 4.1 Lock IDs & topics

Canonical lock IDs:

- `images`
- `r2`
- `r3`
- `slider`
- `knocking`

**Deprecated**:
`door_to_r2`, `door_to_r3`

MQTT topics:

```
esc/ctrl/lock/<id>/cmd
esc/ctrl/lock/<id>/state
```

Allowed commands:

- `OPEN`
- `CLOSE`

State payload:

```json
{"state":"OPEN","reason":"cmd:OPEN","ts":123456789}
```

---

## 4.2 GPIO mapping & electrical semantics

GPIO → lock:

- images → 26 (fail-secure)
- r2 → 16 (fail-safe)
- r3 → 17 (fail-safe)
- slider → 33 (fail-secure)
- knocking → 25 (fail-secure)

### Fail-safe locks (r2, r3)

- **OPEN** = coil off (GPIO LOW), unlocked
- **CLOSE** = coil on (GPIO HIGH), locked
- No pulsing, no cooldown

### Fail-secure locks (images, slider, knocking)

Protocol sees only:

- `OPEN`
- `CLOSE`

Internal controller behavior:

#### On OPEN:
1. Output HIGH immediately
2. Hold for **1.0 second**
3. Output LOW
4. Start **10-second cooldown**
5. Ignore new OPEN until cooldown ends

#### On CLOSE:
- Force output LOW immediately
- Does **not** cancel cooldown

Fail-secure coils are never held longer than 1.0 s.

---

# 5. Firmware rules (all nodes)

- FSM-based
- Non-blocking (no long delay)
- Ethernet + MQTT + HTTP OTA only
- Task Watchdog: 2–5 s
- Auto-reboot on:
  - long MQTT disconnect
  - critically low heap
  - fatal errors

On boot:

- Load state from Preferences
- Publish boot event
- Re-scan sensors and publish state

---

# 6. Logging & tools (ER1)

## 6.1 MQTT → file logging (Pi)

Log directory:

```
/home/rudyy/er1/logs/
```

Filename:

```
er1-DD.MM.YYYY.log
```

Line format:

```
[DD.MM.YYYY HH:MM:SS.mmm] topic payload
```

Timestamp:

```
date +"[%d.%m.%Y %H:%M:%S.%3N]"
```

Main script:

```
/home/rudyy/er1/scripts/mqtt-logs.sh
```

Commands:

- `daemon`
- `live`
- `tail`
- `grep <pattern>`

Systemd:

```
er1-mqtt-log.service
```

---

## 6.2 PC tools (PowerShell)

All Windows-side interaction is now done through the single `er1` function
loaded from `shared/pc-scripts/er1_profile.ps1`.

### Core commands

- `er1 help`
  Show full help including log, OTA, deploy and lock commands.

- `er1 pi`
  SSH into the ER1 Pi (via Tailscale).

### Logging

- `er1 log`
  Tail today’s log (default: 200 lines).

- `er1 log <device>`
  Filter today’s log by device name.

- `er1 log -live`
  Live stream of all MQTT logs from the Pi.

- `er1 log <device> -live`
  Live filtered stream.

- `er1 log -errors`
  Only ERR-level lines.

- `er1 logs …`
  Raw passthrough to the Pi’s `er1 logs` CLI
  (for advanced/archived log access).

### OTA

- `er1 ota <device>`
  Upload and trigger OTA firmware update
  using `er1/firmware/ota.ps1`.

### Deploy Pi runtime

- `er1 deploy`
  Sync `er1/pi-runtime` → `/home/rudyy/er1` and restart systemd units.

### Maglocks

- `er1 lock <id>`
  Send an OPEN command to a lock ID.

- `er1 lock-all`
  Opens all locks via MQTT.

### Git helpers

- `er1 commit "<msg>"`
  Add, commit, and push from repo root.

---

## 6.3 ER1 runtime systemd unit

Runtime managed by:

```
er1-runtime.service
```

After deployment:

```
ssh rudyy@192.168.0.10 "sudo systemctl restart er1-runtime.service"
```

---

_End of canonical protocol._
