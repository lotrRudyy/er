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
    "log"         = "Show logs (tail, errors, live)"
    "ota"         = "Upload firmware to device"
    "deploy"      = "Deploy Pi runtime"
    "lock"        = "Open a maglock"
    "lock-all"    = "Open ALL maglocks"
    "syslog"      = "Show ER1 Pi syslog"
    "mqtt-status" = "Show MQTT runtime status"
    "mqtt-restart"= "Restart MQTT runtime"
    "commit"      = "Commit + push repo"
    "pi"          = "SSH into Pi"
    "help"        = "List commands"
}

# ---- Main dispatcher ----
function er1 {
    param(
        [Parameter(Position=0)]
        [string]$cmd,

        [Parameter(Position=1)]
        [string]$arg1,

        [Parameter(Position=2)]
        [string]$arg2,

        [switch]$live,
        [switch]$errors,
        [int]$n = 200
    )

    switch ($cmd) {

        "help" {
            Write-Host "`nAvailable ER1 commands:`n" -ForegroundColor Cyan
            foreach ($k in $er1Commands.Keys) {
                "{0,-14} {1}" -f $k, $er1Commands[$k]
            }
            return
        }

        "pi" {
            ssh $er1Pi
            return
        }

        "log" {
            $dev = if ($arg1) { $arg1 } else { "*" }

            if ($live) {
                if ($dev -eq "*") { ssh -t $er1Pi "$er1Cmd logs live" }
                else { ssh -t $er1Pi "$er1Cmd logs grep $dev" }
                return
            }

            if ($errors) {
                if ($dev -eq "*") { ssh -t $er1Pi "$er1Cmd logs live" | Select-String '"lv":"ERR"' }
                else { ssh -t $er1Pi "$er1Cmd logs grep $dev" | Select-String '"lv":"ERR"' }
                return
            }

            if ($dev -eq "*") {
                $remoteCmd = "cd ~/er1; tail -n $n logs/er1-`$(date +%d.%m.%Y).log"
            } else {
                $remoteCmd = "cd ~/er1; grep $dev logs/er1-`$(date +%d.%m.%Y).log | tail -n $n"
            }

            ssh $er1Pi $remoteCmd
            return
        }

        "ota" {
            if (-not $arg1) { Write-Error "Usage: er1 ota <device>"; return }
            $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"
            pwsh -File $otaScript -Target $arg1
            return
        }

        "deploy" {
            $deployScript = Join-Path $erRepoRoot "shared\pc-scripts\deploy_pi.ps1"
            pwsh -File $deployScript -Env "er1" -Mode "runtime"
            return
        }

        "lock" {
            if (-not $arg1) { Write-Error "Usage: er1 lock <id>"; return }
            ssh -t $er1Pi "$er1Cmd lock open $arg1"
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
            param([string]$Message = "update")
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
        $list = @("*") + $er1Devices
        return $list |
            Where-Object { $_ -like "$wordToComplete*" } |
            ForEach-Object {
                [System.Management.Automation.CompletionResult]::new($_,$_, 'ParameterValue', $_)
            }
    }
}
