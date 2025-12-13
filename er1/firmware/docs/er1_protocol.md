# ER1 Protocol (Canonical)

If this document conflicts with anything else, **this wins**.

---

# Codex Execution Rules

## No-Permission Execution Rule

- Codex never asks for permission, confirmation, or approval before making changes; every request is executed end-to-end.
- Tasks are completed fully with no partial hand-offs or pauses awaiting chat confirmation.
- Git history is the rollback mechanism; safety reviews happen via diffs, not conversational checkpoints.
- If you ask for permission/confirmation, that is a protocol violation. Continue immediately and execute the task without further       questions.

- Only ask a question if execution is impossible due to missing concrete input (e.g., file not present, ambiguous target file name). Otherwise assume defaults and proceed.

## Mandatory Change Summary

- Every Codex response ends with a summary block in this exact format:

```
CHANGES MADE:
- <file>: <precise description of change>
- <file>: <precise description of change>
```

- When no files change, respond with:

```
CHANGES MADE:
- none (analysis / design only)
```

## Explicitly Forbidden

- Asking “should I proceed”
- Asking “do you want me to”
- Waiting for confirmation
- Partial execution pending approval

The user reviews diffs and reverts via Git when needed.

### Runtime Enforcement

Every Codex task prompt MUST end with:
--ask-for-approval never

This flag disables Codex’s internal approval system.
Prompt text alone is insufficient.

---

# 0. Repo & Pi layout

## 0.1 Git repo structure

Repo root (PC / GitHub):

```
er/
  docs/
    er/
      er1_protocol.md
      er1_codex_report.md
      mqtt_commands.md
      commands.md
      er1_logging_and_tools.md

  pc-scripts/
    er1_profile.ps1               # single source of truth for 'er1 <cmd>'
    codex_commit.ps1

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
        mqtt_logs.sh
        mqtt_locks.sh
        log_live.sh
        ota_verify.py
        aliases_er1.sh
      systemd/
        er1-mqtt-log.service
        er1-runtime.service
      logs/
        .gitkeep
      config/
        local.env.example
      docs/
        README.md

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
`er1 deploy` (defined in `er1_profile.ps1`):

1. Detects repo root (PC vs laptop) and the Pi target via `$er1Pi`.
2. Syncs **er1/pi-runtime/** into `/home/rudyy/er1/`
   (scripts, systemd, docs, config template) using `rsync -avz --delete`
   when available, else `scp -r` fallback.
3. Excludes `/home/rudyy/er1/logs/` and `config/local.env` so Pi-specific
   state survives each deploy.
4. Re-applies execute bits on the remote runtime tree.

After deploying, immediately commit + push your changes with `er1 push`
to keep Pi + Git history aligned.

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
- `192.168.0.12` → images_piano (logical nodes: images + piano on the same ESP32)
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

For all ESP32 nodes the namespace is roomless and uses:

```
<node>/<channel>
```

Channels in use: `cmd`, `hb`, `evt`, `state`, `dbg`, `log`, `cfg`.
See docs/mqtt-topics.md for details.

---

## 2.4 Heartbeats, metrics, logs

Heartbeat JSON:

```json
{"ver":"FW_X.Y","ip":"192.168.0.xx","up":123,"heap":12345,"rssi":-1,"reboot_count":0,"last_event_ts":0,"error_count":0,"diag_level":0}
```

Cadence:

- Heartbeat: **5–10 s**
- Metrics: **1–5 min**
- Logs: event-driven only

---

## 2.5 Command protocol

Commands arrive on:

```
<node>/cmd
```

Supported:

- ENABLE
- DISABLE
- REBOOT
- PING
- SET key=val
- DIAG level=<n> ttl_s=<s>
- UPDATE sha256=<64-hex> hmac=<64-hex> url=/firmware/<Dev>.bin

---

## 2.6 OTA

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

Validation (fails closed):

- Command payload must include `sha256` (lowercase hex) and `hmac` (HMAC-SHA256 over the `sha256` string, keyed by `OTA_PSK`).
- OTA downloads are allowlisted to `http://192.168.0.10/firmware/<Dev>.bin`. Host/IP overrides are rejected; paths must stay under `/firmware/`.
- Firmware bytes are streamed through SHA-256; mismatches abort the OTA and skip reboot.
- HMAC is verified before any download; missing/invalid fields or PSK abort with a `fail` status.

Build + tooling requirements:

- `OTA_PSK` must be available at build time (`platformio.ini` pulls `${sysenv.OTA_PSK}`); export it temporarily from the Pi and avoid saving it on PCs.
- `ota.ps1` now uploads the binary and SSHes into `~/er1/scripts/ota_publish.py`, which reads `/etc/er1/ota_psk` on the Pi to compute sha256 + HMAC and publish `UPDATE` to `<Dev>/cmd` (no PSK on the PC side).
- Optional: define `OTA_VALIDATION_SELF_TEST` at build time to run a small self-test of the hash/HMAC helpers at startup.

### OTA status topic (per node)

Each ESP32 node publishes OTA state to:

```
<node>/ota
```

Payload format (compact JSON, same logging schema as hb/log):

```
{"fw":"<current_fw>","up":<uptime_s>,"st":"<state>","d":{...}}
```

- `st="start"` (retained) right before HTTP GET, `d={"to":"<target_fw_or_?>"}`
- `st="prog"` (not retained, max 1 msg/s) while pulling image, `d={"pct":42}`
- `st="ok"` (retained) after `Update.end(true)` succeeds, `d={"bytes":123456}`
- `st="fail"` (retained) on any OTA failure, `d={"at":"dns|conn|http|hdr|write|end|md5","code":<num>,"msg":"<short>","bytes":<optional_bytes>}`

Retained semantics: `start` stays until `ok`/`fail` overwrites it so operators can see stuck OTAs.

New failure reasons are reported via `msg` + extra fields in `d`:

- `missing_sha256`, `missing_hmac`, `invalid_sha256`, `invalid_hmac`
- `hmac_mismatch`, `sha256_mismatch` (includes expected + actual hashes)
- `host_not_allowed`, `path_not_allowed`
- Legacy failure points (http/connect/write/etc.) remain unchanged.

### Pi OTA verification

`pi-runtime/scripts/ota_verify.py` subscribes to `+/cmd` + `+/hb`. When an `UPDATE` command is seen it opens a 90 s window and waits for:

1. Device to go offline at least once (LWT `offline`) **or** its heartbeat uptime to reset.
2. Device to publish a new heartbeat after reconnecting.
3. Heartbeat `fw` to differ from the version recorded before the update.

If those checks pass it logs:

```
OTA_RESULT dev=<dev> room=<room> result=OK old_fw=<old> new_fw=<new>
```

Failures log a single line with one of: `no_offline`, `no_return`, `no_fw_change`, `timeout` plus the last firmware seen:

```
OTA_RESULT dev=<dev> room=<room> result=FAIL reason=<reason> old_fw=<old> last_fw=<last>
```

Output is appended to `/home/rudyy/er1/logs/ota-verify.log` (and systemd journal via `er1-ota-verify.service`).

---

# 3. Device map (Env / Dev / IP / OTA)

images and piano are separate logical MQTT nodes running on the same ESP32 and deployed together as the `images_piano` env/OTA.

| Role               | Env name   | Dev name  | IP            | OTA path                    |
|--------------------|------------|-----------|----------------|-----------------------------|
| Maglock controller | maglock    | maglock   | 192.168.0.11  | /firmware/maglock_ctrl.bin  |
| Images + piano     | images_piano | images_piano | 192.168.0.12  | /firmware/images_piano.bin  |
| Chess              | chess      | chess     | 192.168.0.13  | /firmware/chess.bin         |
| Knocking           | knocking   | knocking  | 192.168.0.14  | /firmware/knocking.bin      |
| Candles            | candles    | candles   | 192.168.0.15  | /firmware/candles.bin       |
| Star sky           | star_sky   | star_sky  | 192.168.0.16  | /firmware/star_sky.bin      |
| Star slider        | star_slider| star_slider| 192.168.0.17 | /firmware/star_slider.bin   |
| Stop timer         | stop_timer | stop_timer| 192.168.0.18  | /firmware/stop_timer.bin    |

Examples:

- `chess/hb`
- `star_sky/evt`
- `stop_timer/hb`

Maglock controller node topics:

```
maglock/hb
maglock/cmd
maglock/log
maglock/dbg
maglock/lock/<id>/cmd
maglock/lock/<id>/state
```

## 3.1 Firmware module layout

| Env name   | Core shell file         | Module(s)                                  |
|------------|-------------------------|--------------------------------------------|
| images_piano | `images_piano_main.cpp` | `images_riddle.cpp`, `piano_riddle.cpp`    |
| maglock    | `maglock_main.cpp`      | `maglock_controller.cpp`                   |
| knocking   | `knocking_main.cpp`     | `knocking_riddle.cpp`                      |
| candles    | `candles_main.cpp`      | `candles_riddle.cpp`                       |
| star_sky   | `star_sky_main.cpp`     | `star_sky_riddle.cpp`                      |
| chess      | `chess_main.cpp`        | `chess_riddle.cpp`                         |
| star_slider| `star_slider_main.cpp`  | `star_slider_riddle.cpp`                   |

Each listed env uses the reusable core node scaffolding (Ethernet/MQTT/OTA/log/heartbeat) plus the modules shown above for puzzle logic. Update this table as additional nodes adopt the pattern.

## 3.2 Images riddle rule

- Images_piano + piano node: the images riddle now solves only when all four image buttons are pressed simultaneously and held stable for ALL_DOWN_HOLD_MS (200 ms). No sequence/buffer logic is considered; releasing any button before the hold completes cancels the attempt and requires a fresh all-down hold.

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
maglock/lock/<id>/cmd
maglock/lock/<id>/state
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
/home/rudyy/er1/scripts/mqtt_logs.sh
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
loaded from `pc-scripts/er1_profile.ps1`.

### Core commands

- `er1 help`
  Show full help including log, OTA, deploy and lock commands.

- `er1 pi`
  SSH into the ER1 Pi (via Tailscale).

### Logging

- `er1 log`
  Tail today's log (default: 200 lines).

- `er1 log <device>`
  Filter today's log by device name.

- `er1 log -live`
  Live stream of all logs from the Pi.

- `er1 log <device> -live`
  Live filtered stream.

- `er1 log -errors`
  Only ERR-level lines.

- `er1 log <args> --save`
  Duplicates the console output to `<repo>\logs\yyyy-MM-dd_HH-mm-ss__pi_*.log`
  with timestamps (directory is auto-created). Use with live, filter, or errors.

`er1 logs` passthrough was removed; `er1 log` now covers tail, filters, live stream,
error-only view, and optional saving in a single command.

### OTA

- `er1 ota <device>`
  Upload and trigger OTA firmware update
  using `er1/firmware/ota.ps1`.

### Deploy Pi runtime

- `er1 deploy [runtime|full]`
  Mirrors `er1/pi-runtime` into `/home/rudyy/er1` using `rsync -avz --delete`
  (or `scp -r` fallback). Default `runtime` mode copies scripts/, systemd/,
  docs/ and config/ (excluding `config/local.env`) and re-applies execute bits.
  `full` mirrors the entire `pi-runtime/` tree. Logs stay untouched; restart
  services via SSH if required.

  **After every deploy**: immediately run `er1 push "<msg>"` so Pi runtime and
  git history never drift.

### Maglocks

- `er1 lock <id>`
  Send an OPEN command to a lock ID.

- `er1 lock-all`
  Opens all locks via MQTT.

### Git helpers

- `er1 push "<msg>"`
  Add, commit, and push from repo root. Prints branch/upstream info, auto-sets
  upstream to `origin/<branch>` when available, and fails loudly outside git.

- `er1 commit "<msg>"`
  Alias for `er1 push` kept for muscle memory.

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
