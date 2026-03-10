param(
    [ValidateSet("all","maglock","lighting","images_piano","chess","knocking","candles","star_sky","star_slider")]
    [string[]]$Target,
    [string]$Env,
    [string]$Dev,
    [switch]$NoBuild
)

# ota.ps1
# - Supports:
#     pwsh -File ota.ps1 -Target maglock
#     pwsh -File ota.ps1 -Target maglock lighting candles
#     pwsh -File ota.ps1 -Target all
# - In multi-target mode, press Q to stop after the current node finishes.

$scriptDir = Split-Path -Parent $PSCommandPath

$deployments = @{
    "maglock"      = @{ Env="maglock";      Dev="maglock";      CmdNode="maglock";      FirmwareName="maglock.bin" }
    "lighting"     = @{ Env="lighting";     Dev="lighting";     CmdNode="lighting";     FirmwareName="lighting.bin" }
    "images_piano" = @{ Env="images_piano"; Dev="images_piano"; CmdNode="images_piano"; FirmwareName="images_piano.bin" }
    "chess"        = @{ Env="chess";        Dev="chess";        CmdNode="chess";        FirmwareName="chess.bin" }
    "knocking"     = @{ Env="knocking";     Dev="knocking";     CmdNode="knocking";     FirmwareName="knocking.bin" }
    "candles"      = @{ Env="candles";      Dev="candles";      CmdNode="candles";      FirmwareName="candles.bin" }
    "star_sky"     = @{ Env="star_sky";     Dev="star_sky";     CmdNode="star_sky";     FirmwareName="star_sky.bin" }
    "star_slider"  = @{ Env="star_slider";  Dev="star_slider";  CmdNode="star_slider";  FirmwareName="star_slider.bin" }
}

$allTargets = @(
    "maglock",
    "lighting",
    "images_piano",
    "chess",
    "knocking",
    "candles",
    "star_sky",
    "star_slider"
)

function Resolve-OtaTargets {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$RequestedTargets
    )

    if (-not $RequestedTargets -or $RequestedTargets.Count -eq 0) {
        throw "Usage: ota.ps1 -Target <device...|all>"
    }

    if ($RequestedTargets -contains "all") {
        return $allTargets
    }

    $seen = @{}
    $resolved = New-Object System.Collections.Generic.List[string]

    foreach ($t in $RequestedTargets) {
        if (-not $deployments.ContainsKey($t)) {
            throw "Unknown target '$t'"
        }
        if (-not $seen.ContainsKey($t)) {
            $seen[$t] = $true
            [void]$resolved.Add($t)
        }
    }

    return $resolved.ToArray()
}

function Get-FirmwareMainPath {
    param([string]$DeviceName)

    $p = Join-Path $scriptDir ("src/{0}_main.cpp" -f $DeviceName)
    if (-not (Test-Path $p)) {
        $p2 = Join-Path $scriptDir ("src/{0}.cpp" -f $DeviceName)
        if (Test-Path $p2) { return $p2 }
        throw "Unable to locate firmware main for '$DeviceName'. Checked: $p and $p2"
    }
    return $p
}

function Get-FirmwareVersion {
    param([string]$DeviceName)

    $mainPath = Get-FirmwareMainPath $DeviceName
    $content = Get-Content -Path $mainPath -Raw
    $match = [regex]::Match($content, 'FW_VERSION\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw "Unable to locate FW_VERSION in $mainPath"
    }
    return $match.Groups[1].Value
}

function Invoke-ProcessWithTimeout {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(Mandatory=$true)][string[]]$ArgumentList,
        [int]$TimeoutSec = 60,
        [string]$What = "process"
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $psi.CreateNoWindow = $true

    foreach ($arg in $ArgumentList) {
        [void]$psi.ArgumentList.Add($arg)
    }

    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not $p.HasExited) {
        Start-Sleep -Milliseconds 100
        if ((Get-Date) -gt $deadline) {
            try { $p.Kill() } catch {}
            throw "$What timed out after ${TimeoutSec}s"
        }
    }

    if ($p.ExitCode -ne 0) {
        throw "$What failed (exit $($p.ExitCode))"
    }
}

function Invoke-OtaSingleTarget {
    param(
        [Parameter(Mandatory=$true)][string]$SingleTarget,
        [string]$Env,
        [string]$Dev,
        [switch]$NoBuild
    )

    Push-Location $scriptDir
    try {
        if (-not $deployments.ContainsKey($SingleTarget)) {
            throw "Unknown target '$SingleTarget'"
        }

        $cfg = $deployments[$SingleTarget]
        if (-not $Env) { $Env = $cfg.Env }
        if (-not $Dev) { $Dev = $cfg.Dev }
        $cmdNode = $cfg.CmdNode
        $firmwareName = $cfg.FirmwareName

        Write-Host ("== TARGET = {0}  Env={1}  Dev={2}  CmdNode={3}  Firmware={4} ==" -f $SingleTarget, $Env, $Dev, $cmdNode, $firmwareName)

        $pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
        Write-Host "== Using PlatformIO: $pio =="

        if (-not $NoBuild) {
            $versionScript = Join-Path $scriptDir "increment_fw_version.ps1"
            & pwsh -NoProfile -File $versionScript -Dev $Dev -RootDir $scriptDir
            if ($LASTEXITCODE -ne 0) { throw "FW_VERSION increment failed" }
        }

        $ver = Get-FirmwareVersion $Dev
        Write-Host ("== Version = {0} ==" -f $ver)

        if (-not $NoBuild) {
            Write-Host ("== Building environment '{0}' ==" -f $Env)
            & $pio run -e $Env
            if ($LASTEXITCODE -ne 0) { throw "Build failed" }
        }

        $firmwarePath = Join-Path $scriptDir (".pio/build/{0}/firmware.bin" -f $Env)
        if (-not (Test-Path $firmwarePath)) {
            throw "Firmware not found at $firmwarePath"
        }
        Write-Host ("== Preflight: verifying firmware exists at {0} ==" -f $firmwarePath)

        $piHost = "192.168.0.10"
        $piUser = "rudyy"
        $piFirmwareDir = "/home/$piUser/er1/node_firmware"
        $httpHost = "192.168.0.10"

        $sshExe = "C:\WINDOWS\System32\OpenSSH\ssh.exe"
        $scpExe = "C:\WINDOWS\System32\OpenSSH\scp.exe"

        $sshBaseArgs = @(
            "-o","BatchMode=yes",
            "-o","StrictHostKeyChecking=accept-new",
            "-o","ConnectTimeout=10",
            "-o","ServerAliveInterval=2",
            "-o","ServerAliveCountMax=4"
        )

        function Get-FileSha256Hex {
            param([string]$Path)
            (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
        }

        $fwSize = (Get-Item $firmwarePath).Length
        Write-Host ("== Uploading firmware to Pi as {0}  ({1} bytes) ==" -f $firmwareName, $fwSize)

        & $sshExe @sshBaseArgs "$piUser@$piHost" "mkdir -p '$piFirmwareDir'"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create firmware directory on Pi at $piFirmwareDir"
        }

        $remotePath = "$piUser@${piHost}:$piFirmwareDir/$firmwareName"
        $scpArgs = @("-B", "-C") + $sshBaseArgs + @($firmwarePath, $remotePath)
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        Invoke-ProcessWithTimeout -FilePath $scpExe -ArgumentList $scpArgs -TimeoutSec 60 -What "SCP upload"
        $sw.Stop()
        Write-Host ("== Upload finished in {0:n2}s ==" -f $sw.Elapsed.TotalSeconds)

        $url = "http://$httpHost/node_firmware/$firmwareName"
        Write-Host ("== Verifying OTA URL from Pi: {0} ==" -f $url)
        Write-Host "== Verifying HTTP-served firmware matches uploaded file (sha256) =="

        $tmpDl = Join-Path $env:TEMP ("er1_ota_" + $SingleTarget + ".bin")
        Invoke-WebRequest -Uri $url -OutFile $tmpDl -UseBasicParsing | Out-Null
        $localSha = Get-FileSha256Hex $firmwarePath
        $httpSha  = Get-FileSha256Hex $tmpDl
        if ($httpSha -ne $localSha) {
            throw "HTTP firmware mismatch: local=$localSha http=$httpSha"
        }
        Write-Host "== HTTP firmware matches uploaded file =="

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

        & $sshExe @sshBaseArgs "$piUser@$piHost" $remoteCmd
        if ($LASTEXITCODE -ne 0) {
            throw "ota_publish.py failed"
        }

        Write-Host "== DONE =="
    }
    finally {
        Pop-Location
    }
}

function Invoke-OtaChildWithSoftStopSupport {
    param(
        [Parameter(Mandatory=$true)][string]$SingleTarget,
        [switch]$NoBuild
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "pwsh"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $psi.CreateNoWindow = $false

    [void]$psi.ArgumentList.Add("-NoProfile")
    [void]$psi.ArgumentList.Add("-File")
    [void]$psi.ArgumentList.Add($PSCommandPath)
    [void]$psi.ArgumentList.Add("-Target")
    [void]$psi.ArgumentList.Add($SingleTarget)
    if ($NoBuild) {
        [void]$psi.ArgumentList.Add("-NoBuild")
    }

    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()

    while (-not $p.HasExited) {
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if (-not $script:StopAfterCurrent -and $key.Key -eq [ConsoleKey]::Q) {
                    $script:StopAfterCurrent = $true
                    Write-Host ""
                    Write-Host "Soft stop requested. Current node will finish, then OTA sequence stops." -ForegroundColor Yellow
                }
            }
        }
        catch {
            # Ignore if console input is unavailable
        }

        Start-Sleep -Milliseconds 200
    }

    if ($p.ExitCode -ne 0) {
        throw "ota.ps1 failed for $SingleTarget (exit $($p.ExitCode))."
    }
}

$resolvedTargets = @(Resolve-OtaTargets -RequestedTargets $Target)

if ($resolvedTargets.Count -eq 1) {
    Invoke-OtaSingleTarget -SingleTarget ([string]$resolvedTargets[0]) -Env $Env -Dev $Dev -NoBuild:$NoBuild
    exit 0
}

$script:StopAfterCurrent = $false
$failures = @()

Write-Host "Press Q at any time to stop after the current node finishes." -ForegroundColor Yellow

foreach ($singleTarget in $resolvedTargets) {
    Write-Host ""
    Write-Host ("================ OTA {0} ================" -f $singleTarget) -ForegroundColor Cyan

    try {
        Invoke-OtaChildWithSoftStopSupport -SingleTarget $singleTarget -NoBuild:$NoBuild
    }
    catch {
        $failures += ("{0}: {1}" -f $singleTarget, $_.Exception.Message)
        Write-Host ("FAILED: {0}" -f $singleTarget) -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
    }

    if ($script:StopAfterCurrent) {
        Write-Host ""
        Write-Host "Stopped before starting the next node." -ForegroundColor Yellow
        break
    }
}

Write-Host ""
if ($failures.Count -gt 0) {
    throw ("OTA failed for {0} target(s): {1}" -f $failures.Count, ($failures -join "; "))
}

Write-Host "== OTA sequence finished =="
exit 0
