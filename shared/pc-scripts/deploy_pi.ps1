param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("er1", "er2", "er3")]
  [string]$Site
)

# --- Pi hosts (Tailscale hostnames) ---
$piHosts = @{
  "er1" = "er1-pi"
  "er2" = "er2-pi"
  "er3" = "er3-pi"
}

if (-not $piHosts.ContainsKey($Site)) {
  Write-Error "No Pi host configured for site '$Site'"
  exit 1
}

$piHost    = $piHosts[$Site]
$piUser    = "rudyy"
$remoteRoot = "/home/$piUser/$Site"

# --- repo layout: script is in shared/pc-scripts, repo root is two levels up ---
$repoRoot  = (Resolve-Path "$PSScriptRoot\..\..").Path
$siteRoot  = Join-Path $repoRoot $Site        # er1, er2, er3
$piRuntime = Join-Path $siteRoot "pi-runtime" # er1/pi-runtime

if (-not (Test-Path $piRuntime)) {
  Write-Error "Pi runtime folder not found: $piRuntime"
  exit 1
}

Write-Host "[deploy-pi] Site:       $Site"
Write-Host "[deploy-pi] Repo root:  $repoRoot"
Write-Host "[deploy-pi] Runtime:    $piRuntime"
Write-Host "[deploy-pi] Pi target:  ${piUser}@${piHost}:${remoteRoot}"
Write-Host ""

# --- ensure remote directory layout exists ---
$mkDirs = @(
  "$remoteRoot",
  "$remoteRoot/scripts",
  "$remoteRoot/systemd",
  "$remoteRoot/docs",
  "$remoteRoot/config",
  "$remoteRoot/logs"
) -join " "

ssh "${piUser}@${piHost}" "mkdir -p $mkDirs" || {
  Write-Error "[deploy-pi] Failed to create remote dirs on ${piHost}"
  exit 1
}

function Copy-Dir {
  param(
    [string]$Local,
    [string]$Remote
  )
  if (Test-Path $Local) {
    Write-Host "[deploy-pi] sync dir  $Local -> $Remote"
    & scp -r "$Local" "${piUser}@${piHost}:$Remote" | Write-Host
    if ($LASTEXITCODE -ne 0) {
      Write-Error "[deploy-pi] scp failed for $Local -> $Remote"
      exit 1
    }
  } else {
    Write-Host "[deploy-pi] skip dir (missing): $Local"
  }
}

# --- sync scripts, systemd, docs ---
Copy-Dir (Join-Path $piRuntime "scripts")  "$remoteRoot/scripts"
Copy-Dir (Join-Path $piRuntime "systemd") "$remoteRoot/systemd"
Copy-Dir (Join-Path $piRuntime "docs")    "$remoteRoot/docs"

# --- config template: copy example only, never touch local.env ---
$configExample = Join-Path $piRuntime "config/local.env.example"
if (Test-Path $configExample) {
  Write-Host "[deploy-pi] copy config/local.env.example"
  & scp "$configExample" "${piUser}@${piHost}:${remoteRoot}/config/local.env.example" | Write-Host
}

Write-Host ""
Write-Host "[deploy-pi] Done."
Write-Host "  - Real config: ${remoteRoot}/config/local.env"
Write-Host "  - Logs:        ${remoteRoot}/logs/"
