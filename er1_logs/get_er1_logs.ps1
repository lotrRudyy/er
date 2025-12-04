# === CONFIG ===
$PiUser  = "rudyy"
$PiHost  = "er1-pi"
$RemoteCmd = "~/er1_mqtt_last2min.sh"
$LocalOut = "C:\Users\Rudy\Documents\er\er1_logs\er1_last2min.log"

# === FETCH FROM PI ===
try {
    $result = ssh "$PiUser@$PiHost" $RemoteCmd
}
catch {
    Write-Host "ERROR: Could not SSH to the Pi."
    exit 1
}

# === SAVE TO FILE ===
$result | Out-File -FilePath $LocalOut -Encoding UTF8

Write-Host "Wrote last 2 minutes of ER1 logs to:"
Write-Host $LocalOut
