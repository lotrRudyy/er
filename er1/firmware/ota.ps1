param(
    [ValidateSet("maglock","lighting","images_piano","chess","knocking","candles","star_sky","star_slider","stop_timer")]
    [string]$Target,
    [string]$Env,
    [string]$Dev,
    [switch]$NoBuild
)

# ota.ps1 (Phase 2)
# - Legacy OTA fields removed: no more "build" token passed to ota_publish.py
# - Verification is performed on the Pi by ota_publish.py (offline -> hb with fw match and up<10).
# - This script only builds, uploads to Pi, verifies HTTP sha, and triggers ota_publish.py.

$scriptDir = Split-Path -Parent $PSCommandPath
Push-Location $scriptDir
try {

  # =====================
  # DEPLOYMENT MAP
  # =====================
  $deployments = @{
      "maglock"      = @{ Env="maglock";      Dev="maglock";      CmdNode="maglock";      FirmwareName="maglock.bin" }
      "lighting"     = @{ Env="lighting";     Dev="lighting";     CmdNode="lighting";     FirmwareName="lighting.bin" }
      "images_piano" = @{ Env="images_piano"; Dev="images_piano"; CmdNode="images_piano"; FirmwareName="images_piano.bin" }
      "chess"        = @{ Env="chess";        Dev="chess";        CmdNode="chess";        FirmwareName="chess.bin" }
      "knocking"     = @{ Env="knocking";     Dev="knocking";     CmdNode="knocking";     FirmwareName="knocking.bin" }
      "candles"      = @{ Env="candles";      Dev="candles";      CmdNode="candles";      FirmwareName="candles.bin" }
      "star_sky"     = @{ Env="star_sky";     Dev="star_sky";     CmdNode="star_sky";     FirmwareName="star_sky.bin" }
      "star_slider"  = @{ Env="star_slider";  Dev="star_slider";  CmdNode="star_slider";  FirmwareName="star_slider.bin" }
      "stop_timer"   = @{ Env="stop_timer";   Dev="stop_timer";   CmdNode="stop_timer";   FirmwareName="stop_timer.bin" }
  }

  if (-not $Target) { throw "Usage: ota.ps1 <target>" }
  if (-not $deployments.ContainsKey($Target)) { throw "Unknown target '$Target'" }

  $cfg = $deployments[$Target]
  if (-not $Env) { $Env = $cfg.Env }
  if (-not $Dev) { $Dev = $cfg.Dev }
  $cmdNode = $cfg.CmdNode
  $firmwareName = $cfg.FirmwareName

  Write-Host ("== TARGET = {0}  Env={1}  Dev={2}  CmdNode={3}  Firmware={4} ==" -f $Target,$Env,$Dev,$cmdNode,$firmwareName)

  # =====================
  # VERSION
  # =====================
  function Get-FirmwareMainPath([string]$dev) {
      $p = Join-Path $scriptDir ("src/{0}_main.cpp" -f $dev)
      if (-not (Test-Path $p)) {
          $p2 = Join-Path $scriptDir ("src/{0}.cpp" -f $dev)
          if (Test-Path $p2) { return $p2 }
          throw "Unable to locate firmware main for '$dev'. Checked: $p and $p2"
      }
      return $p
  }
  function Get-FirmwareVersion([string]$dev) {
      $mainPath = Get-FirmwareMainPath $dev
      $content = Get-Content -Path $mainPath -Raw
      $match = [regex]::Match($content, 'FW_VERSION\s*=\s*"([^"]+)"')
      if (-not $match.Success) { throw "Unable to locate FW_VERSION in $mainPath" }
      return $match.Groups[1].Value
  }

  $ver = Get-FirmwareVersion $Dev
  Write-Host ("== Version = {0} ==" -f $ver)

  # =====================
  # BUILD
  # =====================
  $pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
  Write-Host "== Using PlatformIO: $pio =="

  if (-not $NoBuild) {
      Write-Host ("== Building environment '{0}' ==" -f $Env)
      & $pio run -e $Env
      if ($LASTEXITCODE -ne 0) { throw "Build failed" }
  }

  $firmwarePath = Join-Path $scriptDir (".pio/build/{0}/firmware.bin" -f $Env)
  if (-not (Test-Path $firmwarePath)) { throw "Firmware not found at $firmwarePath" }
  Write-Host ("== Preflight: verifying firmware exists at {0} ==" -f $firmwarePath)

  # =====================
  # PI SETTINGS
  # =====================
  $piHost = "192.168.0.10"
  $piUser = "rudyy"
  $piFirmwareDir = "/home/$piUser/er1/node_firmware"
  $httpHost = "192.168.0.10"

  $sshBaseArgs = @(
      "-o","BatchMode=yes",
      "-o","ConnectTimeout=5",
      "-o","ServerAliveInterval=2",
      "-o","ServerAliveCountMax=4"
  )

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
      if ($p.ExitCode -ne 0) { throw "$What failed (exit $($p.ExitCode))" }
  }
  function Get-FileSha256Hex([string]$Path) {
      (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
  }

  # =====================
  # UPLOAD TO PI
  # =====================
  $fwSize = (Get-Item $firmwarePath).Length
  Write-Host ("== Uploading firmware to Pi as {0}  ({1} bytes) ==" -f $firmwareName, $fwSize)

  & ssh @sshBaseArgs "$piUser@$piHost" "mkdir -p '$piFirmwareDir'"
  if ($LASTEXITCODE -ne 0) { throw "Failed to create firmware directory on Pi at $piFirmwareDir" }

  $remotePath = "$piUser@${piHost}:$piFirmwareDir/$firmwareName"
  $scpArgs = @("-C") + $sshBaseArgs + @($firmwarePath, $remotePath)
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  Invoke-ProcessWithTimeout -FilePath "scp" -ArgumentList $scpArgs -TimeoutSec 60 -What "SCP upload"
  $sw.Stop()
  Write-Host ("== Upload finished in {0:n2}s ==" -f $sw.Elapsed.TotalSeconds)

  # =====================
  # VERIFY HTTP SHA
  # =====================
  $url = "http://$httpHost/node_firmware/$firmwareName"
  Write-Host ("== Verifying OTA URL from Pi: {0} ==" -f $url)
  Write-Host "== Verifying HTTP-served firmware matches uploaded file (sha256) =="

  $tmpDl = Join-Path $env:TEMP ("er1_ota_" + $Target + ".bin")
  Invoke-WebRequest -Uri $url -OutFile $tmpDl -UseBasicParsing | Out-Null
  $localSha = Get-FileSha256Hex $firmwarePath
  $httpSha  = Get-FileSha256Hex $tmpDl
  if ($httpSha -ne $localSha) { throw "HTTP firmware mismatch: local=$localSha http=$httpSha" }
  Write-Host "== HTTP firmware matches uploaded file =="

  # =====================
  # TRIGGER OTA (verification on Pi)
  # =====================
  Write-Host ("== Triggering OTA on {0}/cmd ==" -f $cmdNode)

  $remotePublisher = "/home/$piUser/er1/scripts/ota_publish.py"
  $remoteCmd = @(
      "python3", $remotePublisher,
      "--dev", $Dev,
      "--cmd-node", $cmdNode,
      "--http-host", $httpHost,
      "--version", $ver,
      "--target", $Dev,
      "--firmware-name", $firmwareName,
      "--verify",
      "--timeout", "10",
      "--up-max", "10"
  ) -join " "

  & ssh @sshBaseArgs "$piUser@$piHost" $remoteCmd
  if ($LASTEXITCODE -ne 0) { throw "ota_publish.py failed" }

  Write-Host "== DONE =="

} finally {
  Pop-Location
}
