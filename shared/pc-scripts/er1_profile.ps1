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
    "log"          = "Show today’s logfile (tail, filter by device, live/errors)"
    "logs"         = "Raw passthrough to 'er1 logs' on the Pi"
    "ota"          = "Upload firmware to a device via ota.ps1"
    "deploy"       = "Deploy ER1 Pi runtime from this repo"
    "lock"         = "Open a single maglock by ID"
    "lock-all"     = "Open ALL maglocks"
    "syslog"       = "Show ER1 Pi syslog (journalctl wrapper)"
    "mqtt-status"  = "Show MQTT runtime status"
    "mqtt-restart" = "Restart MQTT runtime"
    "commit"       = "Git add/commit/push from the ER repo root"
    "pi"           = "SSH into the ER1 Pi"
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

# ---- Main dispatcher ----
function er1 {
    param(
        [Parameter(Position=0)]
        [string]$cmd,

        # Everything after cmd comes in here; we parse it per subcommand
        [Parameter(Position=1, ValueFromRemainingArguments = $true)]
        [string[]]$args,

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
            Write-Host "  er1 log                          # tail today’s log (all devices, $n lines)"
            Write-Host "  er1 log -n 500                   # tail 500 lines of today’s log"
            Write-Host "  er1 log images_piano             # filter today’s log for one device"
            Write-Host "  er1 log images_piano -n 50       # same, but only last 50 lines"
            Write-Host "  er1 log knocking maglock 400     # knocking + maglock_ctrl+knocking-lock, 400 lines"
            Write-Host "  er1 log -live                    # live stream from Pi (all devices)"
            Write-Host "  er1 log images_piano -live       # live filtered stream for one device"
            Write-Host "  er1 log -errors                  # only error lines from today (all devices)"
            Write-Host "  er1 log images_piano -errors     # only error lines for one device"
            Write-Host ""
            Write-Host "Advanced logs (Pi CLI passthrough):" -ForegroundColor Cyan
            Write-Host "  er1 logs help                    # show full 'er1 logs' help on the Pi"
            Write-Host "  er1 logs today                   # whatever 'er1 logs today' does on Pi"
            Write-Host "  er1 logs date 06.12.2025         # example date-based usage (if supported)"
            Write-Host ""
            Write-Host "Other examples:" -ForegroundColor Cyan
            Write-Host "  er1 ota images_piano             # upload firmware for images_piano"
            Write-Host "  er1 deploy                       # deploy ER1 runtime to Pi"
            Write-Host "  er1 lock images                  # open images maglock"
            Write-Host "  er1 lock-all                     # open ALL maglocks"
            Write-Host "  er1 mqtt-status                  # check MQTT runtime"
            Write-Host "  er1 mqtt-restart                 # restart MQTT runtime"
            Write-Host "  er1 commit ""tweak logs""           # git add/commit/push from ER repo"
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
            # Parse args:
            # - any number of string filters (patterns)
            # - optional last raw number = override for -n
            # Examples:
            #   er1 log                            -> all devices, default -n
            #   er1 log images_piano               -> filter by 'images_piano'
            #   er1 log knocking maglock 400       -> filters 'knocking' + 'maglock', n=400
            #   er1 log -live                      -> no patterns, just live tail
            #   er1 log knocking -live             -> live filtered
            #   er1 log knocking -errors -n 300    -> errors only, numeric via named -n

            $patterns = @()
            $localN   = $n

            if ($args -and $args.Count -gt 0) {
                # Check if last arg is a bare integer -> treat as n override
                $last = $args[-1]
                $intRef = 0
                if ([int]::TryParse($last, [ref]$intRef)) {
                    $localN = $intRef
                    if ($args.Count -gt 1) {
                        $patterns = $args[0..($args.Count - 2)]
                    }
                }
                else {
                    $patterns = $args
                }
            }

            if ($patterns.Count -eq 0) {
                # No filters means: all devices
                $patterns = @("*")
            }

            # Expand special aliases like "maglock"
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

            # LIVE mode
            if ($live) {
                if ($useAll) {
                    ssh -t $er1Pi "$er1Cmd logs live"
                }
                else {
                    ssh -t $er1Pi "$er1Cmd logs live | grep -E '$regex'"
                }
                return
            }

            # Today’s logfile path on Pi
            $todayFile = "logs/er1-`$(date +%d.%m.%Y).log"

            if ($errors) {
                if ($useAll) {
                    # All devices, only ERR
                    $remoteCmd = "cd ~/er1; grep '""lv"":""ERR""' $todayFile | tail -n $localN"
                }
                else {
                    # Filter by regex AND only ERR
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | grep '""lv"":""ERR""' | tail -n $localN"
                }
            }
            else {
                if ($useAll) {
                    # All devices
                    $remoteCmd = "cd ~/er1; tail -n $localN $todayFile"
                }
                else {
                    # Filter by regex
                    $remoteCmd = "cd ~/er1; grep -E '$regex' $todayFile | tail -n $localN"
                }
            }

            ssh $er1Pi $remoteCmd
            return
        }

        "logs" {
            # Raw passthrough to the Pi's 'er1 logs' CLI for anything not covered by 'er1 log'
            # Examples:
            #   er1 logs help
            #   er1 logs today
            #   er1 logs date 06.12.2025
            $joined = $args -join " "
            if ([string]::IsNullOrWhiteSpace($joined)) {
                ssh -t $er1Pi "$er1Cmd logs help"
            }
            else {
                ssh -t $er1Pi "$er1Cmd logs $joined"
            }
            return
        }

        "ota" {
            $target = if ($args.Count -ge 1) { $args[0] } else { $null }
            if (-not $target) { Write-Error "Usage: er1 ota <device>"; return }
            $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"
            pwsh -File $otaScript -Target $target
            return
        }

        "deploy" {
            $deployScript = Join-Path $erRepoRoot "shared\pc-scripts\deploy_pi.ps1"
            pwsh -File $deployScript -Env "er1" -Mode "runtime"
            return
        }

        "lock" {
            $id = if ($args.Count -ge 1) { $args[0] } else { $null }
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

        "commit" {
            # Use all remaining args as the commit message; default to "update" if empty.
            $Message = if ($args -and $args.Count -gt 0) { ($args -join " ") } else { "update" }

            Push-Location $erRepoRoot
            git add .
            git commit -m $Message
            git push
            Pop-Location
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
