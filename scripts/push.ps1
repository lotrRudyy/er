param([string]$Message = "update from ChatGPT")
git add .
git commit -m $Message
git branch -M main
git remote remove origin 2>$null
# Set your repo URL here (HTTPS). Example:
# git remote add origin https://github.com/<you>/er1.git
Write-Host "Set your origin with: git remote add origin https://github.com/<you>/er1.git" -ForegroundColor Cyan
Write-Host "Then run: git push -u origin main" -ForegroundColor Cyan
