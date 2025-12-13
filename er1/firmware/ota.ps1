param(
    [ValidateSet("maglock","images","chess","knocking","candles","star_sky","star_slider","stop_timer")]
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
#   maglock, images, chess, knocking, candles, star_sky, star_slider, stop_timer
$deviceMap = @{
    "maglock"     = @{ Env = "maglock";       Dev = "maglock"      }
    "images"      = @{ Env = "images";  Dev = "images"       }
    "chess"       = @{ Env = "chess";         Dev = "chess"        }
    "knocking"    = @{ Env = "knocking";      Dev = "knocking"     }
    "candles"     = @{ Env = "candles";       Dev = "candles"      }
    "star_sky"    = @{ Env = "star_sky";      Dev = "star_sky"     }
    "star_slider" = @{ Env = "star_slider";   Dev = "star_slider"  }
    "stop_timer"  = @{ Env = "stop_timer";    Dev = "stop_timer"   }
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
if (-not (Test-Path $firmwarePath)) {
    Write-Error "Firmware not found: $firmwarePath"
    exit 1
}

# ============ PI / MQTT CONFIG ============
$piUser        = "rudyy"
$piHost        = "100.108.1.80"    # Tailscale IP of Pi
$piFirmwareDir = "/home/rudyy/firmware"

# Topic form: <node>/cmd
$topicUpdate = "$Dev/cmd"

# ============ SCP UPLOAD ============
Write-Host "== Uploading firmware to Pi as $Dev.bin =="

$remotePath = "$piUser@${piHost}:$piFirmwareDir/$Dev.bin"
scp $firmwarePath $remotePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP failed"
    exit 1
}

# ============ TRIGGER OTA ============
Write-Host "== Triggering OTA on $topicUpdate =="

# MQTT remains LAN IP because the Pi itself runs mosquitto on 192.168.0.10
ssh "$piUser@$piHost" "mosquitto_pub -h 192.168.0.10 -t '$topicUpdate' -m 'UPDATE'"

Write-Host "== DONE. Device will update. =="

} finally {
    Pop-Location
}
