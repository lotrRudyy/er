Set-StrictMode -Version Latest

param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Er1Pi,
    [Parameter(Mandatory = $true)][string]$Er1Cmd,
    [ValidateSet("runtime", "full")][string]$Mode = "runtime",
    [switch]$DryRun,
    [switch]$RestartServices,
    [switch]$Verify
)

function Invoke-Er1Sync {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest,
        [string[]]$ExtraArgs,
        [switch]$DryRun,
        [string]$Prefix = "[er1 deploy]"
    )

    $rsyncCmd = Get-Command rsync -ErrorAction SilentlyContinue

    if ($rsyncCmd) {
        $rsyncArgs = @("-avz", "--delete")
        if ($DryRun) { $rsyncArgs += @("--dry-run", "--itemize-changes") }
        $rsyncArgs += ($ExtraArgs | Where-Object { $_ })
        $rsyncArgs += @($Source, $Dest)

        Write-Host "$Prefix rsync $($rsyncArgs -join ' ')"
        & rsync @rsyncArgs
        if ($LASTEXITCODE -ne 0) {
            throw "rsync failed with exit code $LASTEXITCODE"
        }
        return
    }

    if ($DryRun) {
        throw "DryRun requested but rsync not installed. Install rsync or run without -DryRun."
    }

    Write-Host "$Prefix rsync not found, falling back to scp -r (no delete semantics)" -ForegroundColor Yellow
    Write-Host "$Prefix scp -r `"$Source`" `"$Dest`""
    & scp -r "$Source" "$Dest"
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Er1Deploy {
    param(
        [ValidateSet("runtime", "full")]
        [string]$Mode = "runtime",

        [switch]$DryRun,
        [switch]$RestartServices,
        [switch]$Verify
    )

    $prefix     = "[er1 deploy]"
    $remoteRoot = Split-Path -Parent $Er1Cmd
    if (-not $remoteRoot -or $remoteRoot.Trim() -eq "" -or $remoteRoot -eq "/") {
        throw "Refusing deploy: remoteRoot invalid ($remoteRoot)"
    }

    if (-not (Test-Path $RepoRoot)) {
        throw "Repo root not found: $RepoRoot"
    }

    $envRoot     = Join-Path $RepoRoot "er1"
    $runtimeRoot = Join-Path $envRoot "pi-runtime"

    if (-not (Test-Path $runtimeRoot)) {
        throw "Pi runtime folder not found: $runtimeRoot"
    }

    $otaPublish = Join-Path (Join-Path $runtimeRoot "scripts") "ota_publish.py"
    if (-not (Test-Path $otaPublish)) {
        throw "ota_publish.py missing: $otaPublish"
    }

    Write-Host "$prefix Target Pi:   $Er1Pi"
    Write-Host "$prefix Remote root: $remoteRoot"
    Write-Host "$prefix Local root:  $runtimeRoot"
    Write-Host "$prefix Mode:        $Mode"
    Write-Host "$prefix Meaning:"
    Write-Host "  runtime = scripts/config/systemd/docs (+ wrapper if present)"
    Write-Host "  full    = entire pi-runtime folder (still not whole repo)"
    Write-Host "$prefix Ensuring ota_publish.py will be synced: $otaPublish"

    $requiredRemoteDirs = @(
        $remoteRoot,
        "$remoteRoot/scripts",
        "$remoteRoot/config",
        "$remoteRoot/systemd",
        "$remoteRoot/docs"
    )

    $uniqueRemoteDirs = $requiredRemoteDirs |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique

    if ($uniqueRemoteDirs.Count -gt 0) {
        $joined = $uniqueRemoteDirs -join "' '"
        ssh $Er1Pi "mkdir -p '$joined'"
        if ($LASTEXITCODE -ne 0) { throw "Unable to ensure remote path(s) (exit $LASTEXITCODE)." }
    }

    if ($Mode -eq "full") {
        Invoke-Er1Sync `
            -Source ("{0}/" -f $runtimeRoot) `
            -Dest ("{0}:{1}/" -f $Er1Pi, $remoteRoot) `
            -ExtraArgs @("--exclude=.pio", "--exclude=.sconsign*", "--exclude=.vscode") `
            -DryRun:$DryRun `
            -Prefix $prefix
    } else {
        $cliWrapper = Join-Path $envRoot "er1"
        if (Test-Path $cliWrapper) {
            Invoke-Er1Sync `
                -Source $cliWrapper `
                -Dest ("{0}:{1}/" -f $Er1Pi, $remoteRoot) `
                -ExtraArgs @() `
                -DryRun:$DryRun `
                -Prefix $prefix
        }

        $runtimeItems = @(
            @{ Path = (Join-Path $runtimeRoot "scripts"); Remote = "/scripts/"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "config");  Remote = "/config/";  Extra = @("--exclude=local.env") },
            @{ Path = (Join-Path $runtimeRoot "systemd"); Remote = "/systemd/"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "docs");    Remote = "/docs/";    Extra = @() }
        )

        $remoteDirs = $runtimeItems |
            ForEach-Object {
                $r = "{0}{1}" -f $remoteRoot, $_.Remote
                $r.TrimEnd("/")
            } | Sort-Object -Unique

        if ($remoteDirs.Count -gt 0) {
            $joinedRuntime = $remoteDirs -join "' '"
            ssh $Er1Pi "mkdir -p '$joinedRuntime'"
            if ($LASTEXITCODE -ne 0) { throw "Unable to ensure remote runtime subdirectories (exit $LASTEXITCODE)." }
        }

        foreach ($item in $runtimeItems) {
            if (Test-Path $item.Path) {
                Invoke-Er1Sync `
                    -Source ("{0}/" -f $item.Path) `
                    -Dest ("{0}:{1}{2}" -f $Er1Pi, $remoteRoot, $item.Remote) `
                    -ExtraArgs $item.Extra `
                    -DryRun:$DryRun `
                    -Prefix $prefix
            }
        }
    }

    ssh $Er1Pi @"
set -e
cd '$remoteRoot'
chmod +x ./er1 2>/dev/null || true
if [ -d scripts ]; then
  chmod +x scripts/*.sh 2>/dev/null || true
  chmod +x scripts/ota 2>/dev/null || true
fi
"@
    if ($LASTEXITCODE -ne 0) { throw "Unable to refresh remote permissions (exit $LASTEXITCODE)." }

    if ($RestartServices) {
        Write-Host "$prefix Restarting services..." -ForegroundColor Yellow
        ssh $Er1Pi @"
sudo systemctl daemon-reload
sudo systemctl restart er1-mqtt-log.service
sudo systemctl restart er1-ota-verify.service
"@
        if ($LASTEXITCODE -ne 0) { throw "Service restart failed (exit $LASTEXITCODE)." }
    }

    if ($Verify) {
        Write-Host "$prefix Verifying..." -ForegroundColor Yellow

        ssh $Er1Pi "systemctl is-active er1-mqtt-log.service"
        if ($LASTEXITCODE -ne 0) { throw "er1-mqtt-log.service not active" }

        ssh $Er1Pi "systemctl is-active er1-ota-verify.service"
        if ($LASTEXITCODE -ne 0) { throw "er1-ota-verify.service not active" }

        ssh $Er1Pi "$Er1Cmd status_mqtt"
        if ($LASTEXITCODE -ne 0) { throw "status_mqtt failed" }
    }

    Write-Host "$prefix Deploy complete." -ForegroundColor Green
}

Invoke-Er1Deploy -Mode $Mode -DryRun:$DryRun -RestartServices:$RestartServices -Verify:$Verify
