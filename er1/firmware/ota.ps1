param(
    [ValidateSet("maglock","images_piano","chess","knocking","candles","star_sky","star_slider","stop_timer")]
    [string]$Target,
    [string]$Env,
    [string]$Dev,
    [switch]$NoBuild
)

$scriptDir = Split-Path -Parent $PSCommandPath
Push-Location $scriptDir
try {

# ============ DEPLOYMENT MAP ============
# Canonical OTA map (Env/Dev/CmdNode/FirmwareName/LegacyFirmwareNames/VerifyNodes)
$deployments = @{
    "maglock" = @{
        Env                 = "maglock"
        Dev                 = "maglock"
        CmdNode             = "maglock"
        FirmwareName        = "maglock.bin"
        LegacyFirmwareNames = @("maglock_ctrl.bin")
        VerifyNodes         = @("maglock")
    }
    "images_piano" = @{
        Env          = "images_piano"
        Dev          = "images_piano"
        CmdNode      = "images"
        FirmwareName = "images_piano.bin"
        VerifyNodes  = @("images","piano")
    }
    "chess" = @{
        Env          = "chess"
        Dev          = "chess"
        CmdNode      = "chess"
        FirmwareName = "chess.bin"
    }
    "knocking" = @{
        Env          = "knocking"
        Dev          = "knocking"
        CmdNode      = "knocking"
        FirmwareName = "knocking.bin"
    }
    "candles" = @{
        Env          = "candles"
        Dev          = "candles"
        CmdNode      = "candles"
        FirmwareName = "candles.bin"
    }
    "star_sky" = @{
        Env          = "star_sky"
        Dev          = "star_sky"
        CmdNode      = "star_sky"
        FirmwareName = "star_sky.bin"
    }
    "star_slider" = @{
        Env          = "star_slider"
        Dev          = "star_slider"
        CmdNode      = "star_slider"
        FirmwareName = "star_slider.bin"
    }
    "stop_timer" = @{
        Env          = "stop_timer"
        Dev          = "stop_timer"
        CmdNode      = "stop_timer"
        FirmwareName = "stop_timer.bin"
    }
}

function Resolve-Deployment([string]$target) {
    if (-not $deployments.ContainsKey($target)) { return $null }
    $cfg = $deployments[$target].Clone()
    if (-not $cfg.ContainsKey("CmdNode") -or -not $cfg.CmdNode) { $cfg.CmdNode = $cfg.Dev }
    if (-not $cfg.ContainsKey("FirmwareName") -or -not $cfg.FirmwareName) { $cfg.FirmwareName = "$($cfg.Dev).bin" }
    if (-not $cfg.ContainsKey("VerifyNodes") -or -not $cfg.VerifyNodes) { $cfg.VerifyNodes = @($cfg.CmdNode) }
    if (-not $cfg.ContainsKey("LegacyFirmwareNames") -or -not $cfg.LegacyFirmwareNames) { $cfg.LegacyFirmwareNames = @() }
    return $cfg
}

function Test-RemoteFirmwareAvailability([string]$url, [string]$sshTarget) {
    $via = "local"
    $output = ""
    $exitCode = 0

    if ($sshTarget) {
        $output = ssh $sshTarget "curl -I -sS --connect-timeout 5 --max-time 10 $url" 2>&1
        $exitCode = $LASTEXITCODE
        $via = "ssh:$sshTarget"
    } else {
        $curlExe = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curlExe) {
            $output = & $curlExe.Path "-I" "-sS" "--connect-timeout" "5" "--max-time" "10" $url 2>&1
            $exitCode = $LASTEXITCODE
        } else {
            try {
                $resp = Invoke-WebRequest -Method Head -Uri $url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            } catch {
                Write-Error ("Postflight failed for {0} (local): {1}" -f $url, $_.Exception.Message)
                return $false
            }

            if ($resp.StatusCode -ne 200) {
                Write-Error "Postflight failed for $url (local status $($resp.StatusCode))"
                return $false
            }

            $cl = $resp.Headers["Content-Length"]
            try { $len = [int64]$cl } catch { $len = -1 }
            if ($len -le 0) {
                Write-Error "Postflight failed for $url (local Content-Length $cl)"
                return $false
            }

            return $true
        }
    }

    $outText = ($output | ForEach-Object { "$_" }) -join "`n"

    if ($exitCode -ne 0) {
        Write-Error "Postflight failed for $url via $via (curl exit $exitCode). Output:`n$outText"
        return $false
    }

    $okStatus = $outText -match 'HTTP/\d\.\d\s+200'
    $lenMatch = [regex]::Match($outText, 'Content-Length:\s*(\d+)', 'IgnoreCase')
    $okLen = $lenMatch.Success -and ([int64]$lenMatch.Groups[1].Value -gt 0)

    if (-not ($okStatus -and $okLen)) {
        Write-Error "Postflight failed for $url via $via (status/content-length missing). Output:`n$outText"
        return $false
    }

    return $true
}

# ============ Resolve Env/Dev ============
if ($Target) {
    $cfg = Resolve-Deployment $Target
    if (-not $cfg) {
        Write-Error "Unknown Target '$Target'. Valid: $($deployments.Keys -join ', ')"
        exit 1
    }

    if (-not $Env)  { $Env  = $cfg.Env }
    if (-not $Dev)  { $Dev  = $cfg.Dev }
}

if (-not $Env -or -not $Dev) {
    Write-Error "You must either: -Target <name> OR provide -Env/-Dev manually."
    exit 1
}

$cfg = if ($Target) { Resolve-Deployment $Target } else { @{ Env = $Env; Dev = $Dev; CmdNode = $Dev; FirmwareName = "$Dev.bin"; LegacyFirmwareNames = @(); VerifyNodes = @($Dev) } }
$cmdNode = $cfg.CmdNode
$firmwareName = $cfg.FirmwareName
$legacyNames = $cfg.LegacyFirmwareNames
$verifyNodes = $cfg.VerifyNodes

Write-Host "== TARGET = $Target  Env=$Env  Dev=$Dev  CmdNode=$cmdNode  Firmware=$firmwareName  VerifyNodes=$($verifyNodes -join ',') =="

# ============ Locate platformio.exe ============
$possiblePaths = @(
    "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe",
    "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe"
)

$pioFull = $null
foreach ($p in $possiblePaths) {
    if (Test-Path $p) { $pioFull = $p; break }
}

if (-not $pioFull) {
    Write-Error "PlatformIO not found."
    exit 1
}

Write-Host "== Using PlatformIO: $pioFull =="

# ============ BUILD ============
if ($NoBuild) {
    Write-Host "== Skipping build (NoBuild switch set) =="
} else {
    Write-Host "== Building environment '$Env' =="

    & $pioFull run -e $Env
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Build failed"
        exit 1
    }
}

$firmwarePath = ".pio/build/$Env/firmware.bin"
Write-Host "== Preflight: verifying firmware exists at $firmwarePath =="
if (-not (Test-Path $firmwarePath)) {
    Write-Error "Firmware not found: $firmwarePath"
    exit 1
}

# ============ PI / MQTT CONFIG ============
$piUser        = "rudyy"
$piHost        = "100.108.1.80"    # Tailscale IP of Pi
$piFirmwareDir = "/home/rudyy/firmware"

# Topic form: <CmdNode>/cmd
$topicUpdate = "$cmdNode/cmd"

# ============ SCP UPLOAD ============
Write-Host "== Uploading firmware to Pi as $firmwareName =="

$ensureDirCmd = "mkdir -p $piFirmwareDir"
ssh "$piUser@$piHost" $ensureDirCmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create firmware directory on Pi at $piFirmwareDir"
    exit 1
}

$remotePath = "$piUser@${piHost}:$piFirmwareDir/$firmwareName"
scp $firmwarePath $remotePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP failed"
    exit 1
}

# ============ LEGACY FILENAME COPIES ============
# Keep legacy filenames available on the Pi during rolling migrations.
foreach ($legacyName in $legacyNames) {
    $copyCmd = "cp $piFirmwareDir/$firmwareName $piFirmwareDir/$legacyName"
    Write-Host "== Creating legacy copy on Pi: $legacyName =="
    ssh "$piUser@$piHost" $copyCmd
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create legacy firmware copy $legacyName on Pi"
        exit 1
    }
    Write-Host "== Created legacy copy: $piFirmwareDir/$legacyName =="
}

# ============ POSTFLIGHT CHECK ============
$postflightUrl = "http://192.168.0.10/firmware/$firmwareName"
Write-Host "== Verifying OTA URL from Pi: $postflightUrl =="
if (-not (Test-RemoteFirmwareAvailability $postflightUrl "$piUser@$piHost")) {
    exit 1
}

# ============ TRIGGER OTA ============
Write-Host "== Triggering OTA on $topicUpdate =="

$otaPublishCmd = @(
    "python3"
    "/home/rudyy/er1/scripts/ota_publish.py"
    "--dev", "$Dev"
    "--cmd-node", "$cmdNode"
    "--broker", "192.168.0.10"
    "--url", "/firmware/$firmwareName"
    "--file", "$piFirmwareDir/$firmwareName"
) -join " "

ssh "$piUser@$piHost" $otaPublishCmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "OTA publish failed via $piUser@$piHost (exit $LASTEXITCODE)"
    exit 1
}

Write-Host "== DONE. Device will update. =="

} finally {
    Pop-Location
}
