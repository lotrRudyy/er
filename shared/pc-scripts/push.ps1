param([string]$Message = "update")

git add .

# commit only if there is something to commit
$changes = git status --porcelain
if (-not $changes) {
    Write-Host "[push] No changes to commit." -ForegroundColor Yellow
    exit 0
}

git commit -m $Message

# only push if origin exists
$hasOrigin = git remote | Select-String -SimpleMatch "origin" -Quiet
if ($hasOrigin) {
    git push
} else {
    Write-Host "[push] No 'origin' remote configured. Set it with:" -ForegroundColor Yellow
    Write-Host "       git remote add origin https://github.com/<you>/er.git"
    Write-Host "       git push -u origin main"
}
