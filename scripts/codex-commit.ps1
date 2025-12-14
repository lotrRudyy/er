param(
    [string]$Message = ""
)

Set-StrictMode -Version Latest

Write-Host "[codex-commit] This placeholder lives in scripts/." -ForegroundColor Cyan
Write-Host "[codex-commit] Implement your auto-commit workflow here or adjust .vscode/tasks.json to your preferred helper." -ForegroundColor Yellow

if ($Message) {
    Write-Host "[codex-commit] Ignoring message: '$Message'" -ForegroundColor DarkGray
}
