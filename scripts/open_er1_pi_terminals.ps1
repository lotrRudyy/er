<#
.SYNOPSIS
  Launch three PowerShell terminals that connect to er1-pi and label their windows.

.NOTES
  - Adjust $PiUser or $PiHost if the credentials change.
  - Requires OpenSSH client (ssh.exe) to be on PATH.
#>

$PiUser    = "rudyy"
$PiHost    = "100.108.1.80"
$BaseTitle = "er1-pi"

# Keep the launching shell tidy per request.
Clear-Host

$cmd = Get-Command pwsh.exe -ErrorAction SilentlyContinue
$pwshPath = if ($cmd) { $cmd.Source } else { $null }

if (-not $pwshPath) {
    throw "pwsh.exe not found on PATH. Install PowerShell 7 or update the script to point to powershell.exe."
}

1..3 | ForEach-Object {
    $title = "$BaseTitle-$_"
    $perTerminalCommand = "& { `$Host.UI.RawUI.WindowTitle = '$title'; Clear-Host; ssh $PiUser@$PiHost }"

    Start-Process -FilePath $pwshPath -ArgumentList "-NoExit", "-Command", $perTerminalCommand | Out-Null
}
