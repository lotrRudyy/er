# === ER1 Helper Functions (er1_profile.ps1) ===
Set-StrictMode -Version Latest

# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1RemoteLogDir = "/home/rudyy/er1/logs"

# ---- Repo root detection (PC + Laptop) ----
$pcPath     = "$HOME\Documents\Escape Room\er"
$laptopPath = "$HOME\Documents\er"

if (Test-Path $pcPath) {
    $erRepoRoot = $pcPath
}
elseif (Test-Path $laptopPath) {
    $erRepoRoot = $laptopPath
}
else {
    Write-Error "ER repo not found. Checked: $pcPath and $laptopPath"
    return
}

# Canonical repo root variable for helper functions
$script:ER1_REPO = $erRepoRoot

# ---- Device lists ----
# OTA targets must stay in sync with firmware/ota.ps1 ValidateSet.
$er1OtaTargets = @("maglock","images_piano","chess","knocking","candles","star_sky","star_slider","stop_timer")
$er1LogDevices = @(
    "maglock",
    "maglock_ctrl",
    "images",
    "images_piano",
    "piano",
    "chess",
    "knocking",
    "candles",
    "star_sky",
    "star_slider",
    "stop_timer"
)

# ---- Lock IDs (for lock completion) ----
# Adjust to your real lock IDs; these are NOT necessarily firmware device names.
$er1LockIds = @(
    "images",
    "piano",
    "chess",
    "knocking",
    "candles",
    "star_sky",
    "star_slider",
    "stop_timer"
)

# ---- Command help strings ----
$er1Commands = [ordered]@{
    "help"   = "Show help + examples"
    "pi"     = "SSH into the ER1 Pi"
    "log"    = "Tail logs (today/errors/live), tag markers, or extract slices"
    "logs"   = "Pi-side MQTT view (pretty dashboard)"
    "ota"    = "Upload firmware to a device via ota.ps1"
    "lock"   = "Control locks: er1 lock <id> open|close OR er1 lock all open|close"
    "mqtt"   = "MQTT ops: er1 mqtt status|restart|logs"
    "status" = "One-shot health summary"
    "doctor" = "Collect diagnostic bundle to logs/"
    "reset"  = "Reset riddles (all or one): er1 reset all | er1 reset <riddle>"
    "push"   = "Git add/commit/push from repo root"
    "commit" = "Legacy alias for 'er1 push'"
}

# =========================================================
# LOG PATH HELPERS (FIX: DO NOT FREEZE TODAY AT PROFILE LOAD)
# =========================================================

function Get-Er1TodayLogRemotePath {
    # Returns the log file path for "today" based on the PI's date.
    # We intentionally use PI time/date so PC timezone doesn't matter.
    # IMPORTANT: build command via concatenation so PowerShell never evaluates $(date ...)
    $cmd = 'bash -lc "echo ' + $er1RemoteLogDir + '/er1-$(date +%d.%m.%Y).log"'
    return (ssh $er1Pi $cmd).Trim()
}

function Get-Er1LogRemotePathForDate {
    param(
        [Parameter(Mandatory=$true)]
        [datetime]$Date
    )
    # For local, deterministic "date -> filename" mapping (used by extract).
    return "$er1RemoteLogDir/er1-" + $Date.ToString("dd.MM.yyyy") + ".log"
}

# =========================================================
# CORE HELPERS
# =========================================================

function Invoke-Er1Push {
    param(
        [string]$Message,
        [string]$Tag = "push"
    )

    $prefix       = "[er1 $Tag]"
    $messageToUse = if ([string]::IsNullOrWhiteSpace($Message)) { "update" } else { $Message }

    Push-Location $erRepoRoot
    try {
        $isRepo = git rev-parse --is-inside-work-tree 2>$null
        if ($LASTEXITCODE -ne 0 -or $isRepo.Trim().ToLower() -ne "true") {
            throw "Repo root '$erRepoRoot' is not a git repository."
        }

        $branch = (git rev-parse --abbrev-ref HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Unable to resolve current branch." }

        $originExists = $false
        foreach ($r in (git remote)) {
            if ($r.Trim() -eq "origin") { $originExists = $true; break }
        }

        $upstreamName = git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null
        $hasUpstream = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($upstreamName))

        Write-Host "$prefix Branch: $branch"
        if ($hasUpstream) {
            Write-Host "$prefix Upstream: $($upstreamName.Trim())"
        }
        else {
            Write-Host "$prefix Upstream: (not set)" -ForegroundColor Yellow
            if ($originExists) {
                $targetUpstream = "origin/$branch"
                Write-Host "$prefix Setting upstream -> $targetUpstream"
                git branch --set-upstream-to=$targetUpstream | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Failed to set upstream." }
                $hasUpstream = $true
            }
        }

        git add .
        if ($LASTEXITCODE -ne 0) { throw "git add failed." }

        $changes = git status --porcelain
        if (-not $changes) {
            Write-Host "$prefix No changes to commit." -ForegroundColor Yellow
            return
        }

        git commit -m $messageToUse
        if ($LASTEXITCODE -ne 0) { throw "git commit failed." }

        if (-not $originExists -and -not $hasUpstream) {
            Write-Host "$prefix Changes committed locally; configure 'origin' before pushing." -ForegroundColor Yellow
            return
        }

        git push
        if ($LASTEXITCODE -ne 0) { throw "git push failed (exit $LASTEXITCODE)." }

        Write-Host "$prefix Push complete." -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
}

# =========================================================
# STATUS / DOCTOR / MQTT
# =========================================================

function Invoke-Er1Status {
    $prefix = "[er1 status]"

    Write-Host "=== LOCAL REPO ===" -ForegroundColor Cyan
    Push-Location $erRepoRoot
    try {
        $branch = (git rev-parse --abbrev-ref HEAD 2>$null).Trim()
        $dirty = (git status --porcelain)
        if ($dirty) {
            Write-Host "$prefix Repo: $erRepoRoot  Branch: $branch  State: DIRTY" -ForegroundColor Yellow
        }
        else {
            Write-Host "$prefix Repo: $erRepoRoot  Branch: $branch  State: CLEAN" -ForegroundColor Green
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "`n=== REMOTE CONNECTIVITY ===" -ForegroundColor Cyan
    ssh -o BatchMode=yes -o ConnectTimeout=5 $er1Pi "echo OK" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$prefix SSH: FAIL ($er1Pi)" -ForegroundColor Red
        return
    }
    Write-Host "$prefix SSH: OK ($er1Pi)" -ForegroundColor Green

    Write-Host "`n=== SERVICES ===" -ForegroundColor Cyan
    ssh $er1Pi "systemctl is-active mosquitto.service"
    ssh $er1Pi "systemctl is-active mqtt-log.service"
    ssh $er1Pi "systemctl is-active ota-http.service"

    Write-Host "`n=== MQTT (BROKER) ===" -ForegroundColor Cyan
    ssh $er1Pi "mosquitto_sub -h 127.0.0.1 -t '\$SYS/broker/version' -C 1 2>/dev/null || true"

    $todayFile = Get-Er1TodayLogRemotePath

    Write-Host "`n=== LAST ERR LOGS (today, last 20) ===" -ForegroundColor Cyan
    ssh $er1Pi "grep '""lv"":""ERR""' $todayFile 2>/dev/null | tail -n 20 || true"
}

function Invoke-Er1Doctor {
    $logsDir = Join-Path $erRepoRoot "logs"
    New-Item -ItemType Directory -Force $logsDir | Out-Null

    $out = Join-Path $logsDir ("{0}__doctor.log" -f (Get-Date -Format "yyyy-MM-dd_HH-mm-ss"))

    "=== LOCAL ===" | Out-File $out -Encoding utf8
    Push-Location $erRepoRoot
    try {
        "repo=$erRepoRoot" | Out-File $out -Append
        ("branch={0}" -f (git rev-parse --abbrev-ref HEAD 2>$null)) | Out-File $out -Append
        ("head={0}" -f (git rev-parse HEAD 2>$null)) | Out-File $out -Append
        "status_porcelain:" | Out-File $out -Append
        (git status --porcelain 2>$null) | Out-File $out -Append
    }
    finally {
        Pop-Location
    }

    "=== REMOTE ===" | Out-File $out -Append

    $todayFile = Get-Er1TodayLogRemotePath

    $remoteCmds = @(
        "echo '--- uname ---'; uname -a",
        "echo '--- uptime ---'; uptime",
        "echo '--- ip ---'; hostname -I || true",
        "echo '--- df ---'; df -h",
        "echo '--- svc mosquitto ---'; systemctl --no-pager --full status mosquitto.service || true",
        "echo '--- svc mqtt-log ---'; systemctl --no-pager --full status mqtt-log.service || true",
        "echo '--- svc ota-http ---'; systemctl --no-pager --full status ota-http.service || true",
        "echo '--- journal mosquitto (200) ---'; journalctl -u mosquitto.service -n 200 --no-pager || true",
        "echo '--- journal mqtt-log (200) ---'; journalctl -u mqtt-log.service -n 200 --no-pager || true",
        "echo '--- journal ota-http (200) ---'; journalctl -u ota-http.service -n 200 --no-pager || true",
        "echo '--- today log tail (200) ---'; tail -n 200 $todayFile 2>/dev/null || true",
        "echo '--- today ERR tail (50) ---'; grep '""lv"":""ERR""' $todayFile 2>/dev/null | tail -n 50 || true"
    )

    foreach ($cmd in $remoteCmds) {
        ("`n# $cmd") | Out-File $out -Append
        ssh $er1Pi $cmd | Out-File $out -Append
    }

    Write-Host "Doctor bundle saved to $out" -ForegroundColor Green
}

function Invoke-Er1Mqtt {
    param(
        [ValidateSet("status","restart","logs")]
        [string]$Action
    )

    switch ($Action) {
        "status"  {
            ssh $er1Pi "systemctl is-active mosquitto.service"
            ssh $er1Pi "systemctl is-active mqtt-log.service"
            ssh $er1Pi "mosquitto_sub -h 127.0.0.1 -t '\$SYS/broker/version' -C 1 2>/dev/null || true"
            return
        }
        "restart" {
            ssh -t $er1Pi "sudo systemctl restart mosquitto.service; sudo systemctl restart mqtt-log.service"
            return
        }
        "logs"    {
            ssh $er1Pi "journalctl -u mqtt-log.service -n 200 -f"
            return
        }
    }
}

# =========================================================
# LOG TAGS + EXTRACTION
# =========================================================

function Invoke-Er1LogTag {
    param(
        [string]$TagName
    )

    if ([string]::IsNullOrWhiteSpace($TagName)) {
        throw "Usage: er1 log tag <tag-name>"
    }

    $payloadObj = [ordered]@{
        msg = "LOG_TAG"
        d   = @{ tag = $TagName }
    }
    $payload = $payloadObj | ConvertTo-Json -Compress
    $payloadEscaped = $payload -replace "'", "'`"`'`"`'"

    $topic = "cli/log"
    $cmd = "mosquitto_pub -h 127.0.0.1 -t $topic -m '$payloadEscaped'"
    ssh $er1Pi $cmd
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to publish log tag '$TagName' (exit $LASTEXITCODE)."
    }
    Write-Host "[er1 log tag] Emitted LOG_TAG '$TagName' to $topic" -ForegroundColor Green
}

function Get-Er1LogTagFromLine {
    param(
        [string]$Line
    )

    if (-not $Line) { return $null }
    $idx = $Line.IndexOf("{")
    if ($idx -lt 0) { return $null }
    $json = $Line.Substring($idx)
    try {
        $obj = $json | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
    if ($obj -and $obj.msg -eq "LOG_TAG" -and $obj.d -and $obj.d.tag) {
        return [string]$obj.d.tag
    }
    return $null
}

function Invoke-Er1LogExtract {
    param(
        [string[]]$ExtractArgs
    )

    if (-not $ExtractArgs -or $ExtractArgs.Count -eq 0) {
        throw "Usage: er1 log extract --from <tagA> --to <tagB> [--date YYYY-MM-DD] [--open|--no-open] OR er1 log extract --tag <name> [--date YYYY-MM-DD] [--open|--no-open]"
    }

    $fromTag = $null
    $toTag = $null
    $baseTag = $null
    $dateInput = $null
    $openExplorer = $true

    for ($i = 0; $i -lt $ExtractArgs.Count; $i++) {
        $arg = $ExtractArgs[$i]
        switch ($arg) {
            "--from" {
                if ($i + 1 -ge $ExtractArgs.Count) { throw "Missing value for --from" }
                $fromTag = $ExtractArgs[$i + 1]; $i++; continue
            }
            "--to" {
                if ($i + 1 -ge $ExtractArgs.Count) { throw "Missing value for --to" }
                $toTag = $ExtractArgs[$i + 1]; $i++; continue
            }
            "--tag" {
                if ($i + 1 -ge $ExtractArgs.Count) { throw "Missing value for --tag" }
                $baseTag = $ExtractArgs[$i + 1]
                $fromTag = "$baseTag-start"
                $toTag = "$baseTag-end"
                $i++
                continue
            }
            "--date" {
                if ($i + 1 -ge $ExtractArgs.Count) { throw "Missing value for --date" }
                $dateInput = $ExtractArgs[$i + 1]; $i++; continue
            }
            "--open" {
                $openExplorer = $true
                continue
            }
            "--no-open" {
                $openExplorer = $false
                continue
            }
            default {
                throw "Unknown option '$arg'. Usage: er1 log extract --from <tagA> --to <tagB> [--date YYYY-MM-DD] [--open|--no-open] OR er1 log extract --tag <name> [--date YYYY-MM-DD] [--open|--no-open]"
            }
        }
    }

    if (-not $fromTag -or -not $toTag) {
        throw "Usage: er1 log extract --from <tagA> --to <tagB> [--date YYYY-MM-DD] [--open|--no-open] OR er1 log extract --tag <name> [--date YYYY-MM-DD] [--open|--no-open]"
    }

    $extractLabel = if ($baseTag) { $baseTag } else { "$fromTag-to-$toTag" }
    $safeLabel = $extractLabel
    foreach ($c in [IO.Path]::GetInvalidFileNameChars()) {
        $safeLabel = $safeLabel -replace ([Regex]::Escape($c)), "_"
    }

    $date = $null
    if ($dateInput) {
        try {
            $date = [datetime]::ParseExact($dateInput, "yyyy-MM-dd", $null)
        } catch {
            throw "Invalid date. Use YYYY-MM-DD."
        }
    } else {
        $date = Get-Date
    }

    $dateHyphen = $date.ToString("yyyy-MM-dd")
    $dateDots = $date.ToString("dd.MM.yyyy")
    $remoteCandidates = @(
        "$er1RemoteLogDir/er1-$dateHyphen.log",
        "$er1RemoteLogDir/er1-$dateDots.log"
    )

    $remoteFile = $null
    foreach ($candidate in $remoteCandidates) {
        ssh $er1Pi "test -f $candidate"
        if ($LASTEXITCODE -eq 0) {
            $remoteFile = $candidate
            break
        }
    }

    if (-not $remoteFile) {
        throw "Remote log file not found for $dateHyphen (tried $($remoteCandidates -join ', '))."
    }

    $localLogDir = Join-Path $erRepoRoot "er1\data\logs"
    New-Item -ItemType Directory -Force $localLogDir | Out-Null

    $localRaw = Join-Path $localLogDir ("er1-raw-$($date.ToString('yyyyMMdd')).log")
    scp "${er1Pi}:$remoteFile" $localRaw
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy $remoteFile from Pi (exit $LASTEXITCODE)."
    }

    $lines = Get-Content $localRaw
    if (-not $lines -or $lines.Count -eq 0) {
        Write-Error "[er1 log extract] Remote log file was empty: $remoteFile"
        return
    }

    $startIdx = $null
    $endIdx = $null
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $tag = Get-Er1LogTagFromLine -Line $lines[$i]
        if (-not $tag) { continue }

        if ($null -eq $startIdx -and $tag -eq $fromTag) {
            $startIdx = $i
            continue
        }

        if ($null -ne $startIdx -and $tag -eq $toTag) {
            $endIdx = $i
            break
        }
    }

    if ($null -eq $startIdx) {
        Write-Error "[er1 log extract] Tag '$fromTag' not found in $remoteFile."
        return
    }
    if ($null -eq $endIdx -or $endIdx -lt $startIdx) {
        Write-Error "[er1 log extract] Tag '$toTag' not found after '$fromTag' in $remoteFile."
        return
    }

    $slice = $lines[$startIdx..$endIdx]
    if (-not $slice -or $slice.Count -eq 0) {
        Write-Error "[er1 log extract] Extracted slice is empty; no file created."
        return
    }

    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    $outPath = Join-Path $localLogDir ("extract_{0}_{1}.log" -f $safeLabel, $ts)
    $slice | Set-Content -Path $outPath -Encoding utf8

    Write-Host "[er1 log extract] Saved slice -> $outPath" -ForegroundColor Green
    if ($openExplorer) {
        Invoke-Item -Path (Split-Path $outPath -Parent)
    }
}

# =========================================================
# MAIN DISPATCHER
# =========================================================

function Get-Er1RiddleResetMap {
    # Key = riddle name you type after `er1 reset <name>`
    # Value = @{ dev = "<mqtt device>"; cmd = "<command>" }
    return @{
        "images"      = @{ dev = "images_piano"; cmd = "RESET_IMAGES" }
        "piano"       = @{ dev = "images_piano"; cmd = "RESET_PIANO" }
        "chess"       = @{ dev = "chess";        cmd = "RESET_CHESS" }
        "knocking"    = @{ dev = "knocking";     cmd = "RESET_KNOCKING" }
        "candles"     = @{ dev = "candles";      cmd = "RESET_CANDLES" }
        "star_sky"    = @{ dev = "star_sky";     cmd = "RESET_STAR_SKY" }
        "starsky"     = @{ dev = "star_sky";     cmd = "RESET_STAR_SKY" }
        "star_slider" = @{ dev = "star_slider";  cmd = "RESET_STAR_SLIDER" }
        "starslider"  = @{ dev = "star_slider";  cmd = "RESET_STAR_SLIDER" }
        "stop_timer"  = @{ dev = "stop_timer";   cmd = "RESET_STOP_TIMER" }
        "stoptimer"   = @{ dev = "stop_timer";   cmd = "RESET_STOP_TIMER" }
    }
}

function Invoke-Er1ResetRiddle {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Name
    )

    $nameKey = $Name.ToLowerInvariant()
    $map = Get-Er1RiddleResetMap

    if (-not $map.ContainsKey($nameKey)) {
        $valid = ($map.Keys | Sort-Object) -join ", "
        throw "Unknown riddle '$Name'. Valid: $valid"
    }

    $a = $map[$nameKey]
    $topic = "$($a.dev)/cmd"
    $msg   = $a.cmd

    $prefix = "[er1 reset $nameKey]"
    Write-Host "$prefix -> $topic  $msg" -ForegroundColor Cyan

    ssh $er1Pi "mosquitto_pub -h 127.0.0.1 -t '$topic' -m '$msg'"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to publish $msg to $topic (exit $LASTEXITCODE)."
    }

    Write-Host "$prefix done." -ForegroundColor Green
}

function Invoke-Er1ResetRiddles {
    $prefix = "[er1 reset all]"

    # Order matters; keep it consistent and simple.
    $actions = @(
        @{ dev = "images_piano"; cmd = "RESET_IMAGES" },
        @{ dev = "images_piano"; cmd = "RESET_PIANO"  },
        @{ dev = "chess";        cmd = "RESET_CHESS"  },
        @{ dev = "knocking";     cmd = "RESET_KNOCKING" },
        @{ dev = "candles";      cmd = "RESET_CANDLES" },
        @{ dev = "star_sky";     cmd = "RESET_STAR_SKY" },
        @{ dev = "star_slider";  cmd = "RESET_STAR_SLIDER" },
        @{ dev = "stop_timer";   cmd = "RESET_STOP_TIMER" }
    )

    foreach ($a in $actions) {
        $topic = "$($a.dev)/cmd"
        $msg   = $a.cmd
        Write-Host "$prefix -> $topic  $msg" -ForegroundColor Cyan
        ssh $er1Pi "mosquitto_pub -h 127.0.0.1 -t '$topic' -m '$msg'"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to publish $msg to $topic (exit $LASTEXITCODE)."
        }
        Start-Sleep -Milliseconds 120
    }

    # Also lock R2 + R3 (these are fail-open / need explicit close on reset)
    foreach ($lockId in @("r2", "r3")) {
        $lockTopic = "maglock/lock/$lockId/cmd"
        Write-Host "$prefix -> $lockTopic  CLOSE" -ForegroundColor Cyan
        ssh $er1Pi "mosquitto_pub -h 127.0.0.1 -t '$lockTopic' -m 'CLOSE'"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to publish CLOSE to $lockTopic (exit $LASTEXITCODE)."
        }
        Start-Sleep -Milliseconds 120
    }

    Write-Host "$prefix done." -ForegroundColor Green
}

function er1 {
    param(
        [Parameter(Position=0)]
        [string]$cmd,

        [Parameter(Position=1, ValueFromRemainingArguments=$true)]
        [string[]]$cmdArgs,

        # Log-only switches
        [switch]$live,
        [switch]$errors,
        [int]$n = 200
    )

    switch ($cmd) {

        "help" {
            Write-Host "`nER1 helper – commands:`n" -ForegroundColor Cyan
            foreach ($k in $er1Commands.Keys) {
                "{0,-10} {1}" -f $k, $er1Commands[$k]
            }

            Write-Host "`nLock examples:" -ForegroundColor Cyan
            Write-Host "  er1 lock images open"
            Write-Host "  er1 lock images close"
            Write-Host "  er1 lock all open"
            Write-Host "  er1 lock all close"

            Write-Host "`nOTA examples:" -ForegroundColor Cyan
            Write-Host "  er1 ota images_piano"

            Write-Host "`nStatus/Doctor examples:" -ForegroundColor Cyan
            Write-Host "  er1 status"
            Write-Host "  er1 doctor"

            Write-Host "`nReset examples:" -ForegroundColor Cyan
            Write-Host "  er1 reset all"

            Write-Host "`nMQTT examples:" -ForegroundColor Cyan
            Write-Host "  er1 mqtt status"
            Write-Host "  er1 mqtt restart"
            Write-Host "  er1 mqtt logs"

            Write-Host "`nLog examples:" -ForegroundColor Cyan
            Write-Host "  er1 log"
            Write-Host "  er1 log images"
            Write-Host "  er1 log piano"
            Write-Host "  er1 log images 50"
            Write-Host "  er1 log -live"
            Write-Host "  er1 log -errors"
            Write-Host "  er1 log images --save"
            Write-Host "  er1 log tag game-start"
            Write-Host "  er1 log extract --tag game"
            Write-Host "  er1 log extract --from game-start --to game-end --date 2025-12-14 --no-open"
            Write-Host "  er1 logs pretty"
            Write-Host ""
            return
        }

        "pi" {
            ssh $er1Pi
            return
        }

        "status" {
            Invoke-Er1Status
            return
        }

        "doctor" {
            Invoke-Er1Doctor
            return
        }

        "reset" {
            if (-not $cmdArgs -or $cmdArgs.Count -lt 1) {
                throw "Usage: er1 reset all | er1 reset <riddle> (e.g. er1 reset chess)"
            }

            $target = $cmdArgs[0].ToLowerInvariant()
            if ($target -eq "all" -or $target -eq "riddles") {
                Invoke-Er1ResetRiddles
                return
            }

            Invoke-Er1ResetRiddle -Name $target
            return
        }

        "mqtt" {
            if (-not $cmdArgs -or $cmdArgs.Count -lt 1) { throw "Usage: er1 mqtt status|restart|logs" }
            Invoke-Er1Mqtt -Action $cmdArgs[0]
            return
        }

        "ota" {
            $target = if ($cmdArgs -and $cmdArgs.Count -ge 1) { $cmdArgs[0] } else { $null }
            if (-not $target) { throw "Usage: er1 ota <device>" }

            $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"

            # Run ota.ps1 once, CAPTURE output so we can parse version/build + print it.
            $otaOut = & pwsh -File $otaScript -Target $target 2>&1
            $otaText = ($otaOut | Out-String)
            Write-Host $otaText

            if ($LASTEXITCODE -ne 0) { throw "ota.ps1 failed (exit $LASTEXITCODE)." }

            # Parse from ota.ps1 output:
            # Firmware : /home/rudyy/er1/node_firmware/<target>.bin
            # Version  : <ver>
            # Build    : <build>
            $fwPath = ([regex]::Match($otaText, '^\s*Firmware\s*:\s*(.+?)\s*$', 'Multiline')).Groups[1].Value.Trim()
            $verStr = ([regex]::Match($otaText, '^\s*Version\s*:\s*(.+?)\s*$', 'Multiline')).Groups[1].Value.Trim()
            $bldStr = ([regex]::Match($otaText, '^\s*Build\s*:\s*(.+?)\s*$', 'Multiline')).Groups[1].Value.Trim()

            if (-not $fwPath) { throw "Verifier: could not parse firmware path from ota.ps1 output." }
            if (-not $verStr) { throw "Verifier: could not parse Version from ota.ps1 output." }
            if (-not $bldStr) { throw "Verifier: could not parse Build from ota.ps1 output." }

	            # NOTE (Option A): ota.ps1 is the single source of truth for triggering OTA.
	            # Do NOT publish a second UPDATE command here — it causes duplicate / invalid_sha256 failures.

            # PC-side verify (fast): spam "SEND HB" while subscribing so we don't wait for the periodic heartbeat.
            # We subscribe to both hb + ota so we can fail fast on OTA_FAIL.
            $hbTimeoutSec = 10
            Write-Host "== Verifier(PC): expect build=$bldStr; watching '$target/hb' + '$target/ota' (up to ${hbTimeoutSec}s) =="

            $cmdTopic = "$target/cmd"
            $spam = "for i in \$(seq 1 40); do mosquitto_pub -h 127.0.0.1 -t '$cmdTopic' -m 'SEND HB' >/dev/null 2>&1; sleep 0.25; done"
            $subCmd = "bash -lc '$spam & timeout ${hbTimeoutSec}s mosquitto_sub -h 127.0.0.1 -v -t '$target/hb' -t '$target/ota' 2>/dev/null || true'"
            $lines = ssh $er1Pi $subCmd

            $rebooted = $false
            $prevUp = $null
            $lastSeen = $null
            $ok = $false

            foreach ($line in $lines) {
                if (-not $line) { continue }
                $lastSeen = $line

                $topic = $null
                $payload = $line
                if ($line -match '^([^\s]+)\s+(.*)$') {
                    $topic = $Matches[1]
                    $payload = $Matches[2]
                }

                if ($topic -and $topic.EndsWith('/ota')) {
                    try { $ota = $payload | ConvertFrom-Json } catch { continue }
                    $st = [string]$ota.st
                    if ($st -eq 'OTA_FAIL') {
                        $reason = "OTA_FAIL"
                        if ($ota.d -and $ota.d.reason) { $reason = "OTA_FAIL $($ota.d.reason)" }
                        throw "== OTA VERIFY: FAIL ($reason) =="
                    }
                    continue
                }

                # hb topic
                if ($payload.ToLowerInvariant() -eq "offline") {
                    $rebooted = $true
                    continue
                }

                try { $hb = $payload | ConvertFrom-Json } catch { continue }
                $upVal = $hb.up
                try {
                    $upInt = [int]$upVal
                    if ($null -ne $prevUp -and ($upInt + 5) -lt $prevUp) {
                        $rebooted = $true
                    }
                    $prevUp = $upInt
                } catch {
                    # ignore
                }

                $hbBuild = [string]$hb.build
                if ($rebooted -and $hbBuild -and $hbBuild -eq $bldStr) {
                    $ok = $true
                    break
                }
            }

            if ($ok) {
                Write-Host "== OTA VERIFY: OK (rebooted + hb.build matches) ==" -ForegroundColor Green
                return
            }

            $reason = if (-not $lastSeen) { "no hb received" } elseif (-not $rebooted) { "no reboot detected" } else { "hb.build didn't match" }
            throw "== OTA VERIFY: FAIL ($reason) within ${hbTimeoutSec}s =="
        }

        "lock" {
            if (-not $cmdArgs -or $cmdArgs.Count -lt 2) {
                throw "Usage: er1 lock <id> open|close OR er1 lock all open|close"
            }

            $toAction = {
                param([string]$a)
                if ($a -eq "open") { return "OPEN" }
                if ($a -eq "close") { return "CLOSE" }
                throw "Usage: er1 lock <id> open|close OR er1 lock all open|close"
            }

            if ($cmdArgs[0] -eq "all") {
                $action = & $toAction $cmdArgs[1]
                foreach ($id in $er1LockIds) {
                    ssh $er1Pi "mosquitto_pub -h 127.0.0.1 -t 'maglock/lock/$id/cmd' -m '$action'"
                }
                return
            }

            $id = $cmdArgs[0]
            $action2 = & $toAction $cmdArgs[1]
            ssh $er1Pi "mosquitto_pub -h 127.0.0.1 -t 'maglock/lock/$id/cmd' -m '$action2'"
            return
        }

        "log" {
            # Subcommands first
            if ($cmdArgs -and $cmdArgs.Count -gt 0) {
                $sub = $cmdArgs[0].ToLowerInvariant()
                if ($sub -eq "tag") {
                    if ($cmdArgs.Count -lt 2) { throw "Usage: er1 log tag <tag-name>" }
                    $tagValue = ($cmdArgs | Select-Object -Skip 1) -join " "
                    Invoke-Er1LogTag -TagName $tagValue
                    return
                }
                elseif ($sub -eq "extract") {
                    $extractArgsLocal = @()
                    if ($cmdArgs.Count -gt 1) { $extractArgsLocal = $cmdArgs[1..($cmdArgs.Count - 1)] }
                    Invoke-Er1LogExtract -ExtractArgs $extractArgsLocal
                    return
                }
                elseif ($sub -eq "hb") {
                    # Heartbeat dashboard (Pi-side)
                    ssh -t $er1Pi "cd ~/er1 && python3 ./scripts/hb_pretty.py"
                    return
                }
                elseif ($sub -eq "pretty") {
                    # Consolidated pretty view (Pi-side)
                    ssh -t $er1Pi "cd ~/er1 && python3 ./scripts/all_logs_pretty.py"
                    return
                }

            }

            $argsNoSave = @()
            $saveRequested = $false

            if ($cmdArgs) {
                foreach ($a in $cmdArgs) {
                    if ($a -and $a.ToLowerInvariant() -eq "--save") { $saveRequested = $true }
                    else { $argsNoSave += $a }
                }
            }

            $patterns = @()
            $localN   = $n

            if ($argsNoSave.Count -gt 0) {
                $last = $argsNoSave[-1]
                $intRef = 0
                if ([int]::TryParse($last, [ref]$intRef)) {
                    $localN = $intRef
                    if ($argsNoSave.Count -gt 1) { $patterns = $argsNoSave[0..($argsNoSave.Count - 2)] }
                } else {
                    $patterns = $argsNoSave
                }
            }

            if ($patterns.Count -eq 0) { $patterns = @("*") }
            $useAll = ($patterns.Count -eq 1 -and $patterns[0] -eq "*")
            $regex  = if ($useAll) { $null } else { ($patterns -join "|") }

            # ALWAYS resolve today file at runtime (using PI date)
            $todayFile = Get-Er1TodayLogRemotePath

            $localSaveDir = Join-Path $erRepoRoot "er1\data\logs"
            if ($saveRequested) {
                New-Item -ItemType Directory -Force $localSaveDir | Out-Null
            }

            if ($live) {
                # Remote loop that switches log file at midnight (PI date), without restarting local terminal.
                $regexEsc = $null
                if (-not $useAll -and $regex) {
                    # Escape for embedding in bash double quotes
                    $regexEsc = $regex -replace '"', '\"'
                }

                if ($useAll) {
                    $tmpl = @'
bash -lc '
cur=""
curDay=""
while true; do
  day=$(date +%d.%m.%Y)
  file="__LOGDIR__/er1-$day.log"

  if [ "$day" != "$curDay" ]; then
    curDay="$day"
    echo "=== [er1 log] following $file ==="
    if [ -n "$cur" ]; then kill "$cur" 2>/dev/null || true; fi
    tail -n 0 -f "$file" &
    cur=$!
  fi

  sleep 1
done
'
'@
                    $remoteFollow = $tmpl.Replace("__LOGDIR__", $er1RemoteLogDir)
                } else {
                    $tmpl = @'
bash -lc '
FILTER="__FILTER__"

cur=""
curDay=""
while true; do
  day=$(date +%d.%m.%Y)
  file="__LOGDIR__/er1-$day.log"

  if [ "$day" != "$curDay" ]; then
    curDay="$day"
    echo "=== [er1 log] following $file (filter) ==="
    if [ -n "$cur" ]; then kill "$cur" 2>/dev/null || true; fi
    tail -n 0 -f "$file" | grep -E "$FILTER" &
    cur=$!
  fi

  sleep 1
done
'
'@
                    $remoteFollow = $tmpl.Replace("__LOGDIR__", $er1RemoteLogDir).Replace("__FILTER__", $regexEsc)
                }

                ssh -t $er1Pi $remoteFollow
                return
            }

            $remoteCmd = $null

            if ($errors) {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; grep '""lv"":""ERR""' $todayFile | tail -n $localN"
                } else {
                    # Escape regex for single-quoted grep -E
                    $regexEsc2 = $regex -replace "'", "'\''"
                    $remoteCmd = "cd ~/er1; grep -E '$regexEsc2' $todayFile | grep '""lv"":""ERR""' | tail -n $localN"
                }
            } else {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; tail -n $localN $todayFile"
                } else {
                    $regexEsc2 = $regex -replace "'", "'\''"
                    $remoteCmd = "cd ~/er1; grep -E '$regexEsc2' $todayFile | tail -n $localN"
                }
            }

            if (-not $saveRequested) {
                ssh $er1Pi $remoteCmd
                return
            }

            $outLines = ssh $er1Pi $remoteCmd
            $ts = Get-Date -Format "yyyyMMdd-HHmmss"
            $label = if ($useAll) { "all" } else { ($patterns -join "_") }
            foreach ($c in [IO.Path]::GetInvalidFileNameChars()) { $label = $label -replace ([Regex]::Escape($c)), "_" }
            $outPath = Join-Path $localSaveDir ("log_{0}_{1}.txt" -f $label, $ts)
            $outLines | Set-Content -Path $outPath -Encoding utf8
            Write-Host "[er1 log] Saved -> $outPath" -ForegroundColor Green
            return
        }

        "logs" {
            $sub = if ($cmdArgs -and $cmdArgs.Count -gt 0) { $cmdArgs[0].ToLowerInvariant() } else { "pretty" }
            switch ($sub) {
                "pretty" {
                    ssh -t $er1Pi "cd ~/er1 && ./scripts/mqtt_logs.sh pretty"
                    return
                }
                default {
                    throw "Usage: er1 logs pretty"
                }
            }
        }

        "push" {
            $Message = if ($cmdArgs -and $cmdArgs.Count -gt 0) { $cmdArgs -join " " } else { $null }
            Invoke-Er1Push -Message $Message -Tag "push"
            return
        }

        "commit" {
            $Message = if ($cmdArgs -and $cmdArgs.Count -gt 0) { $cmdArgs -join " " } else { $null }
            Write-Host "[er1 commit] Alias for 'er1 push'. Prefer 'er1 push' going forward." -ForegroundColor Yellow
            Invoke-Er1Push -Message $Message -Tag "commit"
            return
        }

        default {
            Write-Error "Unknown command. Use: er1 help"
            return
        }
    }
}

# =========================================================
# AUTOCOMPLETION
# =========================================================

# ---- Autocomplete for first argument (command) ----
Register-ArgumentCompleter -CommandName er1 -ParameterName cmd -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete)
    foreach ($k in $er1Commands.Keys) {
        if ($k -like "$wordToComplete*") {
            [System.Management.Automation.CompletionResult]::new($k, $k, 'ParameterValue', $er1Commands[$k])
        }
    }
}

# ---- Autocomplete for mqtt action: status|restart|logs ----
Register-ArgumentCompleter -CommandName er1 -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst)
    $tokens = $commandAst.CommandElements
    if ($tokens.Count -lt 2) { return }
    if ($tokens[1].Value -ne "mqtt") { return }
    if ($tokens.Count -eq 3) {
        @("status","restart","logs") |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
    }
}

# ---- Autocomplete for devices (2nd arg when cmd=log/ota) ----
Register-ArgumentCompleter -CommandName er1 -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst)
    $tokens = $commandAst.CommandElements
    if ($tokens.Count -lt 2) { return }
    $sub = $tokens[1].Value
    if ($sub -eq "log") {
        (@("*") + $er1LogDevices) |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
    }
    elseif ($sub -eq "ota") {
        $er1OtaTargets |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
    }
}

# ---- Autocomplete for lock: <id|all> and open|close ----
Register-ArgumentCompleter -CommandName er1 -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst)
    $tokens = $commandAst.CommandElements
    if ($tokens.Count -lt 2) { return }
    if ($tokens[1].Value -ne "lock") { return }

    # tokens: 0=er1 1=lock 2=<id|all> 3=<open|close>
    if ($tokens.Count -eq 3) {
        (@("all") + $er1LockIds) |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
        return
    }

    if ($tokens.Count -eq 4) {
        @("open","close") |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
        return
    }
}
