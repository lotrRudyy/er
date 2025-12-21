# === ER1 Helper Functions (er1_profile.ps1) ===
Set-StrictMode -Version Latest

# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1RemoteLogDir = "/home/rudyy/er1/logs"
$er1TodayLog = "$er1RemoteLogDir/er1-" + (Get-Date -Format "dd.MM.yyyy") + ".log"

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
    "ota"    = "Upload firmware to a device via ota.ps1"
    "lock"   = "Control locks: er1 lock <id> open|close OR er1 lock all open|close"
    "mqtt"   = "MQTT ops: er1 mqtt status|restart|logs"
    "status" = "One-shot health summary"
    "doctor" = "Collect diagnostic bundle to logs/"
    "push"   = "Git add/commit/push from repo root"
    "commit" = "Legacy alias for 'er1 push'"
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
    ssh $er1Pi "systemctl is-active ota-verify.service"

    Write-Host "`n=== MQTT (BROKER) ===" -ForegroundColor Cyan
    ssh $er1Pi "mosquitto_sub -h 127.0.0.1 -t '\$SYS/broker/version' -C 1 2>/dev/null || true"

    Write-Host "`n=== LAST ERR LOGS (today, last 20) ===" -ForegroundColor Cyan
    ssh $er1Pi "grep '""lv"":""ERR""' $er1TodayLog 2>/dev/null | tail -n 20 || true"
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
    $remoteCmds = @(
        "echo '--- uname ---'; uname -a",
        "echo '--- uptime ---'; uptime",
        "echo '--- ip ---'; hostname -I || true",
        "echo '--- df ---'; df -h",
        "echo '--- svc mosquitto ---'; systemctl --no-pager --full status mosquitto.service || true",
        "echo '--- svc mqtt-log ---'; systemctl --no-pager --full status mqtt-log.service || true",
        "echo '--- svc ota-http ---'; systemctl --no-pager --full status ota-http.service || true",
        "echo '--- svc ota-verify ---'; systemctl --no-pager --full status ota-verify.service || true",
        "echo '--- journal mosquitto (200) ---'; journalctl -u mosquitto.service -n 200 --no-pager || true",
        "echo '--- journal mqtt-log (200) ---'; journalctl -u mqtt-log.service -n 200 --no-pager || true",
        "echo '--- journal ota-http (200) ---'; journalctl -u ota-http.service -n 200 --no-pager || true",
        "echo '--- journal ota-verify (200) ---'; journalctl -u ota-verify.service -n 200 --no-pager || true",
        "echo '--- today log tail (200) ---'; tail -n 200 $er1TodayLog 2>/dev/null || true",
        "echo '--- today ERR tail (50) ---'; grep '""lv"":""ERR""' $er1TodayLog 2>/dev/null | tail -n 50 || true"
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

        "mqtt" {
            if (-not $cmdArgs -or $cmdArgs.Count -lt 1) { throw "Usage: er1 mqtt status|restart|logs" }
            Invoke-Er1Mqtt -Action $cmdArgs[0]
            return
        }

        "ota" {
            $target = if ($cmdArgs -and $cmdArgs.Count -ge 1) { $cmdArgs[0] } else { $null }
            if (-not $target) { throw "Usage: er1 ota <device>" }
            $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"
            pwsh -File $otaScript -Target $target
            return
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
            # Minimal (fast) implementation: today/errors/live + simple regex filter + optional --save.
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

            $todayFile = $er1TodayLog

            $localSaveDir = Join-Path $erRepoRoot "er1\data\logs"
            if ($saveRequested) {
                New-Item -ItemType Directory -Force $localSaveDir | Out-Null
            }

            if ($live) {
                if ($useAll) {
                    ssh -t $er1Pi "tail -f $todayFile"
                } else {
                    ssh -t $er1Pi "tail -f $todayFile | grep -E '$regex'"
                }
                return
            }

            $remoteCmd = $null

            if ($errors) {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; grep '""lv"":""ERR""' $todayFile | tail -n $localN"
                } else {
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | grep '""lv"":""ERR""' | tail -n $localN"
                }
            } else {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; tail -n $localN $todayFile"
                } else {
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | tail -n $localN"
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
