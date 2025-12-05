# ---- ER1 Pi over Tailscale ----
$er1Pi  = "rudyy@100.108.1.80"
$er1Cmd = "/home/rudyy/er1/er1"

function pi {
    ssh $er1Pi
}

# ---- Logs ----
function er1-log-all {
    # live MQTT logs for everything
    ssh -t $er1Pi "$er1Cmd logs live"
}

function er1-log-node {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )
    # filter logs by device name using grep
    ssh -t $er1Pi "$er1Cmd logs grep $dev"
}

function er1-log-live {
    # explicit live logs (same as -all, just clearer)
    ssh -t $er1Pi "$er1Cmd logs live"
}


# ---- Locks (no pulse, only open) ----
function er1-lock {
    param(
        [Parameter(Mandatory = $true)][string]$id
    )
    ssh -t $er1Pi "$er1Cmd lock open $id"
}

function er1-lock-all {
    ssh -t $er1Pi "$er1Cmd lock open_all"
}

function er1-lock-images   { ssh -t $er1Pi "$er1Cmd lock open images" }
function er1-lock-slider   { ssh -t $er1Pi "$er1Cmd lock open slider" }
function er1-lock-knocking { ssh -t $er1Pi "$er1Cmd lock open knocking" }
function er1-lock-r2       { ssh -t $er1Pi "$er1Cmd lock open r2" }
function er1-lock-r3       { ssh -t $er1Pi "$er1Cmd lock open r3" }

# ---- OTA ----
function er1-ota {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )
    ssh -t $er1Pi "$er1Cmd ota $dev"
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

    pwsh -File "$HOME\Documents\Escape Room\er\shared\pc-scripts\deploy_pi.ps1" -Env $Env -Mode $Mode
}

