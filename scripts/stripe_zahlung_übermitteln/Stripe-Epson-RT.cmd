@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stripe-Epson-RT.ps1"
if errorlevel 1 (
  echo.
  echo Das Tool wurde mit einem Fehler beendet.
  pause
)
