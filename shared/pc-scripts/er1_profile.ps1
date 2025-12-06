# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1Cmd = "/home/rudyy/er1/er1"

# ---- Repo root on PC ----
$erRepoRoot = "$HOME\Documents\Escape Room\er"

function pi {
    ssh $er1Pi
}

# ---- Logs ----
function er1-log-all {
    # live MQTT logs for everything
    ssh -t $er1Pi "$er1Cmd logs live"
}

function er1-log-live {
    # explicit alias for live logs
    ssh -t $er1Pi "$er1Cmd logs live"
}

function er1-log-node {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )
    # filter logs by device name using grep
    ssh -t $er1Pi "$er1Cmd logs grep $dev"
}

# ---- Locks (no pulse, only open/close) ----
function er1-lock {
    param(
        [Parameter(Mandatory = $true)][string]$id
    )
    ssh -t $er1Pi "$er1Cmd lock open $id"
}

function er1-lock-all {
    ssh -t $er1Pi "$er1Cmd lock open_all"
}

function er1-lock-images       { ssh -t $er1Pi "$er1Cmd lock open images" }
function er1-lock-slider       { ssh -t $er1Pi "$er1Cmd lock open slider" }
function er1-lock-knocking     { ssh -t $er1Pi "$er1Cmd lock open knocking" }
function er1-lock-r2           { ssh -t $er1Pi "$er1Cmd lock open r2" }
function er1-lock-r3           { ssh -t $er1Pi "$er1Cmd lock open r3" }

# ---- OTA via ota.ps1 + deviceMap ----
function er1-ota {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )

    $otaScript = Join-Path $erRepoRoot "er1\firmware\ota.ps1"
    if (-not (Test-Path $otaScript)) {
        Write-Error "OTA script not found: $otaScript"
        return
    }

    pwsh -File $otaScript -Target $dev
}

# ---- Direct syslog / MQTT helpers ----
function er1-syslog       { ssh -t $er1Pi "$er1Cmd syslog" }
function er1-mqtt-status  { ssh -t $er1Pi "$er1Cmd status_mqtt" }
function er1-mqtt-restart { ssh -t $er1Pi "$er1Cmd restart_mqtt" }

# ---- Deploy ER1 Pi runtime ----
function er1-deploy {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("er1")]
        [string]$Env,

        [Parameter(Mandatory = $false)]
        [ValidateSet("runtime", "full")]
        [string]$Mode = "runtime"
    )

    $deployScript = Join-Path $erRepoRoot "shared\pc-scripts\deploy_pi.ps1"
    if (-not (Test-Path $deployScript)) {
        Write-Error "Deploy script not found: $deployScript"
        return
    }

    pwsh -File $deployScript -Env $Env -Mode $Mode
}
