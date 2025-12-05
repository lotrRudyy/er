# Windows PowerShell Setup for ER1 Development

This file stores the required PowerShell 7 functions that must be added to
`$PROFILE` on every Windows machine (PC + laptop).

Canonical profile script lives at `shared/pc-scripts/er1_profile.ps1`.
Example import in `$PROFILE`:

```powershell
. "$PSScriptRoot/../shared/pc-scripts/er1_profile.ps1"
```

## PowerShell 7 Required

Check version:

```powershell
$PSVersionTable.PSVersion
