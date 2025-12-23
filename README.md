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

- `docs/workflow.md` - Developer workflow
- `docs/er_protocol.md` - Canonical ER1 Protocol
- `docs/` - ER-wide docs (future cross-room content)

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
    . "$ROOT\scripts\er1_profile.ps1"
} else {
    Write-Warning "ER repo not found."
}
```

This loads all development commands for both PC and laptop.

---

# 🧰 Key Commands

## Commit + push

```
er1 push "message"
```

The OTA workflow is now the canonical path for Pi runtime updates; use `er1 ota <Dev>`.

---

## OTA updates (canonical path)

```
er1 ota <Dev>
```

Examples:

```
er1 ota images_piano
er1 ota chess
```

Firmware must exist at:

```
firmware/.pio/build/<env>/<Dev>.bin
```

---

## Logging

Tail today's Pi log:

```
er1 log
```

Filter by device:

```
er1 log star_sky
```

Live stream:

```
er1 log -live
er1 logs pretty   # dashboard + OTA merge
```

Save a copy while streaming:

```
er1 log star_sky --save
er1 log -live --save
```

`--save` writes the live stream (with timestamps) under `<repo>\logs\yyyy-MM-dd_HH-mm-ss__pi_*.log` while still echoing to the console. Pi logfile source remains `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`.

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
maglock/lock/<id>/cmd
maglock/lock/<id>/state
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

### Canonical ER1 protocol (ultimate truth):
```
docs/er_protocol.md
```

Both files should always reflect the canonical commands defined in `scripts/er1_profile.ps1`.

---

# 🧱 Repo Structure

```
er/
  docs/
    er/
  er1/
    firmware/
    pi-runtime/
      scripts/
      systemd/
      logs/
      config/
  er2/
  er3/
  scripts/
    er1_profile.ps1
    codex-commit.ps1
  web/
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
