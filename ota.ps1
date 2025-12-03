param(
    [string]$Target,
    [string]$Env,
    [string]$Dev,
    [string]$Room
)

# ============ DEVICE MAP ============
$deviceMap = @{
    "maglock_ctrl" = @{ Env = "room0_maglock_ctrl"; Dev = "maglock_ctrl";   Room = "room0"  }
    "images_piano" = @{ Env = "room1_images_piano"; Dev = "images_piano";   Room = "room1" }
    "chess"        = @{ Env = "room2_chess";        Dev = "chess";          Room = "room2" }
    "knocking"     = @{ Env = "room3_knocking";     Dev = "knocking";       Room = "room3" }
    "candle"       = @{ Env = "room3_candles";      Dev = "candles";        Room = "room3" }
    "star_sky"     = @{ Env = "room3_star_sky";     Dev = "star_sky";       Room = "room3" }
    "star_slider"  = @{ Env = "room3_star_slider";  Dev = "star_slider";    Room = "room3" }
    "stop_timer"   = @{ Env = "room3_stop_timer";   Dev = "stop_timer";     Room = "room3" }
}

# ============ Resolve Env/Dev/Room ============
if ($Target) {
    if (-not $deviceMap.ContainsKey($Target)) {
        Write-Error "Unknown Target '$Target'. Valid: $($deviceMap.Keys -join ', ')"
        exit 1
    }

    $cfg = $deviceMap[$Target]

    if (-not $Env)  { $Env  = $cfg.Env }
    if (-not $Dev)  { $Dev  = $cfg.Dev }
    if (-not $Room) { $Room = $cfg.Room }
}

if (-not $Env -or -not $Dev -or -not $Room) {
    Write-Error "You must either: -Target <name>  OR  provide -Env/-Dev/-Room manually."
    exit 1
}

Write-Host "== TARGET = $Target  Env=$Env  Dev=$Dev  Room=$Room =="

# ============ Locate platformio.exe ============
# PlatformIO installs here by default:
$possiblePaths = @(
    "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe",
    "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe"
)

$pioFull = $null
foreach ($p in $possiblePaths) {
    if (Test-Path $p) { $pioFull = $p; break }
}

if (-not $pioFull) {
    Write-Error "PlatformIO not found. Add it manually or tell me your installation path."
    exit 1
}

Write-Host "== Using PlatformIO at: $pioFull =="

# ============ BUILD ============
Write-Host "== Building environment '$Env' =="

& $pioFull run -e $Env
if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit 1
}

$firmwarePath = ".pio/build/$Env/firmware.bin"
if (-not (Test-Path $firmwarePath)) {
    Write-Error "Firmware not found after build: $firmwarePath"
    exit 1
}

# ============ PI / MQTT CONFIG ============
$piUser        = "rudyy"
$piHost        = "192.168.0.10"
$piFirmwareDir = "/home/rudyy/firmware"

# Topic form: esc/<room>/<dev>/cmd
$topicUpdate = "esc/$Room/$Dev/cmd"

# ============ SCP ============
Write-Host "== Uploading firmware to Pi ($Dev.bin) =="

$remotePath = "$piUser@${piHost}:$piFirmwareDir/$Dev.bin"
scp $firmwarePath $remotePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP failed"
    exit 1
}

# ============ TRIGGER OTA ============
Write-Host "== Triggering OTA on $topicUpdate =="

ssh "$piUser@$piHost" "mosquitto_pub -h 192.168.0.10 -t '$topicUpdate' -m UPDATE"

Write-Host "== DONE. Device should reboot with new firmware. =="
