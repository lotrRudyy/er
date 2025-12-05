# ER1 Protocol (Canonical)

If this document conflicts with anything else, **this wins**.

---

## 0. Repo & Pi layout

### 0.1 Git repo structure

Repo root (PC / GitHub):

er/
  shared/
    pc-scripts/
      er1_profile.ps1
      open_er1_pi_terminals.ps1
      push.ps1
      reset_repo.ps1
      push.sh
      reset_repo.sh
      # later: deploy_pi.ps1
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
        er1-tmux.sh
        ota
        aliases
      systemd/
        er1-mqtt-log.service
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

### 0.2 Pi layout for ER1

ER1 Pi runtime root:

/home/rudyy/er1
  scripts/
  systemd/
  logs/
  config/
    local.env
  docs/

Mapping from repo → Pi:

- er1/pi-runtime/scripts/  → /home/rudyy/er1/scripts/
- er1/pi-runtime/systemd/  → /home/rudyy/er1/systemd/
- er1/pi-runtime/docs/     → /home/rudyy/er1/docs/
- er1/pi-runtime/config/local.env.example → /home/rudyy/er1/config/local.env (edited manually)
- /home/rudyy/er1/logs/ exists only on Pi (no real logs in Git).

### 0.3 .gitignore rules (relevant)

- Ignore build dirs:
  - er1/firmware/.pio/
  - er2/firmware/.pio/
  - er3/firmware/.pio/
- Ignore runtime junk:
  - er1/pi-runtime/logs/ (except .gitkeep)
  - er1/pi-runtime/config/local.env
  - same pattern for er2 / er3
- Keep:
  - local.env.example
  - all scripts, docs, systemd units.

---

## 1. Network & IPs

- Subnet: 192.168.0.0/24
- Router: 192.168.0.1
- ER1 Pi / MQTT broker: 192.168.0.10
- NVR: 192.168.0.5

### 1.1 Fixed IP map (ER1 devices)

- 192.168.0.10 → ER1 Pi (MQTT, Node-RED, logs, OTA host)
- 192.168.0.11 → Maglock controller (ESP32 + W5500)
- 192.168.0.12 → Room1 images+piano node
- 192.168.0.13 → Room2 chess node
- 192.168.0.14 → Room3 knocking node
- 192.168.0.15 → Room3 candles node
- 192.168.0.16 → Room3 star_sky node
- 192.168.0.17 → Room3 star_slider node
- 192.168.0.18 → Room3 stop_timer node

---

## 2. Global ESP32 rules

### 2.1 Hardware / transport

- Board: 38-pin ESP32 DevKit (USB-C clone).
- Ethernet only (no Wi-Fi, no Wi-Fi libs).
- Ethernet: W5500 on shared SPI:
  - MOSI = GPIO23
  - MISO = GPIO19
  - SCK  = GPIO18
  - ETH_CS  = GPIO15
  - ETH_RST = GPIO27 (reserved; nothing else uses 27)
- Static IP from table above.
- Gateway & DNS: 0.0.0.0 (LAN-only).

### 2.2 MQTT topic pattern (nodes)

For all normal nodes (not per-lock topics):

- esc/<room>/<dev>/hb
- esc/<room>/<dev>/event
- esc/<room>/<dev>/cmd
- esc/<room>/<dev>/log
- esc/<room>/<dev>/metric

Where:

- <room> ∈ {room0, room1, room2, room3}
- <dev> is the device name from section 3 (note the underscores).

QoS / retain:

- hb: QoS 0, retained
- event: QoS 0 (or 1 if critical), not retained
- cmd: QoS 1, not retained
- log: QoS 0, not retained
- metric: QoS 0, not retained

### 2.3 Heartbeats, metrics, logs

Heartbeat JSON example:

```json
{"ver":"FW_X.Y","ip":"192.168.0.xx","up":123,"heap":12345,"rssi":-1,"reboot_count":0,"last_event_ts":0,"error_count":0,"diag_level":0}
Cadence:

Heartbeat: every 5–10 s.

Metrics: every 1–5 min (prod).

Logs: event-driven only, rate-limited.

2.4 Command protocol
Each node listens on esc/<room>/<dev>/cmd.

Supported commands (protocol level):

ENABLE

DISABLE

REBOOT

PING

SET key=val

DIAG level=<0..n> ttl_s=<seconds>

UPDATE url=/firmware/<Dev>.bin

2.5 OTA
HTTP OTA served by ER1 Pi.

OTA path per device: /firmware/<Dev>.bin.

PC side: er1/firmware/ota.ps1, used like:

. ota.ps1 -Env <Env> -Dev <Dev>

Always bump FW_VERSION on each change.

3. Device map (Env / Dev / Room / IP / OTA)
Dev names here are the MQTT <dev> and OTA basename.

Role	Env name	Dev name	Room	IP	OTA path
Maglock controller	room0_maglock_ctrl	maglock_ctrl	room0	192.168.0.11	/firmware/maglock_ctrl.bin
Images + piano	room1_images_piano	images_piano	room1	192.168.0.12	/firmware/images_piano.bin
Chess	room2_chess	chess	room2	192.168.0.13	/firmware/chess.bin
Knocking	room3_knocking	knocking	room3	192.168.0.14	/firmware/knocking.bin
Candles	room3_candles	candles	room3	192.168.0.15	/firmware/candles.bin
Star sky	room3_star_sky	star_sky	room3	192.168.0.16	/firmware/star_sky.bin
Star slider	room3_star_slider	star_slider	room3	192.168.0.17	/firmware/star_slider.bin
Stop timer	room3_stop_timer	stop_timer	room3	192.168.0.18	/firmware/stop_timer.bin

Examples:

Chess hb: esc/room2/chess/hb

Star sky event: esc/room3/star_sky/event

Stop timer hb: esc/room3/stop_timer/hb

Maglock controller node topics:

esc/room0/maglock_ctrl/hb

esc/room0/maglock_ctrl/cmd

esc/room0/maglock_ctrl/log

esc/room0/maglock_ctrl/metric

4. Maglocks (IDs, topics, behavior)
4.1 Lock IDs & MQTT topics
Lock IDs:

images

r2

r3

slider

knocking

Lock topics:

Command: esc/ctrl/lock/<id>/cmd

State: esc/ctrl/lock/<id>/state

Allowed lock commands:

OPEN

CLOSE

No PULSE. No STATUS.

Examples:

esc/ctrl/lock/r2/cmd = OPEN
esc/ctrl/lock/r3/cmd = CLOSE
esc/ctrl/lock/images/cmd = OPEN
esc/ctrl/lock/slider/cmd = OPEN
esc/ctrl/lock/knocking/cmd = CLOSE

State payload example:

json
Copy code
{"state":"OPEN","reason":"cmd:OPEN","ts":123456789}
4.2 GPIO mapping and electrical semantics
Maglock controller GPIO → lock:

images → GPIO26 (fail-secure)

r2 → GPIO16 (fail-safe, door to Room 2)

r3 → GPIO17 (fail-safe, door to Room 3)

slider → GPIO33 (fail-secure)

knocking → GPIO25 (fail-secure)

Behavior:

Fail-safe locks (r2, r3):

No power = unlocked.

CLOSE → set GPIO HIGH (power on, locked) and keep it on until OPEN.

OPEN → set GPIO LOW (power off, unlocked).

No pulsing / cooldown needed; they can stay energized.

Fail-secure locks (images, slider, knocking):

No power = locked.

External protocol: only sees OPEN / CLOSE.

Internal behavior for OPEN:

If the lock is not currently in a pulse or cooldown:

Turn output ON immediately.

Keep it ON for exactly 1000 ms.

After 1000 ms, turn output OFF again.

Then start a cooldown period of 10 seconds for that lock.

While either:

the 1 s pulse is running, or

the 10 s cooldown is active,

ignore any new OPEN commands for that lock.

Internal behavior for CLOSE:

Immediately force the output OFF (coil off).

CLOSE does not cancel or shorten the 10 s cooldown; it just ensures no power.

So, from outside:

You only send OPEN and CLOSE.

Fail-secure coils:

get a 1.0 s power pulse on OPEN,

then are in a 10 s “heat protection” window where further OPEN is ignored,

and are safe from being held on.

5. Firmware rules (all nodes)
FSM-based, non-blocking (no long delay()).

Ethernet + MQTT + HTTP OTA only; no Wi-Fi.

ESP32 Task Watchdog (2–5 s) enabled.

Auto-reboot on:

Long MQTT disconnect,

Critically low heap,

Fatal FSM error (with backoff + reboot counter).

On boot:

Restore last FSM state from Preferences.

Publish a boot event on esc/<room>/<dev>/event.

Re-scan sensors and publish updated state if needed.

6. Logging & tools (ER1)
6.1 MQTT → file logging (Pi)
Central MQTT logging on ER1 Pi must use a date-based timestamp prefix with millisecond precision.

Log dir: /home/rudyy/er1/logs/

Log filename pattern (one per day):
er1-DD.MM.YYYY.log

Line format (mandatory):
[DD.MM.YYYY HH:MM:SS.mmm] topic payload

Implementation requirement:

The prefix must be generated with date using %3N for milliseconds, e.g.:

date +"[%d.%m.%Y %H:%M:%S.%3N]"

Do not use ts or any other timestamp tool anymore.

Replace any older timestamp logic so that all MQTT logging flows through this date-based format.

Main script on Pi:

/home/rudyy/er1/scripts/mqtt-logs.sh with subcommands:

daemon → systemd mode, no stdout

live → print to stdout

tail → tail today’s log

grep <pattern> → grep today’s log

Systemd reference unit (on Pi):

/home/rudyy/er1/systemd/er1-mqtt-log.service

WorkingDirectory=/home/rudyy/er1

ExecStart=/home/rudyy/er1/scripts/mqtt-logs.sh daemon

Environment=LOCAL_BROKER=127.0.0.1

6.2 PC helpers (PowerShell, from shared/pc-scripts)
er1_profile.ps1 is loaded via $PROFILE and provides:

pi (SSH into Pi)

er1-log-live, er1-log-node <dev>, er1-log-all

er1-lock <id> (esc/ctrl/lock/<id>/cmd wrapper)

er1-lock-images, er1-lock-all

er1-ota <Dev> (calls er1/firmware/ota.ps1)

Future: deploy_pi.ps1 to sync er1/pi-runtime → /home/rudyy/er1.
