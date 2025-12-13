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

# ============ DEVICE MAP ============
# Valid -Target values:
#   maglock, images_piano, chess, knocking, candles, star_sky, star_slider, stop_timer
$deviceMap = @{
    "maglock"     = @{ Env = "maglock";       Dev = "maglock"      }
    "images_piano" = @{ Env = "images_piano"; Dev = "images_piano" }
    "chess"       = @{ Env = "chess";         Dev = "chess"        }
    "knocking"    = @{ Env = "knocking";      Dev = "knocking"     }
    "candles"     = @{ Env = "candles";       Dev = "candles"      }
    "star_sky"    = @{ Env = "star_sky";      Dev = "star_sky"     }
    "star_slider" = @{ Env = "star_slider";   Dev = "star_slider"  }
    "stop_timer"  = @{ Env = "stop_timer";    Dev = "stop_timer"   }
}

$remoteNameMap = @{
    "images_piano" = "images_piano.bin"
}

function Get-RemoteFirmwareName([string]$dev) {
    if ($remoteNameMap.ContainsKey($dev)) { return $remoteNameMap[$dev] }
    return "$dev.bin"
}

function Test-RemoteFirmwareAvailability([string]$url, [string]$sshTarget) {
    $via = "local"
    $output = $null
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

    $okStatus = $outText -match 'HTTP/\d\.\d\s+200'
    $lenMatch = [regex]::Match($outText, 'Content-Length:\s*(\d+)', 'IgnoreCase')
    $okLen = $lenMatch.Success -and ([int]$lenMatch.Groups[1].Value -gt 0)

    if (-not ($okStatus -and $okLen)) {
      throw "Postflight failed... Output: $outText"
    }
    return $true


    if ($exitCode -ne 0 -or -not $statusMatch.Success) {
        Write-Error "Postflight failed for $url via $via (curl exit $exitCode). Output:`n$outputText"
        return $false
    }

    $statusCode = [int]$statusMatch.Groups[1].Value
    if ($statusCode -ne 200) {
        Write-Error "Postflight failed for $url via $via (status $statusCode). Output:`n$outputText"
        return $false
    }

    $contentLengthMatch = [regex]::Match($outputText, "(?im)^Content-Length:\\s*(\\d+)")
    if (-not $contentLengthMatch.Success) {
        Write-Error "Postflight failed for $url via $via (missing Content-Length). Output:`n$outputText"
        return $false
    }

    $len = [int64]$contentLengthMatch.Groups[1].Value
    if ($len -le 0) {
        Write-Error "Postflight failed for $url via $via (Content-Length $len). Output:`n$outputText"
        return $false
    }

    return $true
}

# ============ Resolve Env/Dev ============
if ($Target) {
    if (-not $deviceMap.ContainsKey($Target)) {
        Write-Error "Unknown Target '$Target'. Valid: $($deviceMap.Keys -join ', ')"
        exit 1
    }

    $cfg = $deviceMap[$Target]

    if (-not $Env)  { $Env  = $cfg.Env }
    if (-not $Dev)  { $Dev  = $cfg.Dev }
}

if (-not $Env -or -not $Dev) {
    Write-Error "You must either: -Target <name> OR provide -Env/-Dev manually."
    exit 1
}

Write-Host "== TARGET = $Target  Env=$Env  Dev=$Dev =="

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

$remoteFirmwareName = Get-RemoteFirmwareName $Dev

if (-not $env:OTA_PSK) {
    Write-Error "OTA_PSK environment variable is required for HMAC validation."
    exit 1
}

$psk = $env:OTA_PSK
Write-Host "== Computing SHA-256 + HMAC for $firmwarePath =="
$sha256 = (Get-FileHash -Algorithm SHA256 -Path $firmwarePath).Hash.ToLower()
$hmacProvider = New-Object System.Security.Cryptography.HMACSHA256 ([Text.Encoding]::UTF8.GetBytes($psk))
$hmacBytes = $hmacProvider.ComputeHash([Text.Encoding]::UTF8.GetBytes($sha256))
$hmac = ([BitConverter]::ToString($hmacBytes) -replace "-", "").ToLower()
Write-Host "SHA256: $sha256"
Write-Host "HMAC  : $hmac"

# ============ PI / MQTT CONFIG ============
$piUser        = "rudyy"
$piHost        = "100.108.1.80"    # Tailscale IP of Pi
$piFirmwareDir = "/home/rudyy/firmware"

# Topic form: <node>/cmd
$topicUpdate = "$Dev/cmd"

# ============ SCP UPLOAD ============
Write-Host "== Uploading firmware to Pi as $remoteFirmwareName =="

$ensureDirCmd = "mkdir -p $piFirmwareDir"
ssh "$piUser@$piHost" $ensureDirCmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create firmware directory on Pi at $piFirmwareDir"
    exit 1
}

$remotePath = "$piUser@${piHost}:$piFirmwareDir/$remoteFirmwareName"
scp $firmwarePath $remotePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP failed"
    exit 1
}

# ============ POSTFLIGHT CHECK ============
$postflightUrl = "http://192.168.0.10/firmware/$remoteFirmwareName"
Write-Host "== Verifying OTA URL from Pi: $postflightUrl =="
if (-not (Test-RemoteFirmwareAvailability $postflightUrl "$piUser@$piHost")) {
    exit 1
}

# ============ TRIGGER OTA ============
Write-Host "== Triggering OTA on $topicUpdate =="

# MQTT remains LAN IP because the Pi itself runs mosquitto on 192.168.0.10
$otaCmd = "UPDATE sha256=$sha256 hmac=$hmac url=/firmware/$remoteFirmwareName"
ssh "$piUser@$piHost" "mosquitto_pub -h 192.168.0.10 -t '$topicUpdate' -m '$otaCmd'"

Write-Host "== DONE. Device will update. =="

} finally {
    Pop-Location
}
