param(
    [string]$Target,
    [string]$Env,
    [string]$Dev,
    [switch]$NoBuild
)

# ota.ps1
# Supports:
#   pwsh -File ota.ps1 -Target maglock
#   pwsh -File ota.ps1 -Target "maglock,lighting,candles"
#   pwsh -File ota.ps1 -Target all
#
# In multi-target mode:
#   Press Q to stop after the current node finishes.
#
# Safety:
# - Each target is resolved from the deployment map independently.
# - Guard rails prevent Env/Dev mismatches.

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

function Normalize-RequestedTargets {
    param([string]$RawTarget)

    if ([string]::IsNullOrWhiteSpace($RawTarget)) {
        throw "Usage: ota.ps1 -Target <device|device,device|all>"
    }

    $parts = $RawTarget.Split(",", [System.StringSplitOptions]::RemoveEmptyEntries)
    $result = @()

    foreach ($p in $parts) {
        $t = $p.Trim()
        if (-not [string]::IsNullOrWhiteSpace($t)) {
            $result += $t
        }
    }

    if ($result.Count -eq 0) {
        throw "Usage: ota.ps1 -Target <device|device,device|all>"
    }

    return @($result)
}

function Resolve-OtaTargets {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$RequestedTargets
    )

    $requested = @($RequestedTargets)
    if (-not $requested -or $requested.Length -eq 0) {
        throw "Usage: ota.ps1 -Target <device|device|all>"
    }

    if ($requested -contains "all") {
        return @($allTargets)
    }

    $seen = @{}
    $resolved = New-Object System.Collections.Generic.List[string]

    foreach ($t in $requested) {
        if (-not $deployments.ContainsKey($t)) {
            throw "Unknown target '$t'"
        }
        if (-not $seen.ContainsKey($t)) {
            $seen[$t] = $true
            [void]$resolved.Add($t)
        }
    }

    return @($resolved.ToArray())
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

function Assert-TargetMapping {
    param(
        [Parameter(Mandatory=$true)][string]$SingleTarget,
        [Parameter(Mandatory=$true)][string]$ResolvedEnv,
        [Parameter(Mandatory=$true)][string]$ResolvedDev,
        [Parameter(Mandatory=$true)][string]$CmdNode,
        [Parameter(Mandatory=$true)][string]$FirmwareName
    )

    $cfg = $deployments[$SingleTarget]
    if (-not $cfg) {
        throw "Internal error: deployment map missing for '$SingleTarget'"
    }

    if ($ResolvedEnv -ne $cfg.Env) {
        throw "Guard failure for '$SingleTarget': Env mismatch. Expected '$($cfg.Env)', got '$ResolvedEnv'."
    }
    if ($ResolvedDev -ne $cfg.Dev) {
        throw "Guard failure for '$SingleTarget': Dev mismatch. Expected '$($cfg.Dev)', got '$ResolvedDev'."
    }
    if ($CmdNode -ne $cfg.CmdNode) {
        throw "Guard failure for '$SingleTarget': CmdNode mismatch. Expected '$($cfg.CmdNode)', got '$CmdNode'."
    }
    if ($FirmwareName -ne $cfg.FirmwareName) {
        throw "Guard failure for '$SingleTarget': FirmwareName mismatch. Expected '$($cfg.FirmwareName)', got '$FirmwareName'."
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

        if ([string]::IsNullOrWhiteSpace($Env)) {
            $Env = $cfg.Env
        }
        if ([string]::IsNullOrWhiteSpace($Dev)) {
            $Dev = $cfg.Dev
        }

        $cmdNode = $cfg.CmdNode
        $firmwareName = $cfg.FirmwareName

        Assert-TargetMapping -SingleTarget $SingleTarget -ResolvedEnv $Env -ResolvedDev $Dev -CmdNode $cmdNode -FirmwareName $firmwareName

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

        # Laptop/PC -> Pi transport host (works over Tailscale / hotspot)
        $piConnectHost = "100.108.1.80"

        # ESP -> Pi firmware download host (must be reachable by the device on ER LAN)
        $deviceHttpHost = "192.168.0.10"

        $piUser = "rudyy"
        $piFirmwareDir = "/home/$piUser/er1/node_firmware"

        $sshExe = "C:\WINDOWS\System32\OpenSSH\ssh.exe"
        $scpExe = "C:\WINDOWS\System32\OpenSSH\scp.exe"

        $sshCommonArgs = @(
            "-o","BatchMode=yes",
            "-o","StrictHostKeyChecking=no",
            "-o","UserKnownHostsFile=NUL",
            "-o","LogLevel=ERROR",
            "-o","ConnectTimeout=10",
            "-o","ServerAliveInterval=2",
            "-o","ServerAliveCountMax=4"
        )

        $sshExecArgs = @("-n") + $sshCommonArgs

        function Get-FileSha256Hex {
            param([string]$Path)
            (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
        }

        $fwSize = (Get-Item $firmwarePath).Length
        Write-Host ("== Uploading firmware to Pi as {0}  ({1} bytes) ==" -f $firmwareName, $fwSize)

        & $sshExe @sshExecArgs "$piUser@$piConnectHost" "mkdir -p '$piFirmwareDir'"
        $sshExit = $LASTEXITCODE
        if ($sshExit -ne 0) {
            throw "Failed to create firmware directory on Pi at $piFirmwareDir (exit $sshExit)"
        }

        $remotePath = "$piUser@${piConnectHost}:$piFirmwareDir/$firmwareName"
        $scpArgs = @("-B", "-q", "-O", "-C") + $sshCommonArgs + @($firmwarePath, $remotePath)

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        & $scpExe @scpArgs
        $scpExit = $LASTEXITCODE
        $sw.Stop()

        if ($scpExit -ne 0) {
            throw "SCP upload failed (exit $scpExit)"
        }

        Write-Host ("== Upload finished in {0:n2}s ==" -f $sw.Elapsed.TotalSeconds)

        $url = "http://$deviceHttpHost/node_firmware/$firmwareName"
        Write-Host ("== Verifying OTA URL from Pi: {0} ==" -f $url)
        Write-Host "== Verifying HTTP-served firmware matches uploaded file (sha256) from the Pi =="

        $localSha = Get-FileSha256Hex $firmwarePath
        $remoteVerifyCmd = "curl -fsSL '$url' | sha256sum | cut -d ' ' -f1"

        $remoteHttpSha = & $sshExe @sshExecArgs "$piUser@$piConnectHost" $remoteVerifyCmd
        $sshExit = $LASTEXITCODE
        if ($sshExit -ne 0) {
            throw "Remote HTTP verification failed (exit $sshExit)"
        }

        $remoteHttpSha = ($remoteHttpSha | Out-String).Trim().ToLower()
        if ([string]::IsNullOrWhiteSpace($remoteHttpSha)) {
            throw "Remote HTTP verification returned empty SHA256"
        }
        if ($remoteHttpSha -ne $localSha) {
            throw "HTTP firmware mismatch: local=$localSha http=$remoteHttpSha"
        }
        Write-Host "== HTTP firmware matches uploaded file =="

        Write-Host ("== Triggering OTA on {0}/cmd ==" -f $cmdNode)

        $remotePublisher = "/home/$piUser/er1/scripts/ota_publish.py"
        $remoteCmd = @(
            "python3", $remotePublisher,
            "--dev", $Dev,
            "--cmd-node", $cmdNode,
            "--http-host", $deviceHttpHost,
            "--version", $ver,
            "--target", $Dev,
            "--firmware-name", $firmwareName,
            "--verify",
            "--timeout", "10",
            "--up-max", "10"
        ) -join " "

        & $sshExe @sshExecArgs "$piUser@$piConnectHost" $remoteCmd
        $sshExit = $LASTEXITCODE
        if ($sshExit -eq 2) {
            Write-Host ("== SKIPPED: {0} did not come online for OTA verify; likely offline/disconnected ==" -f $SingleTarget) -ForegroundColor Yellow
            exit 20
        }
        if ($sshExit -ne 0) {
            throw "ota_publish.py failed (exit $sshExit)"
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
        }

        Start-Sleep -Milliseconds 200
    }

    if ($p.ExitCode -eq 20) {
        return [pscustomobject]@{
            Target = $SingleTarget
            Status = "Skipped"
            Reason = "offline/disconnected"
        }
    }

    if ($p.ExitCode -ne 0) {
        throw "ota.ps1 failed for $SingleTarget (exit $($p.ExitCode))."
    }

    return [pscustomobject]@{
        Target = $SingleTarget
        Status = "OK"
        Reason = ""
    }
}

function Write-OtaSummary {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$Requested,

        [Parameter(Mandatory=$true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Succeeded,

        [Parameter(Mandatory=$true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Skipped,

        [Parameter(Mandatory=$true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Failed,

        [bool]$StoppedEarly = $false
    )

    Write-Host ""
    Write-Host "================ OTA SUMMARY ================" -ForegroundColor Cyan
    Write-Host ("Requested : {0}" -f ($Requested -join ", "))
    Write-Host ("Succeeded : {0}" -f $(if ($Succeeded.Count -gt 0) { $Succeeded -join ", " } else { "-" })) -ForegroundColor Green
    Write-Host ("Skipped   : {0}" -f $(if ($Skipped.Count -gt 0) { $Skipped -join "; " } else { "-" })) -ForegroundColor Yellow
    Write-Host ("Failed    : {0}" -f $(if ($Failed.Count -gt 0) { $Failed -join "; " } else { "-" })) -ForegroundColor Red
    Write-Host ("Stopped   : {0}" -f $(if ($StoppedEarly) { "yes" } else { "no" }))
    Write-Host "=============================================" -ForegroundColor Cyan
}

$requestedTargets = Normalize-RequestedTargets -RawTarget $Target
$resolvedTargets = @(Resolve-OtaTargets -RequestedTargets $requestedTargets)

if ($resolvedTargets.Count -eq 1) {
    Invoke-OtaSingleTarget -SingleTarget ([string]$resolvedTargets[0]) -Env $Env -Dev $Dev -NoBuild:$NoBuild
    exit 0
}

$script:StopAfterCurrent = $false
$failures = New-Object System.Collections.ArrayList
$skipped = New-Object System.Collections.ArrayList
$succeeded = New-Object System.Collections.ArrayList
$stoppedEarly = $false

Write-Host "Press Q at any time to stop after the current node finishes." -ForegroundColor Yellow

foreach ($singleTarget in $resolvedTargets) {
    Write-Host ""
    Write-Host ("================ OTA {0} ================" -f $singleTarget) -ForegroundColor Cyan

    try {
        $result = Invoke-OtaChildWithSoftStopSupport -SingleTarget ([string]$singleTarget) -NoBuild:$NoBuild

        if ($result -and $result.Status -eq "Skipped") {
            [void]$skipped.Add(("{0}: {1}" -f $singleTarget, $result.Reason))
            Write-Host ("SKIPPED: {0}" -f $singleTarget) -ForegroundColor Yellow
            Write-Host ("couldn't update {0}: {1}" -f $singleTarget, $result.Reason) -ForegroundColor Yellow
        }
        else {
            [void]$succeeded.Add([string]$singleTarget)
        }
    }
    catch {
        [void]$failures.Add(("{0}: {1}" -f $singleTarget, $_.Exception.Message))
        Write-Host ("FAILED: {0}" -f $singleTarget) -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
    }

    if ($script:StopAfterCurrent) {
        $stoppedEarly = $true
        Write-Host ""
        Write-Host "Stopped before starting the next node." -ForegroundColor Yellow
        break
    }
}

Write-OtaSummary -Requested $resolvedTargets -Succeeded $succeeded -Skipped $skipped -Failed $failures -StoppedEarly:$stoppedEarly

if ($failures.Count -gt 0) {
    throw ("OTA failed for {0} target(s): {1}" -f $failures.Count, ($failures -join "; "))
}

Write-Host "== OTA sequence finished =="
exit 0
