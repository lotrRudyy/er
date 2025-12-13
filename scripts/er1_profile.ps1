# === ER1 Helper Functions (er1_profile.ps1) ===

# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1Cmd = "/home/rudyy/er1/er1"

# ---- Repo root for BOTH PC and Laptop ----
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

# ---- Common lists ----
$er1Devices = @(
    "maglock_ctrl",
    "images_piano",
    "chess",
    "knocking",
    "candles",
    "star_sky",
    "star_slider",
    "stop_timer"
)

$er1Commands = @{
    "help"         = "Show this help"
    "log"          = "Tail today's logfile (filters/live/errors, optional --save)"
    "ota"          = "Upload firmware to a device via ota.ps1"
    "deploy"       = "Deploy ER1 Pi runtime from this repo"
    "lock"         = "Open a single maglock by ID"
    "lock-all"     = "Open ALL maglocks"
    "syslog"       = "Show ER1 Pi syslog (journalctl wrapper)"
    "mqtt-status"  = "Show MQTT runtime status"
    "mqtt-restart" = "Restart MQTT runtime"
    "push"         = "Stage, commit, and push from the ER repo root"
    "commit"       = "Legacy alias for 'er1 push'"
    "pi"           = "SSH into the ER1 Pi"
}

<#
Next candidates for consolidation:
- er1 status        (repo + service health in one go)
- er1 doctor        (Pi + MQTT diagnostics bundle)
- er1 mqtt          (combined status/log/restart helper)
- er1 nodered       (runtime restart + log tail)
- er1 backup        (Pi runtime backup pull)
- er1 update-fw     (batch OTA helper)
#>

function Invoke-Er1Sync {
    param(
        [string]$Source,
        [string]$Dest,
        [string[]]$ExtraArgs,
        [string]$Prefix = "[er1 deploy]"
    )

    $rsyncCmd = Get-Command rsync -ErrorAction SilentlyContinue

    if ($rsyncCmd) {
        $rsyncArgs = @("-avz", "--delete") + ($ExtraArgs | Where-Object { $_ }) + @($Source, $Dest)
        Write-Host "$Prefix rsync $($rsyncArgs -join ' ')"
        & rsync @rsyncArgs
        if ($LASTEXITCODE -ne 0) {
            throw "rsync failed with exit code $LASTEXITCODE"
        }
    }
    else {
        Write-Host "$Prefix rsync not found, falling back to scp -r" -ForegroundColor Yellow
        Write-Host "$Prefix scp -r `"$Source`" `"$Dest`""
        & scp -r "$Source" "$Dest"
        if ($LASTEXITCODE -ne 0) {
            throw "scp failed with exit code $LASTEXITCODE"
        }
    }
}

function Invoke-Er1Deploy {
    param(
        [ValidateSet("runtime", "full")]
        [string]$Mode = "runtime"
    )

    $prefix       = "[er1 deploy]"
    $remoteRoot   = Split-Path -Parent $er1Cmd
    $envRoot      = Join-Path $erRepoRoot "er1"
    $runtimeRoot  = Join-Path $envRoot "pi-runtime"

    if (-not (Test-Path $runtimeRoot)) {
        Write-Error "$prefix Pi runtime folder not found: $runtimeRoot"
        $global:LASTEXITCODE = 1
        return
    }

    Write-Host "$prefix Target Pi:   $er1Pi"
    Write-Host "$prefix Remote root: $remoteRoot"
    Write-Host "$prefix Mode:        $Mode"
    Write-Host "$prefix Local root:  $runtimeRoot"

    try {
        ssh $er1Pi "mkdir -p '$remoteRoot'"
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to ensure remote path (exit $LASTEXITCODE)."
        }

        if ($Mode -eq "full") {
            Invoke-Er1Sync -Source ("{0}/" -f $runtimeRoot) `
                           -Dest ("{0}:{1}/" -f $er1Pi, $remoteRoot) `
                           -ExtraArgs @(
                               "--exclude=.pio",
                               "--exclude=.sconsign*",
                               "--exclude=.vscode"
                           ) `
                           -Prefix $prefix
        }
        else {
            $cliWrapper = Join-Path $envRoot "er1"
            if (Test-Path $cliWrapper) {
                Invoke-Er1Sync -Source $cliWrapper `
                               -Dest ("{0}:{1}/" -f $er1Pi, $remoteRoot) `
                               -ExtraArgs @() `
                               -Prefix $prefix
            }

            $runtimeItems = @(
                @{ Path = (Join-Path $runtimeRoot "scripts"); Remote = "/scripts/"; Extra = @() },
                @{ Path = (Join-Path $runtimeRoot "config");  Remote = "/config/";  Extra = @("--exclude=local.env") },
                @{ Path = (Join-Path $runtimeRoot "systemd"); Remote = "/systemd/"; Extra = @() },
                @{ Path = (Join-Path $runtimeRoot "docs");    Remote = "/docs/";    Extra = @() }
            )

            foreach ($item in $runtimeItems) {
                if (Test-Path $item.Path) {
                    Invoke-Er1Sync -Source $item.Path `
                                   -Dest ("{0}:{1}{2}" -f $er1Pi, $remoteRoot, $item.Remote) `
                                   -ExtraArgs $item.Extra `
                                   -Prefix $prefix
                }
            }
        }

        ssh $er1Pi @"
set -e
cd '$remoteRoot'
chmod +x ./er1 2>/dev/null || true
if [ -d scripts ]; then
  chmod +x scripts/*.sh 2>/dev/null || true
  chmod +x scripts/ota 2>/dev/null || true
fi
"@
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to refresh remote permissions (exit $LASTEXITCODE)."
        }

        Write-Host "$prefix Deploy complete." -ForegroundColor Green
        Write-Host "$prefix Next: review, commit, then 'er1 push'." -ForegroundColor Yellow
        $global:LASTEXITCODE = 0
    }
    catch {
        Write-Error "$prefix $($_.Exception.Message)"
        $global:LASTEXITCODE = 1
    }
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
            Write-Error "$prefix Repo root '$erRepoRoot' is not a git repository."
            $global:LASTEXITCODE = 1
            return
        }

        $branch = (git rev-parse --abbrev-ref HEAD).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Error "$prefix Unable to resolve current branch."
            $global:LASTEXITCODE = $LASTEXITCODE
            return
        }

        $remotes = git remote
        $originExists = $false
        foreach ($remote in $remotes) {
            if ($remote.Trim() -eq "origin") {
                $originExists = $true
                break
            }
        }

        $upstreamName = git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null
        $hasUpstream = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($upstreamName))
        if (-not $hasUpstream) {
            $global:LASTEXITCODE = 0
        }

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
                if ($LASTEXITCODE -ne 0) {
                    Write-Error "$prefix Failed to set upstream."
                    $global:LASTEXITCODE = $LASTEXITCODE
                    return
                }
                $hasUpstream = $true
                $upstreamName = $targetUpstream
            }
            else {
                Write-Host "$prefix No 'origin' remote configured. Set it with:" -ForegroundColor Yellow
                Write-Host "       git remote add origin https://github.com/<you>/er.git"
                Write-Host "       git push -u origin $branch"
            }
        }

        git add .
        if ($LASTEXITCODE -ne 0) {
            Write-Error "$prefix git add failed."
            $global:LASTEXITCODE = $LASTEXITCODE
            return
        }

        $changes = git status --porcelain
        if (-not $changes) {
            Write-Host "$prefix No changes to commit." -ForegroundColor Yellow
            $global:LASTEXITCODE = 0
            return
        }

        git commit -m $messageToUse
        if ($LASTEXITCODE -ne 0) {
            Write-Error "$prefix git commit failed."
            $global:LASTEXITCODE = $LASTEXITCODE
            return
        }

        if (-not $originExists -and -not $hasUpstream) {
            Write-Host "$prefix Changes committed locally; configure 'origin' before pushing." -ForegroundColor Yellow
            $global:LASTEXITCODE = 0
            return
        }

        git push
        $pushExit = $LASTEXITCODE
        if ($pushExit -ne 0) {
            Write-Error "$prefix git push failed with exit code $pushExit."
        }
        else {
            Write-Host "$prefix Push complete." -ForegroundColor Green
        }
        $global:LASTEXITCODE = $pushExit
    }
    finally {
        Pop-Location
    }
}

# Helper to expand special log aliases into regex fragments
function Expand-Er1LogPattern {
    param([string]$pattern)

    switch ($pattern) {
        # All maglock controller + knocking lock topics in one go
        "maglock" { return "room0/maglock_ctrl|ctrl/lock/knocking" }
        default   { return $pattern }
    }
}

function Get-Er1LogSavePath {
    param(
        [ValidateSet("today","errors","live")]
        [string]$Mode,
        [string[]]$Patterns,
        [bool]$UseAll
    )

    $root = $script:ER1_REPO
    if (-not $root) { throw "ER1_REPO not set in er1_profile.ps1" }

    $logs = Join-Path $root "logs"
    New-Item -ItemType Directory -Force -Path $logs | Out-Null

    $stamp    = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $modeSlug = switch ($Mode) {
        "live"   { "pi_live" }
        "errors" { "pi_errors" }
        default  { "pi_today" }
    }

    $filterSlug = $null
    if (-not $UseAll -and $Patterns -and $Patterns.Count -gt 0) {
        $filterSlug = ($Patterns -join "_")
        $filterSlug = ($filterSlug -replace '[\\\/:*?"<>|\s]+', "_").Trim("_")
    }

    $name = "${stamp}__${modeSlug}"
    if ($filterSlug) {
        $name = "${name}__${filterSlug}"
    }

    return Join-Path $logs "${name}.log"
}

function Invoke-Er1LogCommand {
    param(
        [string]$RemoteCommand,
        [switch]$UseTty,
        [switch]$Save,
        [ValidateSet("today","errors","live")]
        [string]$SaveMode = "today",
        [string[]]$Patterns,
        [bool]$UseAll
    )

    $sshArgs = @()
    if ($UseTty) { $sshArgs += "-t" }
    $sshArgs += $er1Pi
    $sshArgs += $RemoteCommand

    if (-not $Save) {
        & ssh @sshArgs
        return
    }

    $target = Get-Er1LogSavePath -Mode $SaveMode -Patterns $Patterns -UseAll:$UseAll
    Write-Host "[er1 log] Saving to $target" -ForegroundColor Cyan

    $hasTs = (Get-Command ts -ErrorAction SilentlyContinue)
    if ($hasTs) {
        & ssh @sshArgs | ts | Tee-Object -FilePath $target
    }
    else {
        & ssh @sshArgs |
            ForEach-Object { "[{0:dd.MM.yyyy HH:mm:ss.fff}] $_" -f (Get-Date) } |
            Tee-Object -FilePath $target
    }
}

# ---- Main dispatcher ----
function er1 {
    param(
        [Parameter(Position=0)]
        [string]$cmd,

        # Everything after cmd comes in here; we parse it per subcommand
        [Parameter(Position=1, ValueFromRemainingArguments = $true)]
        [string[]]$cmdArgs,

        [switch]$live,
        [switch]$errors,
        [int]$n = 200
    )

    switch ($cmd) {

        "help" {
            Write-Host "`nER1 helper – commands:`n" -ForegroundColor Cyan

            foreach ($k in $er1Commands.Keys) {
                "{0,-14} {1}" -f $k, $er1Commands[$k]
            }

            Write-Host "`nLog command examples:" -ForegroundColor Cyan
            Write-Host "  er1 log                          # tail today's log (all devices, $n lines)"
            Write-Host "  er1 log -n 500                   # tail 500 lines of today's log"
            Write-Host "  er1 log images_piano             # filter today's log for one device"
            Write-Host "  er1 log images_piano -n 50       # same, but only last 50 lines"
            Write-Host "  er1 log knocking maglock 400     # knocking + maglock_ctrl+knocking-lock, 400 lines"
            Write-Host "  er1 log -live                    # live stream from Pi (all devices)"
            Write-Host "  er1 log images_piano -live       # live filtered stream for one device"
            Write-Host "  er1 log -errors                  # only error lines from today (all devices)"
            Write-Host "  er1 log images_piano -errors     # only error lines for one device"
            Write-Host "  er1 log images_piano --save      # tail + save output to logs/"
            Write-Host "  er1 log -live --save             # live stream while saving a copy"
            Write-Host ""
            Write-Host "Other examples:" -ForegroundColor Cyan
            Write-Host "  er1 ota images_piano             # upload firmware for images_piano"
            Write-Host "  er1 deploy                       # deploy ER1 runtime to Pi"
            Write-Host "  er1 lock images                  # open images maglock"
            Write-Host "  er1 lock-all                     # open ALL maglocks"
            Write-Host "  er1 mqtt-status                  # check MQTT runtime"
            Write-Host "  er1 mqtt-restart                 # restart MQTT runtime"
            Write-Host "  er1 push ""tweak logs""             # git add/commit/push from ER repo"
            Write-Host "  er1 commit ""tweak logs""           # legacy alias for 'er1 push'"
            Write-Host ""
            Write-Host "Tip: use TAB after 'er1 ' to autocomplete commands,"
            Write-Host "     and after 'er1 log/ota/lock ' to autocomplete devices."
            return
        }

        "pi" {
            ssh $er1Pi
            return
        }

        "log" {
            $argsNoSave = @()
            $saveRequested = $false
            if ($cmdArgs) {
                foreach ($arg in $cmdArgs) {
                    if ($arg -and $arg.ToLowerInvariant() -eq "--save") {
                        $saveRequested = $true
                    }
                    else {
                        $argsNoSave += $arg
                    }
                }
            }

            $patterns = @()
            $localN   = $n

            if ($argsNoSave.Count -gt 0) {
                $last = $argsNoSave[-1]
                $intRef = 0
                if ([int]::TryParse($last, [ref]$intRef)) {
                    $localN = $intRef
                    if ($argsNoSave.Count -gt 1) {
                        $patterns = $argsNoSave[0..($argsNoSave.Count - 2)]
                    }
                }
                else {
                    $patterns = $argsNoSave
                }
            }

            if ($patterns.Count -eq 0) {
                $patterns = @("*")
            }

            $expandedPatterns = @()
            foreach ($p in $patterns) {
                if ($p -eq "*") {
                    $expandedPatterns = @("*")
                    break
                }
                $expandedPatterns += (Expand-Er1LogPattern $p)
            }

            $useAll = ($expandedPatterns.Count -eq 1 -and $expandedPatterns[0] -eq "*")
            $regex  = $null
            if (-not $useAll) {
                $regex = $expandedPatterns -join "|"
            }

            $saveMode = if ($errors) { "errors" } else { "today" }

            if ($live) {
                $saveMode = "live"
                if ($useAll) {
                    Invoke-Er1LogCommand -RemoteCommand "$er1Cmd logs live" -UseTty -Save:$saveRequested -SaveMode $saveMode -Patterns $patterns -UseAll:$useAll
                }
                else {
                    Invoke-Er1LogCommand -RemoteCommand "$er1Cmd logs live | grep -E '$regex'" -UseTty -Save:$saveRequested -SaveMode $saveMode -Patterns $patterns -UseAll:$useAll
                }
                return
            }

            $todayFile = "logs/er1-`$(date +%d.%m.%Y).log"

            if ($errors) {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; grep '""lv"":""ERR""' $todayFile | tail -n $localN"
                }
                else {
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | grep '""lv"":""ERR""' | tail -n $localN"
                }
            }
            else {
                if ($useAll) {
                    $remoteCmd = "cd ~/er1; tail -n $localN $todayFile"
                }
                else {
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | tail -n $localN"
                }
            }

            Invoke-Er1LogCommand -RemoteCommand $remoteCmd -Save:$saveRequested -SaveMode $saveMode -Patterns $patterns -UseAll:$useAll
            return
        }

        "ota" {
            $target = if ($cmdArgs.Count -ge 1) { $cmdArgs[0] } else { $null }
            if (-not $target) { Write-Error "Usage: er1 ota <device>"; return }
            $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"
            pwsh -File $otaScript -Target $target
            return
        }

        "deploy" {
            $mode = "runtime"
            if ($cmdArgs -and $cmdArgs.Count -gt 0) {
                $candidate = $cmdArgs[0].ToLowerInvariant()
                if ($candidate -in @("runtime", "full")) {
                    $mode = $candidate
                }
                else {
                    Write-Error "Usage: er1 deploy [runtime|full]"
                    return
                }
            }

            Invoke-Er1Deploy -Mode $mode
            return
        }

        "lock" {
            $id = if ($cmdArgs.Count -ge 1) { $cmdArgs[0] } else { $null }
            if (-not $id) { Write-Error "Usage: er1 lock <id>"; return }
            ssh -t $er1Pi "$er1Cmd lock open $id"
            return
        }

        "lock-all" {
            ssh -t $er1Pi "$er1Cmd lock open_all"
            return
        }

        "syslog" {
            ssh -t $er1Pi "$er1Cmd syslog"
            return
        }

        "mqtt-status" {
            ssh -t $er1Pi "$er1Cmd status_mqtt"
            return
        }

        "mqtt-restart" {
            ssh -t $er1Pi "$er1Cmd restart_mqtt"
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
            Write-Host "Unknown command. Use: er1 help" -ForegroundColor Red
            return
        }
    }
}

# ---- Autocomplete for first argument (command) ----
Register-ArgumentCompleter -CommandName er1 -ParameterName cmd -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete)
    foreach ($k in $er1Commands.Keys) {
        if ($k -like "$wordToComplete*") {
            [System.Management.Automation.CompletionResult]::new($k,$k,'ParameterValue',$er1Commands[$k])
        }
    }
}

# ---- Autocomplete for device (2nd arg only when cmd=log/ota/lock) ----
Register-ArgumentCompleter -CommandName er1 -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst)

    $tokens = $commandAst.CommandElements
    if ($tokens.Count -lt 2) { return }

    $cmd = $tokens[1].Value

    if ($cmd -in @("log","ota","lock")) {
        $list = @("*") + $er1Devices + @("maglock")
        return $list |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object {
                [System.Management.Automation.CompletionResult]::new($_,$_, 'ParameterValue', $_)
            }
    }
}
