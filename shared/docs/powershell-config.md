# ER1 PowerShell Configuration (powershell-config.md)

Authoritative place for all Windows/VS Code PowerShell profile, aliases, and helper functions used for ER1.

---

## 1. Profile Files (Canonical Locations)

* Global VS Code PowerShell profile:

  * `C:\Users\<USER>\Documents\PowerShell\Microsoft.VSCode_profile.ps1`
* Optional general PowerShell profile (Windows Terminal, plain PS):

  * `C:\Users\<USER>\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`

For ER1 we mainly care about **Microsoft.VSCode_profile.ps1**.

---

## 2. Canonical VS Code Profile Template

Put this in `Microsoft.VSCode_profile.ps1` (adjust `<USER>` if needed):

```powershell
# === ER1 VS Code Profile (Canonical) ===

$erRoot = "$HOME\Documents\Escape Room\er"
$pcScripts = Join-Path $erRoot "shared\pc-scripts"

# Ensure scripts folder exists
if (-not (Test-Path $pcScripts)) {
    Write-Warning "[er1] pc-scripts folder not found: $pcScripts"
}

# Import ER1 helper functions if present
$erProfile = Join-Path $pcScripts "er1_profile.ps1"
if (Test-Path $erProfile) {
    . $erProfile
    Write-Host ">>> VS CODE PROFILE LOADED <<<" -ForegroundColor Green
} else {
    Write-Warning "[er1] er1_profile.ps1 not found at $erProfile"
}

# Better history navigation
Import-Module PSReadLine -ErrorAction SilentlyContinue
Set-PSReadLineOption -HistorySearchCursorMovesToEnd

# Make TAB jump word-wise (ForwardWord)
Set-PSReadLineKeyHandler -Chord Tab -Function ForwardWord

# Short prompt path
function prompt {
    "PS " + (Get-Location).Path.Replace($HOME, '~') + '> '
}
```

This keeps the VS Code profile small and delegates most logic to `er1_profile.ps1` inside the repo.

---

## 3. er1_profile.ps1 (Repo-side Helpers)

File: `er/shared/pc-scripts/er1_profile.ps1`

```powershell
# === ER1 Helper Functions (er1_profile.ps1) ===

param()

$erRoot = "$HOME\Documents\Escape Room\er"
$er1Root = Join-Path $erRoot "er1"
$pcScripts = Join-Path $erRoot "shared\pc-scripts"

# --- Basic navigation ---
function er-root {
    Set-Location $erRoot
}

function er1 {
    Set-Location $er1Root
}

# --- Git helpers ---
function er-commit {
    param([string]$Message = "update")
    Set-Location $erRoot
    git add .
    git commit -m $Message
}

function er-push {
    Set-Location $erRoot
    git push
}

# One-shot helperunction er-save {
    param([string]$Message = "update")
    er-commit -Message $Message
    er-push
}

# --- Deploy to Pi ---
function er1-deploy {
    param([string]$Project = "er1")

    $deployScript = Join-Path $pcScripts "deploy_pi.ps1"
    if (-not (Test-Path $deployScript)) {
        Write-Error "deploy_pi.ps1 not found at $deployScript"
        return
    }

    & $deployScript -Project $Project
}

# --- OTA Firmware ---
function er1-ota {
    param(
        [ValidateSet("maglock_ctrl","images_piano","chess","knocking","candles","star_sky","star_slider","stop_timer")]
        [string]$Dev,
        [switch]$NoBuild
    )

    $otaScript = Join-Path $er1Root "firmware\ota.ps1"
    if (-not (Test-Path $otaScript)) {
        Write-Error "ota.ps1 not found at $otaScript"
        return
    }

    Push-Location $er1Root
    try {
        if ($NoBuild) {
            . $otaScript -Target $Dev -NoBuild
        } else {
            . $otaScript -Target $Dev
        }
    }
    finally {
        Pop-Location
    }
}

# --- MQTT log helpers (via Pi runtime scripts) ---
function er1-log {
    param([string]$Filter = "")
    $ssh = "rudyy@er1-pi"
    $cmd = "cd ~/er1 && ./scripts/mqtt-logs.sh live"
    if ($Filter -ne "") { $cmd += " $Filter" }
    ssh $ssh $cmd
}

function er1-locks {
    $ssh = "rudyy@er1-pi"
    $cmd = "cd ~/er1 && ./scripts/mqtt-locks.sh"
    ssh $ssh $cmd
}

Write-Host "[er1_profile] Loaded ER1 helper functions" -ForegroundColor Cyan
```

This keeps **all ER-specific logic in the repo**, and the VS Code profile just points to it.

---

## 4. Usage Cheatsheet

In a VS Code PowerShell terminal:

```powershell
# Go to repo
er-root

# Go to er1 subfolder
er1

# Commit + push
er-save "message here"

# Deploy Pi runtime
er1-deploy

# Trigger OTA for chess
er1-ota -Dev chess

# Live MQTT logs (from Pi)
er1-log

# Lock-specific logs
er1-locks
```

---

## 5. TODO / Future Improvements

* Add interactive target selection for `er1-ota` (e.g., fuzzy picker)
* Add automatic `git pull` + `git status` summary in `er-root`
* Add wrapper to open common log files directly from Windows (e.g., `er1-open-log chess`)
