param(
    [Parameter(Mandatory=$true)]
    [string]$Dev,

    [string]$RootDir
)

if (-not $RootDir) {
    $RootDir = Split-Path -Parent $PSCommandPath
}

function Get-FirmwareMainPath {
    param([string]$DeviceName)

    $p = Join-Path $RootDir ("src/{0}_main.cpp" -f $DeviceName)
    if (Test-Path $p) { return $p }

    $p2 = Join-Path $RootDir ("src/{0}.cpp" -f $DeviceName)
    if (Test-Path $p2) { return $p2 }

    throw "Unable to locate firmware main for '$DeviceName'. Checked: $p and $p2"
}

function Increment-VersionString {
    param([string]$Version)

    if ($Version -match '^\d+$') {
        return ([string](([int]$Version) + 1))
    }

    if ($Version -match '^\d+(\.\d+)+$') {
        $parts = $Version -split '\.'
        $last = [int]$parts[-1]
        $parts[-1] = [string]($last + 1)
        return ($parts -join '.')
    }

    throw "Unsupported FW_VERSION format '$Version'. Use either integer (e.g. 1) or dotted numeric version (e.g. 1.2.3)."
}

$mainPath = Get-FirmwareMainPath -DeviceName $Dev
$content = Get-Content -Path $mainPath -Raw

$pattern = 'FW_VERSION\s*=\s*"([^"]+)"'
$match = [regex]::Match($content, $pattern)

if (-not $match.Success) {
    throw "Unable to locate FW_VERSION in $mainPath"
}

$oldVersion = $match.Groups[1].Value
$newVersion = Increment-VersionString -Version $oldVersion

$newContent = [regex]::Replace(
    $content,
    $pattern,
    ('FW_VERSION = "{0}"' -f $newVersion),
    1
)

Set-Content -Path $mainPath -Value $newContent -NoNewline

Write-Host ("FW_VERSION updated for {0}: {1} -> {2}" -f $Dev, $oldVersion, $newVersion)
