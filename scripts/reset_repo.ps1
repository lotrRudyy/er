# Resets the repo in-place (keeps files), removes git history and PlatformIO build outputs.
Write-Host "Resetting repo..." -ForegroundColor Yellow
if (Test-Path .git) { Remove-Item -Recurse -Force .git }
if (Test-Path .pio) { Remove-Item -Recurse -Force .pio }
if (Test-Path .vscode) { Remove-Item -Recurse -Force .vscode }
git init
git add .
git commit -m "init: clean ER1 skeleton"
Write-Host "Done."
