param(
    [Parameter(Mandatory=$true)]
    [string]$Target
)

# =====================
# CONFIG
# =====================

$piHost = "192.168.0.10"
$piUser = "rudyy"
$httpHost = $piHost

$scriptDir = Split-Path -Parent $PSCommandPath
$firmwareRoot = Join-Path $scriptDir "..\er1\firmware"
$pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"

$otaVersion = "1.20"
$timeoutVerify = 25

# =====================
# DEPLOY MAP
# =====================

$deployments = @{
    "knocking" = @{
        Env = "knocking"
        Firmware = "knocking.bin"
    }
}

if (-not $deployments.ContainsKey($Target)) {
    throw "Unknown target '$Target'"
}

$cfg = $deployments[$Target]
$envName = $cfg.Env
$firmwareName = $cfg.Firmware

# =====================
# HELPERS
# =====================

function Invoke-ProcessWithTimeout {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(Mandatory=$true)][string[]]$ArgumentList,
        [int]$TimeoutSec = 60,
        [string]$What = "process"
    )

    $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -NoNewWindow -PassThru

    $deadline = (Get-Date).AddSeconds($TimeoutSec)

    while (-not $p.HasExited) {
        Start-Sleep -Milliseconds 100
        if ((Get-Date) -gt $deadline) {
            try { Stop-Process -Id $p.Id -Force } catch {}
            throw "$What timed out after ${TimeoutSec}s"
        }
    }

    if ($p.ExitCode -ne 0) {
        throw "$What failed (exit $($p.ExitCode))"
    }
}

function Get-FileSha256Hex {
    param([string]$Path)
    (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
}

# =====================
# BUILD
# =====================

Write-Host "== Building $envName =="

Push-Location $firmwareRoot
& $pio run -e $envName
if ($LASTEXITCODE -ne 0) {
    throw "Build failed"
}
Pop-Location

$firmwarePath = Join-Path $firmwareRoot ".pio\build\$envName\firmware.bin"

if (-not (Test-Path $firmwarePath)) {
    throw "Firmware not found: $firmwarePath"
}

$firmwareSize = (Get-Item $firmwarePath).Length

# Generate build id
$alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
$rng = New-Object System.Random
$otaBuild = -join (1..20 | ForEach-Object { $alphabet[$rng.Next(0,$alphabet.Length)] })

Write-Host "== Build = $otaBuild =="

# =====================
# UPLOAD TO PI
# =====================

$remotePath = "$piUser@$piHost:/home/$piUser/er1/node_firmware/$firmwareName"

Write-Host "== Uploading firmware to Pi ($firmwareSize bytes) =="

$scpArgs = @(
    "-o","BatchMode=yes",
    "-o","ConnectTimeout=5",
    "-o","ServerAliveInterval=5",
    "-o","ServerAliveCountMax=2",
    $firmwarePath,
    $remotePath
)

Invoke-ProcessWithTimeout -FilePath "scp" -ArgumentList $scpArgs -TimeoutSec 30 -What "scp upload"

Write-Host "== Upload finished =="

# =====================
# VERIFY HTTP SHA
# =====================

$url = "http://$httpHost/node_firmware/$firmwareName"

Write-Host "== Verifying HTTP SHA256 =="

$tmp = Join-Path $env:TEMP "er1_ota_verify.bin"
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing

$localSha = Get-FileSha256Hex $firmwarePath
$httpSha  = Get-FileSha256Hex $tmp

if ($localSha -ne $httpSha) {
    throw "SHA mismatch (local=$localSha http=$httpSha)"
}

Write-Host "== HTTP firmware matches =="

# =====================
# TRIGGER OTA (PI SIDE)
# =====================

Write-Host "== Triggering OTA =="

$remoteCmd = @(
    "python3",
    "/home/$piUser/er1/scripts/ota_publish.py",
    "--node", $Target,
    "--url", $url,
    "--version", $otaVersion,
    "--build", $otaBuild,
    "--target", $Target,
    "--sha256", $localSha,
    "--size", $firmwareSize,
    "--verify",
    "--timeout", $timeoutVerify,
    "--up-max", "10"
) -join " "

$sshArgs = @(
    "-o","BatchMode=yes",
    "-o","ConnectTimeout=5",
    "$piUser@$piHost",
    $remoteCmd
)

$result = & ssh @sshArgs
if ($LASTEXITCODE -ne 0) {
    throw "ota_publish.py failed"
}

Write-Host $result
Write-Host "== DONE =="
