param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("er1")]
    [string]$Env,

    [Parameter(Mandatory = $false)]
    [ValidateSet("runtime", "full")]
    [string]$Mode = "runtime"
)

$ErrorActionPreference = "Stop"

# ----- Resolve paths -----

# repo root = ...\Escape Room\er
$repoRoot = (Get-Item "$PSScriptRoot\..\..").FullName

# local ER folder for this env -> C:\Users\...\Escape Room\er\er1
$localRoot = Join-Path $repoRoot "er1"

if (-not (Test-Path $localRoot)) {
    throw "Pi runtime folder not found: $localRoot"
}

# ----- Env mapping -----

switch ($Env) {
    "er1" {
        $piUser     = "rudyy"
        $piHost     = "100.108.1.80"   # Tailscale IP
        $remoteRoot = "/home/rudyy/er1"
    }
    default {
        throw "Unknown environment: $Env"
    }
}

$piTarget = "{0}@{1}" -f $piUser, $piHost

Write-Host ("[deploy-er1] Env:         {0}" -f $Env)
Write-Host ("[deploy-er1] Mode:        {0}" -f $Mode)
Write-Host ("[deploy-er1] Local root:  {0}" -f $localRoot)
Write-Host ("[deploy-er1] Remote root: {0}:{1}" -f $piTarget, $remoteRoot)

# ----- Ensure remote directory exists -----

ssh $piTarget "mkdir -p '$remoteRoot'"

# ----- Helper: run rsync -----

function Invoke-Rsync {
    param(
        [string]$Source,
        [string]$Dest,
        [string[]]$ExtraArgs
    )

    $args = @("-avz", "--delete") + $ExtraArgs + @($Source, $Dest)
    Write-Host "[deploy-er1] rsync $($args -join ' ')"
    & rsync @args
    if ($LASTEXITCODE -ne 0) {
        throw "rsync failed with exit code $LASTEXITCODE"
    }
}

# ----- Mode: full (mirror whole er1 tree, with deletes) -----

if ($Mode -eq "full") {
    # Mirror everything under er1/, but skip local build crud if you want later
    Invoke-Rsync -Source ("{0}/" -f $localRoot) `
                 -Dest   ("{0}:{1}/" -f $piTarget, $remoteRoot) `
                 -ExtraArgs @(
                    "--exclude=.pio",
                    "--exclude=.sconsign*",
                    "--exclude=.vscode"
                 )
}
else {
    # ----- Mode: runtime (only what the Pi needs to run) -----

    # 1) CLI wrapper
    if (Test-Path (Join-Path $localRoot "er1")) {
        Invoke-Rsync -Source (Join-Path $localRoot "er1") `
                     -Dest   ("{0}:{1}/" -f $piTarget, $remoteRoot) `
                     -ExtraArgs @()
    }

    # 2) scripts/ (CLI helpers, mqtt-logs.sh, mqtt-locks.sh, ota, ...)
    if (Test-Path (Join-Path $localRoot "scripts")) {
        Invoke-Rsync -Source (Join-Path $localRoot "scripts/") `
                     -Dest   ("{0}:{1}/scripts/" -f $piTarget, $remoteRoot) `
                     -ExtraArgs @()
    }

    # 3) config/ (runtime config)
    if (Test-Path (Join-Path $localRoot "config")) {
        Invoke-Rsync -Source (Join-Path $localRoot "config/") `
                     -Dest   ("{0}:{1}/config/" -f $piTarget, $remoteRoot) `
                     -ExtraArgs @()
    }

    # 4) systemd/ (unit files)
    if (Test-Path (Join-Path $localRoot "systemd")) {
        Invoke-Rsync -Source (Join-Path $localRoot "systemd/") `
                     -Dest   ("{0}:{1}/systemd/" -f $piTarget, $remoteRoot) `
                     -ExtraArgs @()
    }

    # NOTE: we deliberately do NOT touch logs/ in "runtime" mode
}

# ----- Fix execute bits on remote -----

ssh $piTarget @"
set -e
cd '$remoteRoot'
chmod +x ./er1 2>/dev/null || true
if [ -d scripts ]; then
  chmod +x scripts/*.sh 2>/dev/null || true
  chmod +x scripts/ota 2>/dev/null || true
fi
"@

Write-Host "[deploy-er1] Deploy complete."
Write-Host "[deploy-er1] Next step: run .\pc-scripts\push.ps1 to commit & push." -ForegroundColor Yellow
