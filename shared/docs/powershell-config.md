# ER1 PowerShell Configuration (powershell-config.md)

Authoritative place for all Windows / VS Code PowerShell profiles, aliases, and helper functions used for ER1.

If this document conflicts with anything else about PowerShell setup, **this wins**.

---

## 1. Profile files (canonical locations)

Windows user profile:

- Global VS Code PowerShell profile
  `C:\Users\<USER>\Documents\PowerShell\Microsoft.VSCode_profile.ps1`

- Optional general PowerShell profile (Windows Terminal / plain PS)
  `C:\Users\<USER>\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`

For ER1 we primarily care about **Microsoft.VSCode_profile.ps1**. The repo-side logic lives in `er/shared/pc-scripts/er1_profile.ps1`.

---

## 2. Canonical VS Code profile

This is the recommended content of:

`C:\Users\<USER>\Documents\PowerShell\Microsoft.VSCode_profile.ps1`

It auto-detects the ER repo location (PC vs laptop) and dot-sources the repo-side profile.

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

$erProfile = Join-Path $erRepoRoot "shared\pc-scripts\er1_profile.ps1"

if (Test-Path $erProfile) {
    . $erProfile
    Write-Host "[er1] Loaded repo-side profile: $erProfile" -ForegroundColor Cyan
}
else {
    Write-Warning "[er1] er1_profile.ps1 not found at $erProfile"
}

# Better history navigation
Import-Module PSReadLine -ErrorAction SilentlyContinue
Set-PSReadLineOption -HistorySearchCursorMovesToEnd

# Optional: tweak TAB behavior if you want
# Set-PSReadLineKeyHandler -Chord Tab -Function ForwardWord

# Short prompt path
function prompt {
    "PS " + (Get-Location).Path.Replace($HOME, '~') + '> '
}
