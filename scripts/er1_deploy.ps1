param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Er1Pi,
    [Parameter(Mandatory = $true)][string]$Er1Cmd,
    [ValidateSet("runtime", "full")][string]$Mode = "runtime",
    [switch]$DryRun,
    [switch]$RestartServices,
    [switch]$Verify
)

Set-StrictMode -Version Latest

$script:Er1RemoteRoot  = "/home/rudyy/er1"
$script:Er1ExcludeArgs = @("--exclude=__pycache__/", "--exclude=*.pyc")

function Join-RemotePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Child
    )

    $r = $Root.Replace('\','/').TrimEnd('/')
    $c = $Child.Replace('\','/').Trim('/')
    return "$r/$c"
}

function Get-RemoteDestinationParts {
    param(
        [Parameter(Mandatory = $true)][string]$Dest,
        [string]$Prefix = "[er1 deploy]"
    )

    if ($Dest -notmatch "^(?<host>[^:]+):(?<path>.+)$") {
        throw "$Prefix Remote dest must be user@host:/abs/path (got '$Dest')"
    }

    $host = $Matches.host
    $path = $Matches.path.Replace('\', '/')
    $path = "/" + $path.TrimStart("/")

    if ($path -notlike "$($script:Er1RemoteRoot)*") {
        throw "$Prefix Refusing to deploy outside $($script:Er1RemoteRoot). Got: $path"
    }

    return @{
        Host       = $host
        Path       = $path
        Normalized = "{0}:{1}" -f $host, $path
    }
}

function Invoke-Er1Sync {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest,
        [string[]]$ExtraArgs,
        [switch]$DryRun,
        [string]$Prefix = "[er1 deploy]"
    )

    $destInfo      = Get-RemoteDestinationParts -Dest $Dest -Prefix $Prefix
    $destNormalized = $destInfo.Normalized
    $excludeArgs    = $ExtraArgs | Where-Object { $_ -like "--exclude=*" }
    $rsyncCmd = Get-Command rsync -ErrorAction SilentlyContinue

    if ($rsyncCmd) {
        $rsyncArgs = @("-avz", "--delete")
        if ($DryRun) { $rsyncArgs += @("--dry-run", "--itemize-changes") }
        $rsyncArgs += ($ExtraArgs | Where-Object { $_ })
        $rsyncArgs += @($Source, $destNormalized)

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

    $sourceItem = Get-Item -LiteralPath $Source
    $isDirectory = $sourceItem.PSIsContainer

    if ($isDirectory) {
        $tarArgs = @()
        foreach ($ex in $excludeArgs) { $tarArgs += $ex }
        $tarArgs += @("-cf", "-", "-C", $sourceItem.FullName, ".")
        $extractCmd = "tar -xf - -C '$($destInfo.Path)'"

        Write-Host "$Prefix rsync not found, falling back to tar|ssh (no delete semantics; excludes honored)" -ForegroundColor Yellow
        Write-Host "$Prefix tar $($tarArgs -join ' ') | ssh $($destInfo.Host) $extractCmd"
        & tar @tarArgs | ssh $destInfo.Host $extractCmd
        if ($LASTEXITCODE -ne 0) {
            throw "tar|ssh fallback failed with exit code $LASTEXITCODE"
        }
    }
    else {
        Write-Host "$Prefix rsync not found, falling back to scp (no delete semantics)" -ForegroundColor Yellow
        Write-Host "$Prefix scp `"$($sourceItem.FullName)`" `"$destNormalized`""
        & scp "$($sourceItem.FullName)" "$destNormalized"
        if ($LASTEXITCODE -ne 0) {
            throw "scp failed with exit code $LASTEXITCODE"
        }
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
    $remoteRoot = $script:Er1RemoteRoot.Replace('\','/').TrimEnd('/')
    if ($remoteRoot -like "*/scripts") {
        $remoteRoot = Split-Path -Parent $remoteRoot
    }
    if ($remoteRoot -notlike "/*") {
        $remoteRoot = "/" + $remoteRoot.TrimStart("/")
    }

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

    $ensureRuntimeDirsCmd = "mkdir -p {0}/{{scripts,config,systemd,docs}}" -f $remoteRoot
    Write-Host "$prefix Ensuring remote runtime directories exist..."
    ssh $Er1Pi $ensureRuntimeDirsCmd
    if ($LASTEXITCODE -ne 0) { throw "Unable to ensure remote runtime paths (exit $LASTEXITCODE)." }

    if ($Mode -eq "full") {
        Invoke-Er1Sync `
            -Source ("{0}/" -f $runtimeRoot) `
            -Dest ("{0}:{1}/" -f $Er1Pi, ($remoteRoot.TrimEnd('/') + "/")) `
            -ExtraArgs @("--exclude=.pio", "--exclude=.sconsign*", "--exclude=.vscode") + $script:Er1ExcludeArgs `
            -DryRun:$DryRun `
            -Prefix $prefix
    } else {
        $cliWrapper = Join-Path $envRoot "er1"
        if (Test-Path $cliWrapper) {
            Invoke-Er1Sync `
                -Source $cliWrapper `
                -Dest ("{0}:{1}/" -f $Er1Pi, $remoteRoot) `
                -ExtraArgs $script:Er1ExcludeArgs `
                -DryRun:$DryRun `
                -Prefix $prefix
        }

        $runtimeItems = @(
            @{ Path = (Join-Path $runtimeRoot "scripts"); RemoteRel = "scripts"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "config");  RemoteRel = "config";  Extra = @("--exclude=local.env") },
            @{ Path = (Join-Path $runtimeRoot "systemd"); RemoteRel = "systemd"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "docs");    RemoteRel = "docs";    Extra = @() }
        )

        foreach ($item in $runtimeItems) {
            if (Test-Path $item.Path) {
                $remoteTarget = Join-RemotePath -Root $remoteRoot -Child $item.RemoteRel
                Invoke-Er1Sync `
                    -Source ("{0}/" -f $item.Path) `
                    -Dest ("{0}:{1}/" -f $Er1Pi, $remoteTarget) `
                    -ExtraArgs ($item.Extra + $script:Er1ExcludeArgs) `
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

    $otaRemotePath = "/home/rudyy/er1/scripts/ota_publish.py"
    ssh $Er1Pi "test -f '$otaRemotePath'"
    if ($LASTEXITCODE -ne 0) { throw "ota_publish.py missing after deploy: $otaRemotePath" }

    Write-Host "$prefix Deploy complete." -ForegroundColor Green
    Write-Host "$prefix Verify publisher exists: ssh rudyy@<pi> \"ls -la /home/rudyy/er1/scripts/ota_publish.py\"" -ForegroundColor Cyan
}

Invoke-Er1Deploy -Mode $Mode -DryRun:$DryRun -RestartServices:$RestartServices -Verify:$Verify
