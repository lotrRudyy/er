# ---- ER1 Pi over Tailscale ----
$er1Pi = "rudyy@100.108.1.80"

function pi {
    ssh $er1Pi
}

# ---- Logs ----
function er1-log-all {
    ssh -t $er1Pi "er1 logs all"
}

function er1-log-node {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )
    ssh -t $er1Pi "er1 logs node $dev"
}

function er1-log-live {
    ssh -t $er1Pi "er1 logs live"
}

# ---- Locks (no pulse, only open) ----
function er1-lock {
    param(
        [Parameter(Mandatory = $true)][string]$id
    )
    ssh -t $er1Pi "er1 lock open $id"
}

function er1-lock-all {
    ssh -t $er1Pi "er1 lock open_all"
}

function er1-lock-images       { ssh -t $er1Pi "er1 lock open images" }
function er1-lock-slider       { ssh -t $er1Pi "er1 lock open slider" }
function er1-lock-knocking     { ssh -t $er1Pi "er1 lock open knocking" }
function er1-lock-r2   { ssh -t $er1Pi "er1 lock open r2" }
function er1-lock-r3   { ssh -t $er1Pi "er1 lock open r3" }

# ---- OTA (canonical ER1 protocol device names) ----
# maglock_ctrl, images_piano, chess, knocking, candles, star_sky, star_slider, stop_timer
function er1-ota {
    param(
        [Parameter(Mandatory = $true)][string]$dev
    )
    ssh -t $er1Pi "er1 ota $dev"
}

# ---- Direct syslog / MQTT helpers ----
function er1-syslog        { ssh -t $er1Pi "er1 syslog" }
function er1-mqtt-status   { ssh -t $er1Pi "status_mqtt" }
function er1-mqtt-restart  { ssh -t $er1Pi "restart_mqtt" }
