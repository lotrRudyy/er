# ER1 PowerShell Configuration (powershell_config.md)

Authoritative place for all Windows / VS Code PowerShell profiles, aliases, and helper functions used for ER1.

If this document conflicts with anything else about PowerShell setup, **this wins**.

---

## 1. Profile files (canonical locations)

Windows user profile files:

- VS Code PowerShell profile (primary for ER work):
  `C:\Users\<USER>\Documents\PowerShell\Microsoft.VSCode_profile.ps1`

- Optional general PowerShell profile (Windows Terminal / plain PS):
  `C:\Users\<USER>\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`

Repo-side ER1 logic lives in a file named `er1_profile.ps1` inside the ER repo (under `scripts/`).
This repo-side profile is the single source of truth for ER commands (`er1`, deploy, logs, status, doctor, etc).

---

## 2. Canonical VS Code profile

Recommended content for:

`C:\Users\<USER>\Documents\PowerShell\Microsoft.VSCode_profile.ps1`

Responsibilities:
- Auto-detect ER repo location (PC vs laptop).
- Dot-source the repo-side ER profile if found.
- Set PSReadLine options.
- Provide a short prompt.

```powershell
# === ER1 VS Code Profile (Canonical) ===

Write-Host ">>> VS CODE PROFILE LOADED <<<" -ForegroundColor Green

# Try both known repo locations (PC and laptop)
$pcPath     = "$HOME\Documents\Escape Room\er"
$laptopPath = "$HOME\Documents\er"

if (Test-Path $pcPath) {
    $erRepoRoot = $pcPath
}
elseif (Test-Path $laptopPath) {
    $erRepoRoot = $laptopPath
}
else {
    Write-Warning "[er1] ER repo not found. Checked: $pcPath and $laptopPath"
    return
}

# We allow a few repo-side layouts so the profile keeps working if folders move.
$candidates = @(
    (Join-Path $erRepoRoot "scripts\er1_profile.ps1")
)

$erProfile = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($erProfile) {
    . $erProfile
    Write-Host "[er1] Loaded repo-side profile: $erProfile" -ForegroundColor Cyan
}
else {
    Write-Warning "[er1] er1_profile.ps1 not found. Checked:`n  - $($candidates -join "`n  - ")"
}

# Better history navigation
Import-Module PSReadLine -ErrorAction SilentlyContinue
Set-PSReadLineOption -HistorySearchCursorMovesToEnd

# Short prompt path
function prompt {
    "PS " + (Get-Location).Path.Replace($HOME, '~') + '> '
}
