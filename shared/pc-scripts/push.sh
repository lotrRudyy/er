#!/usr/bin/env bash
set -e
msg="${1:-update from ChatGPT}"
git add .
git commit -m "$msg" || true
git branch -M main
echo "Set origin: git remote add origin https://github.com/<you>/er1.git"
echo "Push:       git push -u origin main"
