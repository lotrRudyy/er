# Escape Room Control System (ER1 / ER2 / ER3)

This repository contains the complete control system for the escape room project:

- ESP32 firmware (PlatformIO)
- Raspberry Pi runtime + systemd services
- Deployment tools (PC + laptop compatible)
- OTA update tools
- Logging infrastructure
- Canonical protocol and documentation

This README provides a quick overview.
**Deep technical documentation lives in:**

- `docs/workflow.md` — Developer workflow
- `er1/docs/ER1_PROTOCOL.md` — Canonical ER1 Protocol
- `shared/docs/` — cross-room documentation (future)

---

# 🚀 Quickstart

## Requirements

- **PowerShell 7** (pwsh)
- **VS Code**
- **Git**
- Laptop or PC with repo cloned to one of:
  - `C:\Users\rudol\Documents\Escape Room\er`
  - `C:\Users\Rudy\Documents\er`
- SSH access to ER1 Pi: `rudyy@192.168.0.10` or via Tailscale

---

# 🧩 VS Code Profile

Create:

```
$HOME\Documents\PowerShell\Microsoft.VSCode_profile.ps1
```

Content:

```powershell
Write-Host ">>> VS CODE PROFILE LOADED <<<"

$pc = "$HOME\Documents\Escape Room\er"
$laptop = "$HOME\Documents\er"

$ROOT = if (Test-Path $pc) { $pc } elseif (Test-Path $laptop) { $laptop } else { "" }

if ($ROOT -ne "") {
    . "$ROOT\shared\pc-scripts\er1_profile.ps1"
} else {
    Write-Warning "ER repo not found."
}
```

This loads all development commands for both PC and laptop.

---

# 🧰 Key Commands

## Commit + push

```
er-commit "message"
```

## Deploy Pi runtime

```
er1-deploy er1
```

Deploy script syncs:

- `er1/pi-runtime/scripts/`
- `er1/pi-runtime/systemd/`
- `er1/pi-runtime/docs/`
- `config/local.env.example`

And restarts:

```
er1-runtime.service
```

---

## OTA updates

```
er1-ota <Dev>
```

Examples:

```
er1-ota images_piano
er1-ota chess
```

Internally runs:

```
pwsh ota.ps1 -Target <Dev>
```

Firmware must exist at:

```
er1/firmware/.pio/build/<env>/<Dev>.bin
```

---

## Logging

Live:

```
er1-log-live
```

By device:

```
er1-log-node star_sky
```

MQTT → file logger runs on the Pi and outputs:

```
/home/rudyy/er1/logs/er1-DD.MM.YYYY.log
```

---

## Lock Commands

```
er1-lock-images
er1-lock-r2
er1-lock-r3
er1-lock-slider
er1-lock-knocking
```

Lock MQTT topics:

```
er1/ctrl/lock/<id>/cmd
er1/ctrl/lock/<id>/state
```

Lock IDs (canonical):

- `images`
- `r2` (Room 2 door — fail-safe)
- `r3` (Room 3 door — fail-safe)
- `slider`
- `knocking`

Legacy names `door_to_r2`, `door_to_r3` are deprecated.

---

# 📚 Documentation

### Complete developer workflow:
```
docs/workflow.md
```

### Canonical ER1 protocol (wins over everything else):
```
er1/docs/ER1_PROTOCOL.md
```

Both files should always be updated together when modifying firmware, runtime, or MQTT conventions.

---

# 🧱 Repo Structure

```
er/
  er1/
    firmware/
    pi-runtime/
      scripts/
      systemd/
      logs/
      config/
      docs/
  er2/
  er3/
  shared/
    pc-scripts/
    libs/
    docs/
```

---

# ✔ Status

This README + workflow + protocol now form the **complete documentation triad**.

- PC & laptop workflows unified
- OTA usage standardized (`pwsh ota.ps1 -Target <Dev>`)
- Deployment documented
- Logging documented
- Lock IDs + behavior standardized
- Protocol contradictions resolved

---

_End of README.md_
