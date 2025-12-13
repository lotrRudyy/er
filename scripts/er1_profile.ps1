# === ER1 Helper Functions (er1_profile.ps1) ===
Set-StrictMode -Version Latest

# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1Cmd = "/home/rudyy/er1/er1"

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

# ---- Device list (for log/ota completion) ----
$er1Devices = @(
    "maglock_ctrl",
    "images",
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
    "log"    = "Tail logs (today/errors/live), optional --save"
    "ota"    = "Upload firmware to a device via ota.ps1"
    "deploy" = "Deploy ER1 Pi runtime (supports -Mode/-DryRun/-RestartServices/-Verify)"
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

function Invoke-Er1Sync {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Dest,
        [string[]]$ExtraArgs,
        [switch]$DryRun,
        [string]$Prefix = "[er1 deploy]"
    )

    $rsyncCmd = Get-Command rsync -ErrorAction SilentlyContinue

    if ($rsyncCmd) {
        $rsyncArgs = @("-avz", "--delete")
        if ($DryRun) { $rsyncArgs += @("--dry-run", "--itemize-changes") }
        $rsyncArgs += ($ExtraArgs | Where-Object { $_ })
        $rsyncArgs += @($Source, $Dest)

        Write-Host "$Prefix rsync $($rsyncArgs -join ' ')"
        & rsync @rsyncArgs
        if ($LASTEXITCODE -ne 0) {
            throw "rsync failed with exit code $LASTEXITCODE"
        }
        return
    }

    # No rsync
    if ($DryRun) {
        throw "DryRun requested but rsync not installed. Install rsync or run without -DryRun."
    }

    Write-Host "$Prefix rsync not found, falling back to scp -r (no delete semantics)" -ForegroundColor Yellow
    Write-Host "$Prefix scp -r `"$Source`" `"$Dest`""
    & scp -r "$Source" "$Dest"
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Er1Deploy {
    param(
        [ValidateSet("runtime", "full")]
        [string]$Mode = "runtime",

        [switch]$DryRun,
        [switch]$RestartServices,
        [switch]$Verify
    )

    $prefix     = "[er1 deploy]"
    $remoteRoot = Split-Path -Parent $er1Cmd
    if (-not $remoteRoot -or $remoteRoot.Trim() -eq "" -or $remoteRoot -eq "/") {
        throw "Refusing deploy: remoteRoot invalid ($remoteRoot)"
    }

    $envRoot     = Join-Path $erRepoRoot "er1"
    $runtimeRoot = Join-Path $envRoot "pi-runtime"

    if (-not (Test-Path $runtimeRoot)) {
        throw "Pi runtime folder not found: $runtimeRoot"
    }

    Write-Host "$prefix Target Pi:   $er1Pi"
    Write-Host "$prefix Remote root: $remoteRoot"
    Write-Host "$prefix Local root:  $runtimeRoot"
    Write-Host "$prefix Mode:        $Mode"
    Write-Host "$prefix Meaning:"
    Write-Host "  runtime = scripts/config/systemd/docs (+ wrapper if present)"
    Write-Host "  full    = entire pi-runtime folder (still not whole repo)"

    ssh $er1Pi "mkdir -p '$remoteRoot'"
    if ($LASTEXITCODE -ne 0) { throw "Unable to ensure remote path (exit $LASTEXITCODE)." }

    if ($Mode -eq "full") {
        Invoke-Er1Sync `
            -Source ("{0}/" -f $runtimeRoot) `
            -Dest ("{0}:{1}/" -f $er1Pi, $remoteRoot) `
            -ExtraArgs @("--exclude=.pio", "--exclude=.sconsign*", "--exclude=.vscode") `
            -DryRun:$DryRun `
            -Prefix $prefix
    }
    else {
        # Wrapper CLI (if present)
        $cliWrapper = Join-Path $envRoot "er1"
        if (Test-Path $cliWrapper) {
            Invoke-Er1Sync `
                -Source $cliWrapper `
                -Dest ("{0}:{1}/" -f $er1Pi, $remoteRoot) `
                -ExtraArgs @() `
                -DryRun:$DryRun `
                -Prefix $prefix
        }

        # Runtime folders
        $runtimeItems = @(
            @{ Path = (Join-Path $runtimeRoot "scripts"); Remote = "/scripts/"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "config");  Remote = "/config/";  Extra = @("--exclude=local.env") },
            @{ Path = (Join-Path $runtimeRoot "systemd"); Remote = "/systemd/"; Extra = @() },
            @{ Path = (Join-Path $runtimeRoot "docs");    Remote = "/docs/";    Extra = @() }
        )

        foreach ($item in $runtimeItems) {
            if (Test-Path $item.Path) {
                Invoke-Er1Sync `
                    -Source ("{0}/" -f $item.Path) `
                    -Dest ("{0}:{1}{2}" -f $er1Pi, $remoteRoot, $item.Remote) `
                    -ExtraArgs $item.Extra `
                    -DryRun:$DryRun `
                    -Prefix $prefix
            }
        }
    }

    # Best-effort permissions refresh
    ssh $er1Pi @"
set -e
cd '$remoteRoot'
chmod +x ./er1 2>/dev/null || true
if [ -d scripts ]; then
  chmod +x scripts/*.sh 2>/dev/null || true
  chmod +x scripts/ota 2>/dev/null || true
fi
"@
    if ($LASTEXITCODE -ne 0) { throw "Unable to refresh remote permissions (exit $LASTEXITCODE)." }

    if ($RestartServices) {
        Write-Host "$prefix Restarting services..." -ForegroundColor Yellow
        ssh $er1Pi @"
sudo systemctl daemon-reload
sudo systemctl restart er1-mqtt-log.service
sudo systemctl restart er1-ota-verify.service
"@
        if ($LASTEXITCODE -ne 0) { throw "Service restart failed (exit $LASTEXITCODE)." }
    }

    if ($Verify) {
        Write-Host "$prefix Verifying..." -ForegroundColor Yellow

        ssh $er1Pi "systemctl is-active er1-mqtt-log.service"
        if ($LASTEXITCODE -ne 0) { throw "er1-mqtt-log.service not active" }

        ssh $er1Pi "systemctl is-active er1-ota-verify.service"
        if ($LASTEXITCODE -ne 0) { throw "er1-ota-verify.service not active" }

        ssh $er1Pi "$er1Cmd status_mqtt"
        if ($LASTEXITCODE -ne 0) { throw "status_mqtt failed" }
    }

    Write-Host "$prefix Deploy complete." -ForegroundColor Green
}

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
# LOGGING (remote tail helpers)
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
    ssh $er1Pi "systemctl is-active er1-mqtt-log.service"
    ssh $er1Pi "systemctl is-active er1-ota-verify.service"

    Write-Host "`n=== MQTT ===" -ForegroundColor Cyan
    ssh $er1Pi "$er1Cmd status_mqtt"

    Write-Host "`n=== LAST ERR LOGS (today, last 20) ===" -ForegroundColor Cyan
    ssh $er1Pi "grep '""lv"":""ERR""' ~/er1/logs/er1-\$(date +%d.%m.%Y).log 2>/dev/null | tail -n 20 || true"
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
        "echo '--- svc er1-mqtt-log ---'; systemctl --no-pager --full status er1-mqtt-log.service || true",
        "echo '--- svc er1-ota-verify ---'; systemctl --no-pager --full status er1-ota-verify.service || true",
        "echo '--- journal mqtt-log (200) ---'; journalctl -u er1-mqtt-log.service -n 200 --no-pager || true",
        "echo '--- journal ota-verify (200) ---'; journalctl -u er1-ota-verify.service -n 200 --no-pager || true",
        "echo '--- status_mqtt ---'; $er1Cmd status_mqtt || true",
        "echo '--- today log tail (200) ---'; tail -n 200 ~/er1/logs/er1-\$(date +%d.%m.%Y).log 2>/dev/null || true",
        "echo '--- today ERR tail (50) ---'; grep '""lv"":""ERR""' ~/er1/logs/er1-\$(date +%d.%m.%Y).log 2>/dev/null | tail -n 50 || true"
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
        "status"  { ssh $er1Pi "$er1Cmd status_mqtt"; return }
        "restart" { ssh $er1Pi "$er1Cmd restart_mqtt"; return }
        "logs"    { ssh $er1Pi "journalctl -u er1-mqtt-log.service -n 200 -f"; return }
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

        # Deploy-only switches (accepted here so you can call: er1 deploy -Mode full -Verify ...)
        [ValidateSet("runtime","full")]
        [string]$Mode = "runtime",
        [switch]$DryRun,
        [switch]$RestartServices,
        [switch]$Verify,

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

            Write-Host "`nDeploy examples:" -ForegroundColor Cyan
            Write-Host "  er1 deploy"
            Write-Host "  er1 deploy -Mode full"
            Write-Host "  er1 deploy -DryRun"
            Write-Host "  er1 deploy -RestartServices -Verify"

            Write-Host "`nLock examples:" -ForegroundColor Cyan
            Write-Host "  er1 lock images open"
            Write-Host "  er1 lock images close"
            Write-Host "  er1 lock all open"
            Write-Host "  er1 lock all close"

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
            Write-Host "  er1 log images 50"
            Write-Host "  er1 log -live"
            Write-Host "  er1 log -errors"
            Write-Host "  er1 log images --save"
            Write-Host ""
            return
        }

        "pi" {
            ssh $er1Pi
            return
        }

        "deploy" {
            Invoke-Er1Deploy -Mode $Mode -DryRun:$DryRun -RestartServices:$RestartServices -Verify:$Verify
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

            if ($cmdArgs[0] -eq "all") {
                $action = $cmdArgs[1]
                if ($action -notin @("open","close")) { throw "Usage: er1 lock all open|close" }
                ssh -t $er1Pi "$er1Cmd lock $action all"
                return
            }

            $id = $cmdArgs[0]
            $action2 = $cmdArgs[1]
            if ($action2 -notin @("open","close")) { throw "Usage: er1 lock <id> open|close" }
            ssh -t $er1Pi "$er1Cmd lock $action2 $id"
            return
        }

        "log" {
            # Minimal (fast) implementation: today/errors/live + simple regex filter + optional --save.
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

            $todayFile = "logs/er1-`$(date +%d.%m.%Y).log"

            if ($live) {
                if ($useAll) {
                    ssh -t $er1Pi "$er1Cmd logs live"
                } else {
                    ssh -t $er1Pi "$er1Cmd logs live | grep -E '$regex'"
                }
                return
            }

            if ($errors) {
                if ($useAll) {
                    ssh $er1Pi "cd ~/er1; grep '""lv"":""ERR""' $todayFile | tail -n $localN"
                } else {
                    ssh $er1Pi "cd ~/er1; grep -E '$regex' $todayFile | grep '""lv"":""ERR""' | tail -n $localN"
                }
                return
            }

            if ($useAll) {
                ssh $er1Pi "cd ~/er1; tail -n $localN $todayFile"
            } else {
                ssh $er1Pi "cd ~/er1; grep -E '$regex' $todayFile | tail -n $localN"
            }
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
    if ($sub -in @("log","ota")) {
        $list = @("*") + $er1Devices
        $list |
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
